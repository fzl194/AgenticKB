package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.evidence.EvidenceRefCodec;
import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.mapper.result.EvidenceDocumentRow;
import com.coremasterkb.serving.mapper.result.StructureNodeRow;
import com.coremasterkb.serving.operator.mapper.EvidenceSourceV2Mapper;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * {@code structure_navigate}——确定性后端能力（批次8 R7，25 号 §6.10）。
 *
 * <p>由 {@code navigate_structure} MCP 工具调用，不进搜索主图、不注册 Operator catalog。
 * 只按显式 {@code st_} ref + 白名单关系走 {@code asset_structure_nodes/edges} 批量查询，
 * 不从 query 猜章节、不做隐藏 hard filter、不复用 legacy TreeNavigator 推断。
 * {@code references/footnotes} 仅跟随 {@code asset_structure_edges} 里的显式可追溯边
 * （当前投影只产出 parent/order 边——二者返回空结果是正常结果）。</p>
 *
 * <p>上限：depth 默认 1、上限 3（仅 ancestors/descendants）；limit 默认 50、上限 200；
 * cursor 为 base64 offset（快照不可变 → 顺序稳定）。</p>
 */
@Service
public class StructureNavigateService {

    /** 白名单关系（§6.10）。 */
    public static final Set<String> RELATIONS = Set.of(
            "parent", "children", "previous", "next", "ancestors", "descendants",
            "container", "caption", "footnotes", "references");

    static final int DEFAULT_DEPTH = 1;
    static final int MAX_DEPTH = 3;
    static final int DEFAULT_LIMIT = 50;
    static final int MAX_LIMIT = 200;
    static final int SIBLING_SCAN_CAP = 1000;

    private final StructureRefService refService;
    private final StructureToolMapper toolMapper;
    private final EvidenceSourceV2Mapper sourceMapper;
    private final EvidenceRefCodec codec;

    public StructureNavigateService(StructureRefService refService,
                                    StructureToolMapper toolMapper,
                                    EvidenceSourceV2Mapper sourceMapper,
                                    EvidenceRefCodec codec) {
        this.refService = refService;
        this.toolMapper = toolMapper;
        this.sourceMapper = sourceMapper;
        this.codec = codec;
    }

    /** 结构节点摘要（public ref = st_ 编码）。 */
    public record NodeSummary(
            String ref,
            String node_type,
            String title,
            Integer level,
            Integer ordinal,
            String block_type,
            List<String> relations) {}

    /** navigate 响应。 */
    public record NavigateResult(
            String structure_ref,
            String relation,
            int depth,
            int limit,
            List<NodeSummary> nodes,
            String cursor,
            boolean has_more,
            Map<String, Object> source) {}

    public NavigateResult navigate(String structureRef, String relation, Integer depth,
                                   Integer limit, String cursor,
                                   String domain, List<String> kbIds, String username) {
        if (relation == null || !RELATIONS.contains(relation)) {
            throw StructureToolException.unsupportedOperation(
                    "未知关系: " + relation + "；允许：" + RELATIONS,
                    Map.of("allowed_relations", new ArrayList<>(RELATIONS.stream().sorted().toList())));
        }
        int effDepth = clamp(depth == null ? DEFAULT_DEPTH : depth, 1, MAX_DEPTH);
        int effLimit = clamp(limit == null ? DEFAULT_LIMIT : limit, 1, MAX_LIMIT);
        int offset = Cursors.decodeOffset(cursor);

        EvidenceRefResolver.ResolvedRef resolved =
                refService.resolve(structureRef, domain, kbIds, username);
        String snapshotId = resolved.snapshotId();
        // A0-2：document ref 双变体（历史快照裸 ref / 新快照 #document）
        StructureNodeRow node = StructureNodeLookup.find(toolMapper, snapshotId,
                resolved.internalRef());
        if (node == null) {
            throw StructureToolException.invalidRef("目标结构节点不存在（ref 可能已失效）");
        }

        List<StructureNodeRow> rows = switch (relation) {
            case "parent" -> parentOf(node);
            case "children" -> page(toolMapper.selectChildren(
                    snapshotId, node.getRef(), effLimit + 1, offset), effLimit);
            case "previous", "next" -> adjacentSibling(snapshotId, node, relation);
            case "ancestors" -> toolMapper.selectAncestors(snapshotId, node.getRef(), effDepth);
            case "descendants" -> page(toolMapper.selectDescendants(
                    snapshotId, node.getRef(), effDepth, effLimit + 1, offset), effLimit);
            case "container" -> containerOf(snapshotId, node);
            case "caption" -> captionOf(snapshotId, node);
            case "footnotes" -> edgeTargets(snapshotId, node, "footnote", effLimit);
            case "references" -> edgeTargets(snapshotId, node, "reference", effLimit);
            default -> throw StructureToolException.unsupportedOperation(
                    "未知关系: " + relation, Map.of());
        };

        boolean hasMore = rows.size() > effLimit && switch (relation) {
            // 单值关系天然无分页语义
            case "parent", "previous", "next", "container", "caption" -> false;
            default -> true;
        };
        List<StructureNodeRow> trimmed = hasMore ? rows.subList(0, effLimit) : rows;
        String nextCursor = hasMore ? Cursors.encodeOffset(offset + effLimit) : null;

        return new NavigateResult(
                structureRef, relation, effDepth, effLimit,
                trimmed.stream().map(r -> toSummary(snapshotId, r)).toList(),
                nextCursor, hasMore, sourceProjection(snapshotId, documentRefOf(node)));
    }

    // ------------------------------------------------------------------ relations

    private List<StructureNodeRow> parentOf(StructureNodeRow node) {
        if (node.getParentRef() == null || node.getParentRef().isEmpty()) {
            return List.of();
        }
        StructureNodeRow parent = toolMapper.selectNode(node.getSnapshotId(), node.getParentRef());
        return parent == null ? List.of() : List.of(parent);
    }

    private List<StructureNodeRow> adjacentSibling(String snapshotId, StructureNodeRow node,
                                                   String relation) {
        if (node.getParentRef() == null || node.getOrdinal() == null) {
            return List.of(); // 无序节点（如 section）没有确定的前后邻居
        }
        List<StructureNodeRow> siblings =
                toolMapper.selectSiblings(snapshotId, node.getParentRef(), SIBLING_SCAN_CAP);
        int idx = -1;
        for (int i = 0; i < siblings.size(); i++) {
            if (node.getRef().equals(siblings.get(i).getRef())) {
                idx = i;
                break;
            }
        }
        if (idx < 0) {
            return List.of();
        }
        int target = "next".equals(relation) ? idx + 1 : idx - 1;
        if (target < 0 || target >= siblings.size()) {
            return List.of();
        }
        return List.of(siblings.get(target));
    }

    /** 最近祖先 section/document（segment/table 的语义容器）。 */
    private List<StructureNodeRow> containerOf(String snapshotId, StructureNodeRow node) {
        if ("document".equals(node.getNodeType()) || "section".equals(node.getNodeType())) {
            return List.of(node); // 章节/文档即自身的容器
        }
        for (StructureNodeRow ancestor : toolMapper.selectAncestors(snapshotId, node.getRef(), MAX_DEPTH)) {
            if ("section".equals(ancestor.getNodeType()) || "document".equals(ancestor.getNodeType())) {
                return List.of(ancestor);
            }
        }
        return List.of();
    }

    /** 表格 caption：源表示的 structural_context（无则空结果——caption 是可得信息）。 */
    private List<StructureNodeRow> captionOf(String snapshotId, StructureNodeRow node) {
        if (!"table".equals(node.getNodeType())) {
            return List.of();
        }
        String tableRef = shortTableRef(node);
        String caption = toolMapper.selectStructuralContext(snapshotId, tableRef);
        if (caption == null || caption.isBlank()) {
            return List.of();
        }
        StructureNodeRow pseudo = new StructureNodeRow();
        pseudo.setSnapshotId(snapshotId);
        pseudo.setNodeType("caption");
        pseudo.setRef(node.getRef());
        pseudo.setTitle(caption.length() > 200 ? caption.substring(0, 200) : caption);
        return List.of(pseudo);
    }

    /** 显式边目标节点（批量取，无 N+1）。 */
    private List<StructureNodeRow> edgeTargets(String snapshotId, StructureNodeRow node,
                                               String relation, int limit) {
        List<StructureToolMapper.EdgeRow> edges =
                toolMapper.selectEdges(snapshotId, node.getRef(), relation, limit);
        if (edges.isEmpty()) {
            return List.of();
        }
        List<String> toRefs = edges.stream().map(StructureToolMapper.EdgeRow::toRef).distinct().toList();
        List<StructureNodeRow> targets =
                sourceMapper.selectStructureNodes(List.of(snapshotId), toRefs);
        // 保持边顺序（去重后的 to_ref 顺序）
        Set<String> seen = new LinkedHashSet<>(toRefs);
        List<StructureNodeRow> ordered = new ArrayList<>();
        for (String ref : seen) {
            targets.stream()
                    .filter(t -> ref.equals(t.getRef()))
                    .findFirst()
                    .ifPresent(ordered::add);
        }
        return ordered;
    }

    private static List<StructureNodeRow> page(List<StructureNodeRow> rows, int limit) {
        return rows.size() > limit ? new ArrayList<>(rows.subList(0, limit + 1)) : rows;
    }

    // ------------------------------------------------------------------ projection

    private NodeSummary toSummary(String snapshotId, StructureNodeRow node) {
        return new NodeSummary(
                codec.encodeStructure(snapshotId, node.getRef()),
                node.getNodeType(),
                node.getTitle(),
                node.getLevel(),
                node.getOrdinal(),
                node.getBlockType(),
                availableRelations(node));
    }

    /** 该节点可继续的关系（§8.3 渐进披露：让 Agent 知道下一步能走哪）。 */
    private static List<String> availableRelations(StructureNodeRow node) {
        List<String> out = new ArrayList<>();
        out.add("parent");
        out.add("ancestors");
        out.add("container");
        String type = node.getNodeType() == null ? "" : node.getNodeType();
        if ("document".equals(type) || "section".equals(type)) {
            out.add("children");
            out.add("descendants");
        }
        if (node.getOrdinal() != null) {
            out.add("previous");
            out.add("next");
        }
        if ("table".equals(type)) {
            out.add("caption");
        }
        return out.stream().distinct().toList();
    }

    /** 文档级 source projection（snapshot = 单文档快照，导航结果同源）。 */
    private Map<String, Object> sourceProjection(String snapshotId, String documentRef) {
        List<EvidenceDocumentRow> rows = sourceMapper.selectDocumentSources(List.of(snapshotId));
        EvidenceDocumentRow doc = rows.isEmpty() ? null : rows.get(0);
        Map<String, Object> source = new java.util.LinkedHashMap<>();
        if (doc != null) {
            source.put("knowledge_base", doc.getKbName());
            source.put("file_name", doc.getDocumentName());
            source.put("relative_path", doc.getRelativePath());
        }
        source.put("document_ref", documentRef != null
                ? codec.encodeDocument(snapshotId, documentRef) : null);
        return source;
    }

    /** 节点 ref 的文档部分（`{doc}#…` 取 '#' 前；document 节点 ref 本身即文档 ref）。 */
    private static String documentRefOf(StructureNodeRow node) {
        String ref = node.getRef();
        if (ref == null) return null;
        int hash = ref.indexOf('#');
        return hash > 0 ? ref.substring(0, hash) : ref;
    }

    /** table 节点 ref 的短表 ref（`{doc}#table:{tbl}` 的 {tbl}）。 */
    private static String shortTableRef(StructureNodeRow node) {
        String ref = node.getRef();
        int cut = ref == null ? -1 : ref.lastIndexOf("#table:");
        return cut >= 0 ? ref.substring(cut + "#table:".length()) : ref;
    }

    // ------------------------------------------------------------------ cursor

    private static int clamp(int v, int min, int max) {
        return Math.max(min, Math.min(max, v));
    }
}
