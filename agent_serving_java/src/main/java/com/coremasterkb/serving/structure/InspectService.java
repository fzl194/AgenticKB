package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.evidence.EvidenceRefCodec;
import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.mapper.result.EvidenceDocumentRow;
import com.coremasterkb.serving.mapper.result.StructureNodeRow;
import com.coremasterkb.serving.mapper.result.TableAssetRow;
import com.coremasterkb.serving.mapper.result.UnitV2Row;
import com.coremasterkb.serving.operator.mapper.EvidenceSourceV2Mapper;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;
import com.coremasterkb.serving.operator.operators.output.TargetRefFormat;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * {@code inspect_knowledge}——结构能力渐进披露（批次8 R7，25 号 §8.3）。
 *
 * <p>输入 document_ref/structure_ref/evidence ref 之一，返回 capabilities
 * （can_navigate/can_query_structured/can_aggregate/can_read_document）、允许关系、
 * 表格资产清单（st_ ref + readiness）与字段 display name/type/operations。能力来自
 * v2 结构表事实（nodes/edges/typed assets/cells）——不吐内部 DDL、不吐 schema 全量到
 * search 响应。</p>
 */
@Service
public class InspectService {

    /** 文档/章节级 inspect 的资产清单与聚合判定扫描上限。 */
    static final int ASSET_LIST_CAP = 50;
    static final int AGGREGATE_SCAN_ASSET_CAP = 10;

    private final StructureRefService refService;
    private final StructureToolMapper toolMapper;
    private final EvidenceSourceV2Mapper sourceMapper;
    private final StructuredQueryService queryService;
    private final EvidenceRefCodec codec;

    public InspectService(StructureRefService refService,
                          StructureToolMapper toolMapper,
                          EvidenceSourceV2Mapper sourceMapper,
                          StructuredQueryService queryService,
                          EvidenceRefCodec codec) {
        this.refService = refService;
        this.toolMapper = toolMapper;
        this.sourceMapper = sourceMapper;
        this.queryService = queryService;
        this.codec = codec;
    }

    /** 表格资产摘要（st_ ref 可直接喂 query_structured_asset）。 */
    public record AssetSummary(
            String ref,
            String asset_type,
            String readiness,
            Integer row_count,
            List<String> columns,
            Boolean can_aggregate,
            List<StructuredQueryService.FieldSchema> schema) {}

    /** inspect 响应。 */
    public record InspectResult(
            String ref,
            String ref_kind,
            String node_type,
            String evidence_type,
            Map<String, Object> source,
            Map<String, Boolean> capabilities,
            List<String> relations,
            List<AssetSummary> assets) {}

    public InspectResult inspect(String ref, String domain, List<String> kbIds, String username) {
        EvidenceRefResolver.ResolvedRef resolved = refService.resolve(ref, domain, kbIds, username);
        String snapshotId = resolved.snapshotId();

        return switch (resolved.kind()) {
            case DOCUMENT -> inspectDocument(ref, snapshotId, resolved.internalRef());
            case STRUCTURE -> inspectStructure(ref, snapshotId, resolved.internalRef());
            case EVIDENCE -> inspectEvidence(ref, snapshotId, resolved.internalRef());
        };
    }

    // ------------------------------------------------------------------ kinds

    /**
     * 27号审查修复 B：mining 冻结的 readiness 事实（asset_snapshot_readiness）。
     * 无行/解析失败返回 null——调用方回落现场计数（legacy 快照兼容）。
     */
    private Map<String, Boolean> frozenReadiness(String snapshotId) {
        String json = toolMapper.selectFrozenReadinessJson(snapshotId);
        if (json == null || json.isBlank()) {
            return null;
        }
        try {
            com.fasterxml.jackson.databind.JsonNode node = CODEC_MAPPER.readTree(json);
            Map<String, Boolean> out = new LinkedHashMap<>();
            out.put("structure_navigate_ready", node.path("structure_navigate_ready").asBoolean(false));
            out.put("structured_query_ready", node.path("structured_query_ready").asBoolean(false));
            out.put("aggregate_ready", node.path("aggregate_ready").asBoolean(false));
            return out;
        } catch (Exception e) {
            return null;
        }
    }

    private static final com.fasterxml.jackson.databind.ObjectMapper CODEC_MAPPER =
            new com.fasterxml.jackson.databind.ObjectMapper();

    private InspectResult inspectDocument(String ref, String snapshotId, String documentRef) {
        boolean navigateReady = navigateReady(snapshotId);
        List<TableAssetRow> assets = toolMapper.selectSnapshotTableAssets(snapshotId, ASSET_LIST_CAP);
        List<AssetSummary> summaries = assetSummaries(snapshotId, assets, false);
        boolean anyReady = summaries.stream().anyMatch(a -> "ready".equals(a.readiness()));
        // 27号审查修复 B：能力披露优先读 mining 冻结 readiness（与发布门禁
        // 同源）；缺行（legacy 快照）回落现场计数。
        Map<String, Boolean> frozen = frozenReadiness(snapshotId);
        if (frozen != null) {
            navigateReady = frozen.get("structure_navigate_ready");
            anyReady = frozen.get("structured_query_ready");
        }

        Map<String, Boolean> caps = new LinkedHashMap<>();
        caps.put("can_read_document", true);
        caps.put("can_navigate", navigateReady);
        caps.put("can_query_structured", anyReady);
        caps.put("can_aggregate", frozen != null
                ? frozen.get("aggregate_ready") : anyAggregate(snapshotId, assets));

        return new InspectResult(ref, "document_ref", "document", null,
                sourceProjection(snapshotId, documentRef), caps,
                navigateReady ? List.of("children", "descendants") : List.of(), summaries);
    }

    private InspectResult inspectStructure(String ref, String snapshotId, String internalRef) {
        TableAssetRow asset = toolMapper.selectTableAssetByAssetRef(snapshotId, internalRef);
        if (asset != null) {
            return inspectAsset(ref, snapshotId, asset);
        }
        StructureNodeRow node = toolMapper.selectNode(snapshotId, internalRef);
        if (node == null) {
            throw StructureToolException.invalidRef("目标结构节点不存在（ref 可能已失效）");
        }
        boolean navigateReady = navigateReady(snapshotId);
        List<TableAssetRow> assets = toolMapper.selectSnapshotTableAssets(snapshotId, ASSET_LIST_CAP);
        List<AssetSummary> summaries = assetSummaries(snapshotId, assets, false);
        boolean anyReady = summaries.stream().anyMatch(a -> "ready".equals(a.readiness()));
        Map<String, Boolean> frozen = frozenReadiness(snapshotId);
        if (frozen != null) {
            navigateReady = frozen.get("structure_navigate_ready");
            anyReady = frozen.get("structured_query_ready");
        }

        Map<String, Boolean> caps = new LinkedHashMap<>();
        caps.put("can_read_document", true);
        caps.put("can_navigate", navigateReady);
        caps.put("can_query_structured", anyReady);
        caps.put("can_aggregate", frozen != null
                ? frozen.get("aggregate_ready") : anyAggregate(snapshotId, assets));

        List<String> relations = new ArrayList<>();
        if (navigateReady) {
            relations.add("parent");
            relations.add("ancestors");
            relations.add("container");
            if ("document".equals(node.getNodeType()) || "section".equals(node.getNodeType())) {
                relations.add("children");
                relations.add("descendants");
            }
            if (node.getOrdinal() != null) {
                relations.add("previous");
                relations.add("next");
            }
            if ("table".equals(node.getNodeType())) {
                relations.add("caption");
            }
        }
        return new InspectResult(ref, "structure_ref", node.getNodeType(), null,
                sourceProjection(snapshotId, documentRefOf(node)), caps, relations, summaries);
    }

    /** st_ 指向表格资产：返回完整字段 schema（query_structured_asset 的入参依据）。 */
    private InspectResult inspectAsset(String ref, String snapshotId, TableAssetRow asset) {
        StructuredQueryService.TableSchema schema = queryService.schemaOf(snapshotId, asset);
        boolean canQuery = "ready".equals(asset.getReadiness()) && !schema.columns().isEmpty();
        boolean canAgg = schema.columns().stream().anyMatch(StructuredQueryService.FieldSchema::can_aggregate);

        Map<String, Boolean> caps = new LinkedHashMap<>();
        caps.put("can_read_document", true);
        caps.put("can_navigate", navigateReady(snapshotId));
        caps.put("can_query_structured", canQuery);
        caps.put("can_aggregate", canAgg);

        AssetSummary summary = new AssetSummary(ref, asset.getAssetType(), asset.getReadiness(),
                asset.getRowCount(),
                schema.columns().stream().map(StructuredQueryService.FieldSchema::name).toList(),
                canAgg, schema.columns());
        List<String> relations = new ArrayList<>(List.of("parent", "ancestors", "container", "caption"));
        return new InspectResult(ref, "structure_ref", asset.getAssetType(), null,
                sourceProjection(snapshotId, documentRefOfAsset(asset)), caps, relations,
                List.of(summary));
    }

    /** ev_ 指向证据：定位其 canonical target 后按 target 类型披露（§8.3）。 */
    private InspectResult inspectEvidence(String ref, String snapshotId, String canonicalId) {
        List<UnitV2Row> reps =
                sourceMapper.selectCanonicalRepresentations(List.of(snapshotId), List.of(canonicalId));
        if (reps.isEmpty()) {
            throw StructureToolException.invalidRef("证据不可回源（canonical 无 returnable 表示）");
        }
        UnitV2Row rep = reps.get(0);
        TargetRefFormat.Parsed parsed = TargetRefFormat.parse(rep.getTargetRef());
        String nodeType = parsed == null ? null : switch (parsed.targetType()) {
            case "segment" -> "segment";
            case "section" -> "section";
            case "document" -> "document";
            case "table" -> "table";
            case "table_row" -> "table_row";
            default -> null;
        };
        boolean navigable = parsed != null && nodeType != null;

        List<TableAssetRow> assets = navigable
                ? toolMapper.selectSnapshotTableAssets(snapshotId, ASSET_LIST_CAP) : List.of();
        List<AssetSummary> summaries = assetSummaries(snapshotId, assets, false);
        boolean anyReady = summaries.stream().anyMatch(a -> "ready".equals(a.readiness()));

        Map<String, Boolean> caps = new LinkedHashMap<>();
        caps.put("can_read_document", true);
        caps.put("can_navigate", navigateReady(snapshotId) && navigable);
        caps.put("can_query_structured", anyReady);
        caps.put("can_aggregate", anyAggregate(snapshotId, assets));

        return new InspectResult(ref, "evidence_ref", nodeType, rep.getRepresentationType(),
                sourceProjection(snapshotId, rep.getTargetRef() == null ? null
                        : documentRefOfRaw(rep.getTargetRef())), caps,
                caps.get("can_navigate")
                        ? List.of("parent", "ancestors", "container") : List.of(),
                summaries);
    }

    // ------------------------------------------------------------------ facts

    /** structure_navigate_ready 同源判定：有节点且有 parent 边（readiness 口径）。 */
    private boolean navigateReady(String snapshotId) {
        boolean hasContent = toolMapper.countNodesByType(snapshotId, "section") > 0
                || toolMapper.countNodesByType(snapshotId, "segment") > 0;
        return hasContent && toolMapper.countEdgesByRelation(snapshotId, "parent") > 0;
    }

    /** aggregate_ready：前 N 个 ready 资产里存在数值列（有界扫描）。 */
    private boolean anyAggregate(String snapshotId, List<TableAssetRow> assets) {
        int scanned = 0;
        for (TableAssetRow asset : assets) {
            if (!"ready".equals(asset.getReadiness())) {
                continue;
            }
            if (scanned++ >= AGGREGATE_SCAN_ASSET_CAP) {
                break;
            }
            StructuredQueryService.TableSchema schema = queryService.schemaOf(snapshotId, asset);
            if (schema.columns().stream().anyMatch(StructuredQueryService.FieldSchema::can_aggregate)) {
                return true;
            }
        }
        return false;
    }

    private List<AssetSummary> assetSummaries(String snapshotId, List<TableAssetRow> assets,
                                              boolean withSchema) {
        List<AssetSummary> out = new ArrayList<>(assets.size());
        for (TableAssetRow a : assets) {
            List<StructuredQueryService.FieldSchema> schema = withSchema
                    ? queryService.schemaOf(snapshotId, a).columns() : null;
            out.add(new AssetSummary(
                    codec.encodeStructure(snapshotId, a.getAssetRef()),
                    a.getAssetType(), a.getReadiness(), a.getRowCount(),
                    schema == null ? parseColumns(a) : schema.stream()
                            .map(StructuredQueryService.FieldSchema::name).toList(),
                    null, schema));
        }
        return out;
    }

    private static List<String> parseColumns(TableAssetRow asset) {
        try {
            List<?> raw = com.coremasterkb.serving.util.JsonUtils.mapper()
                    .readValue(asset.getColumnsJson() == null ? "[]" : asset.getColumnsJson(),
                            List.class);
            return raw.stream().map(String::valueOf).filter(s -> !s.isBlank()).toList();
        } catch (Exception e) {
            return List.of();
        }
    }

    private Map<String, Object> sourceProjection(String snapshotId, String documentRef) {
        List<EvidenceDocumentRow> rows = sourceMapper.selectDocumentSources(List.of(snapshotId));
        EvidenceDocumentRow doc = rows.isEmpty() ? null : rows.get(0);
        Map<String, Object> source = new LinkedHashMap<>();
        if (doc != null) {
            source.put("knowledge_base", doc.getKbName());
            source.put("file_name", doc.getDocumentName());
            source.put("relative_path", doc.getRelativePath());
        }
        source.put("document_ref", documentRef != null
                ? codec.encodeDocument(snapshotId, documentRef) : null);
        return source;
    }

    private static String documentRefOf(StructureNodeRow node) {
        String ref = node.getRef();
        if (ref == null) return null;
        int hash = ref.indexOf('#');
        return hash > 0 ? ref.substring(0, hash) : ref;
    }

    private static String documentRefOfAsset(TableAssetRow asset) {
        String ref = asset.getAssetRef();
        if (ref == null) return null;
        int hash = ref.indexOf('#');
        return hash > 0 ? ref.substring(0, hash) : ref;
    }

    /** target_ref（`{doc}#…`）的文档部分。 */
    private static String documentRefOfRaw(String targetRef) {
        int hash = targetRef.indexOf('#');
        return hash > 0 ? targetRef.substring(0, hash) : targetRef;
    }
}
