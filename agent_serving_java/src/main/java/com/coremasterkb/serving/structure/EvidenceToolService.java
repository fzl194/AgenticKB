package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domain.EvidenceResponse;
import com.coremasterkb.serving.domain.HydratedEvidence;
import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.evidence.EvidenceRefCodec;
import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.mapper.result.EvidenceDocumentRow;
import com.coremasterkb.serving.mapper.result.SegmentTextRow;
import com.coremasterkb.serving.mapper.result.UnitV2Row;
import com.coremasterkb.serving.operator.core.ExecContext;
import com.coremasterkb.serving.operator.core.Params;
import com.coremasterkb.serving.operator.core.SlotValues;
import com.coremasterkb.serving.operator.mapper.EvidenceSourceV2Mapper;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;
import com.coremasterkb.serving.operator.operators.output.EvidenceHydrateOperator;
import com.coremasterkb.serving.util.JsonUtils;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * {@code get_evidence} / {@code get_document}——按 opaque ref 取准确原文（批次8 R7，
 * 25 号 §8.1）。
 *
 * <p>get_evidence：ev_ 反查 (snapshot, canonical) → 复用 {@link EvidenceHydrateOperator}
 * 以<b>宽预算</b>重水合（默认 auto：父章节/整文 fits 即大粒度返回；可用 mode 显式收窄），
 * 投影与 assemble 同协议但 {@code truncated} 如实反映水合截断——这正是 EvidenceResponse
 * {@code truncated=true} 时 Agent 的取回通道。</p>
 *
 * <p>get_document：doc_ 反查 → 结构化章节读取（章节 outline + segment 有界稳定分页），
 * 不再要求 Agent 记 kb name + 内部 document id。</p>
 */
@Service
public class EvidenceToolService {

    private static final Logger log = LoggerFactory.getLogger(EvidenceToolService.class);

    static final int DEFAULT_DOCUMENT_LIMIT = 100;
    static final int MAX_DOCUMENT_LIMIT = 200;
    static final int OUTLINE_CAP = 200;
    /** 单 segment 展示截断（超长原文保护；truncated=true 告知）。 */
    static final int SEGMENT_TEXT_CAP = 4000;

    private static final Set<String> HYDRATE_MODES = Set.of(
            "auto", "exact", "window", "parent", "whole_document");

    private final StructureRefService refService;
    private final EvidenceSourceV2Mapper sourceMapper;
    private final StructureToolMapper toolMapper;
    private final EvidenceHydrateOperator hydrateOperator;
    private final EvidenceRefCodec codec;

    public EvidenceToolService(StructureRefService refService,
                               EvidenceSourceV2Mapper sourceMapper,
                               StructureToolMapper toolMapper,
                               EvidenceHydrateOperator hydrateOperator,
                               EvidenceRefCodec codec) {
        this.refService = refService;
        this.sourceMapper = sourceMapper;
        this.toolMapper = toolMapper;
        this.hydrateOperator = hydrateOperator;
        this.codec = codec;
    }

    // ------------------------------------------------------------------ get_evidence

    /**
     * @param mode 展开粒度（auto/exact/window/parent/whole_document；缺省 auto = 宽预算就大）
     */
    public EvidenceResponse.EvidenceItem getEvidence(String evidenceRef, String mode,
                                                     String domain, List<String> kbIds,
                                                     String username) {
        if (mode != null && !mode.isBlank() && !HYDRATE_MODES.contains(mode)) {
            throw StructureToolException.unsupportedOperation(
                    "未知展开模式: " + mode + "；允许：" + HYDRATE_MODES,
                    Map.of("allowed", new ArrayList<>(HYDRATE_MODES.stream().sorted().toList())));
        }
        EvidenceRefResolver.ResolvedRef resolved =
                refService.resolve(evidenceRef, domain, kbIds, username);
        if (resolved.kind() != EvidenceRefResolver.RefKind.EVIDENCE) {
            throw StructureToolException.invalidRef("get_evidence 期望 ev_ 前缀证据 ref");
        }
        String snapshotId = resolved.snapshotId();
        String canonical = resolved.internalRef();

        List<UnitV2Row> reps =
                sourceMapper.selectCanonicalRepresentations(List.of(snapshotId), List.of(canonical));
        if (reps.isEmpty() || reps.get(0).getTargetRef() == null) {
            throw StructureToolException.invalidRef("证据不可回源（无 returnable 源表示）");
        }
        UnitV2Row rep = reps.get(0);
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("snapshot_id", snapshotId);
        RetrievalCandidate synthetic = new RetrievalCandidate(
                rep.getRepresentationId(), 1.0, "get_evidence", metadata, null,
                List.of(rep.getRepresentationId()), rep.getRepresentationType(), canonical,
                rep.getTargetType(), rep.getTargetRef(), "get_evidence", 1, 1.0, null, Map.of());

        ActiveScope scope = new ActiveScope(
                "kb:" + snapshotId, null, List.of(snapshotId), Map.of(), Map.of());
        ExecContext ctx = new ExecContext("get-evidence", domain, "default", false, username);
        ctx.setQuery("");

        SlotValues out = hydrateOperator.execute(
                SlotValues.of("candidates", List.of(synthetic)).put("scope", scope),
                wideParams(mode), ctx);
        List<HydratedEvidence> hydrated = out.getHydratedEvidence("hydratedEvidence");
        if (hydrated == null || hydrated.isEmpty()) {
            throw StructureToolException.invalidRef("证据不可回源（水合无结果——ref 可能已失效）");
        }
        return toItem(snapshotId, canonical, hydrated.get(0));
    }

    /** 宽预算水合参数（§5.3 truncated 通道：get_evidence 是取"准确完整/更大粒度"原文的口）。 */
    private static Params wideParams(String mode) {
        ObjectNode node = JsonNodeFactory.instance.objectNode();
        node.put("mode", (mode == null || mode.isBlank()) ? "auto" : mode);
        node.put("windowRadius", 2);
        node.put("maxParentTokens", 50_000);
        node.put("maxDocumentTokens", 100_000);
        node.put("topN", 1);
        return new Params(node);
    }

    /** 与 AssembleOperator 相同的公开投影（ref/type/content/source/truncated/structure_ref）。 */
    private EvidenceResponse.EvidenceItem toItem(String snapshotId, String canonical,
                                                 HydratedEvidence e) {
        HydratedEvidence.SourceProjection src = e.source();
        EvidenceResponse.EvidenceSource source = new EvidenceResponse.EvidenceSource(
                src != null ? src.knowledgeBase() : null,
                src != null ? src.fileName() : null,
                src != null ? src.relativePath() : null,
                e.documentRef() != null ? codec.encodeDocument(snapshotId, e.documentRef()) : null,
                src != null ? src.section() : null,
                src != null ? src.page() : null);
        boolean truncated = e.provenance().get("truncated") instanceof Boolean b && b;
        String structureRef = e.navigable() && !e.structureRefs().isEmpty()
                ? codec.encodeStructure(snapshotId, e.structureRefs().get(0)) : null;
        return new EvidenceResponse.EvidenceItem(
                codec.encodeEvidence(snapshotId, canonical), e.evidenceType(),
                e.contentText(), source, truncated, structureRef);
    }

    // ------------------------------------------------------------------ get_document

    /** 章节摘要。 */
    public record SectionSummary(String ref, String title, Integer level) {}

    /** 文档 segment 行。 */
    public record SegmentView(
            int ordinal, String block_type, String section, String text, boolean truncated) {}

    /** get_document 响应（有界稳定分页：outline 仅首页返回）。 */
    public record DocumentResult(
            String document_ref,
            Map<String, Object> source,
            List<SectionSummary> sections,
            List<SegmentView> segments,
            int total_segments,
            String cursor,
            boolean has_more) {}

    public DocumentResult getDocument(String documentRef, Integer limit, String cursor,
                                      String domain, List<String> kbIds, String username) {
        EvidenceRefResolver.ResolvedRef resolved =
                refService.resolve(documentRef, domain, kbIds, username);
        if (resolved.kind() != EvidenceRefResolver.RefKind.DOCUMENT) {
            throw StructureToolException.invalidRef("get_document 期望 doc_ 前缀文档 ref");
        }
        String snapshotId = resolved.snapshotId();
        int effLimit = Math.max(1, Math.min(
                limit == null ? DEFAULT_DOCUMENT_LIMIT : limit, MAX_DOCUMENT_LIMIT));
        // A0-3：cursor = 上一页最后一条实际 ordinal（排他下界）；空 cursor 起点 -1
        //（SQL 是 ordinal > after——此前起点 0 漏掉 segment 0）。
        int after = Cursors.decodeAfter(cursor);

        int total = toolMapper.countSegments(snapshotId);
        List<SegmentTextRow> rows =
                toolMapper.selectSegmentsPage(snapshotId, after, effLimit + 1);
        boolean hasMore = rows.size() > effLimit;
        List<SegmentView> segments = new ArrayList<>(Math.min(rows.size(), effLimit));
        for (int i = 0; i < Math.min(rows.size(), effLimit); i++) {
            segments.add(toView(rows.get(i)));
        }

        boolean firstPage = after == Cursors.START;
        List<SectionSummary> sections = firstPage
                ? toolMapper.selectSectionOutline(snapshotId, OUTLINE_CAP).stream()
                        .map(n -> new SectionSummary(
                                codec.encodeStructure(snapshotId, n.getRef()),
                                n.getTitle(), n.getLevel()))
                        .toList()
                : List.of();

        List<EvidenceDocumentRow> docs = sourceMapper.selectDocumentSources(List.of(snapshotId));
        EvidenceDocumentRow doc = docs.isEmpty() ? null : docs.get(0);
        Map<String, Object> source = new LinkedHashMap<>();
        if (doc != null) {
            source.put("knowledge_base", doc.getKbName());
            source.put("file_name", doc.getDocumentName());
            source.put("relative_path", doc.getRelativePath());
        }

        // 下一页游标记录本页最后一条实际 ordinal——编号稀疏时仍不漏不重
        //（此前 offset+effLimit 假设编号连续，跳号会漏行）。ordinal 缺失的
        // 数据异常态不给游标（客户端重查），不制造不可解码的 a:-1。
        String nextCursor = null;
        if (hasMore && !rows.isEmpty()) {
            Integer last = rows.get(Math.min(rows.size(), effLimit) - 1).getOrdinal();
            if (last != null) {
                nextCursor = Cursors.encodeAfter(last);
            }
        }
        return new DocumentResult(documentRef, source, sections, segments, total,
                nextCursor, hasMore);
    }

    private static SegmentView toView(SegmentTextRow row) {
        String text = row.getRawText() == null ? "" : row.getRawText();
        boolean truncated = text.length() > SEGMENT_TEXT_CAP;
        return new SegmentView(
                row.getOrdinal() == null ? -1 : row.getOrdinal(),
                row.getBlockType(),
                lastSectionTitle(row.getHeadingChainJson()),
                truncated ? text.substring(0, SEGMENT_TEXT_CAP) : text,
                truncated);
    }

    /** heading_chain_json（[[level,title],…]）末位标题 = 该 segment 所属章节。 */
    private static String lastSectionTitle(String headingChainJson) {
        if (headingChainJson == null || headingChainJson.isBlank()) {
            return null;
        }
        try {
            var node = JsonUtils.mapper().readTree(headingChainJson);
            if (node.isArray() && node.size() > 0 && node.get(node.size() - 1).isArray()
                    && node.get(node.size() - 1).size() > 1) {
                return node.get(node.size() - 1).get(1).asText(null);
            }
        } catch (Exception e) {
            log.debug("[get_document] heading chain parse failed: {}", e.getMessage());
        }
        return null;
    }
}
