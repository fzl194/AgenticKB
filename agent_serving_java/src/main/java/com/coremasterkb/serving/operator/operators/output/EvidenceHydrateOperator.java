package com.coremasterkb.serving.operator.operators.output;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domain.HydratedEvidence;
import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.mapper.result.EvidenceDocumentRow;
import com.coremasterkb.serving.mapper.result.SegmentTextRow;
import com.coremasterkb.serving.mapper.result.StructureNodeRow;
import com.coremasterkb.serving.mapper.result.TableAssetRow;
import com.coremasterkb.serving.mapper.result.TableCellRow;
import com.coremasterkb.serving.mapper.result.UnitV2Row;
import com.coremasterkb.serving.operator.core.ErrorPolicy;
import com.coremasterkb.serving.operator.core.ExecContext;
import com.coremasterkb.serving.operator.core.Operator;
import com.coremasterkb.serving.operator.core.OperatorDef;
import com.coremasterkb.serving.operator.core.Params;
import com.coremasterkb.serving.operator.core.SlotDecl;
import com.coremasterkb.serving.operator.core.SlotType;
import com.coremasterkb.serving.operator.core.SlotValues;
import com.coremasterkb.serving.operator.mapper.EvidenceSourceV2Mapper;
import com.coremasterkb.serving.util.JsonUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * {@code evidence_hydrate} — 搜索表示命中 → 真实证据的唯一主链算子（批次8 R5，25 号 §6.8）。
 *
 * <p>Top-N 候选批量解析 canonical target 回源读取原文并类型化展开（prose 邻窗 /
 * table_row 表头回填 / section 聚合 / alias 回源）。批量约束：按 snapshot_id 分组批量查询
 * （canonical 表示 / 结构节点 / 邻窗 / 章节子行 / 整文 / 表格资产+cells），禁止 N+1、禁止
 * 全库扫描、不递归无界图。只依据 parent/container/ordinal/caption 等确定性结构；不读旧
 * RST relation。</p>
 *
 * <p>失败语义：单条证据读取失败按候选留痕跳过（ctx attribute {@code hydrateSkipped}）；
 * 授权范围外 snapshot 直接跳过；存储系统性失败（mapper 抛出）按 FAIL_FAST 向上抛，不伪装
 * 空结果。hydrate 不做最终条数截断、公开协议投影或 rerank——那是 assemble 的职责。</p>
 */
@Component
public class EvidenceHydrateOperator implements Operator {

    private static final Logger log = LoggerFactory.getLogger(EvidenceHydrateOperator.class);

    /** 章节直接子行 / 整文行 / 每表 cells 的有界上限（防单证据撑爆内存/响应）。 */
    static final int MAX_SECTION_ROWS = 200;
    static final int MAX_DOCUMENT_ROWS = 400;
    static final int MAX_TABLE_CELLS = 400;

    /** 展开模式。 */
    static final String MODE_AUTO = "auto";
    static final String MODE_EXACT = "exact";
    static final String MODE_WINDOW = "window";
    static final String MODE_PARENT = "parent";
    static final String MODE_WHOLE_DOCUMENT = "whole_document";

    /** 公开协议类型映射（§5.3 / A0-4）：representation_type → evidence type，单一真相源
     * {@link com.coremasterkb.serving.operator.api.EvidenceTypeVocabulary}；target_type 兜底映射。 */

    private static final String PARAM_SCHEMA = """
            {"type":"object","properties":{
              "mode":{"type":"string","enum":["auto","exact","window","parent","whole_document"],\
            "default":"auto","title":"展开模式",\
            "description":"auto=按预算就大原则（parent→window→exact）；whole_document 仅全文 fits 时生效"},
              "windowRadius":{"type":"integer","minimum":0,"maximum":5,"default":1,"title":"邻窗半径"},
              "maxParentTokens":{"type":"integer","minimum":100,"maximum":50000,"default":3000,\
            "title":"父章节 token 上限"},
              "maxDocumentTokens":{"type":"integer","minimum":100,"maximum":100000,"default":8000,\
            "title":"整文 token 上限"},
              "topN":{"type":"integer","minimum":1,"maximum":100,"default":50,\
            "title":"回源候选上限","description":"只 hydrate 融合排序后的前 N 个 canonical"}
            }}""";

    private final EvidenceSourceV2Mapper mapper;

    public EvidenceHydrateOperator(EvidenceSourceV2Mapper mapper) {
        this.mapper = mapper;
    }

    @Override
    public OperatorDef definition() {
        return new OperatorDef(
                "evidence_hydrate", "output", "证据回源水合",
                "Top-N 候选按 canonical target 批量回源读取原文并类型化展开（邻窗/表头回填/章节聚合/alias 回源）",
                List.of(
                        SlotDecl.required("candidates", SlotType.CANDIDATE_LIST, "融合排序候选"),
                        SlotDecl.required("scope", SlotType.SCOPE, "检索范围(授权 snapshot 集合)")),
                List.of(SlotDecl.required("hydratedEvidence", SlotType.HYDRATED_EVIDENCE_LIST, "水合证据列表")),
                PARAM_SCHEMA,
                ErrorPolicy.FAIL_FAST);
    }

    @Override
    public SlotValues execute(SlotValues inputs, Params params, ExecContext ctx) {
        List<RetrievalCandidate> candidates = inputs.getCandidates("candidates");
        ActiveScope scope = inputs.getScope("scope");
        if (candidates.isEmpty() || scope == null || scope.snapshotIds().isEmpty()) {
            return SlotValues.of("hydratedEvidence", List.of());
        }

        String mode = normalizedMode(params, ctx.requestExpansion());
        int windowRadius = params.getInt("windowRadius", 1);
        int maxParentTokens = params.getInt("maxParentTokens", 3000);
        int maxDocumentTokens = params.getInt("maxDocumentTokens", 8000);
        int topN = ctx.resolveTopK(params.getInt("topN", 50), 100);

        // canonical 去重（保序）+ topN 上限 + snapshot 授权过滤。
        Set<String> scopeSnapshots = new LinkedHashSet<>(scope.snapshotIds());
        Map<String, Work> works = new LinkedHashMap<>();
        List<Map<String, String>> skipped = new ArrayList<>();
        for (RetrievalCandidate c : candidates) {
            if (works.size() >= topN) {
                break;
            }
            String canonical = c.fusionKey();
            if (works.containsKey(snapshotScopedKey(snapshotOf(c), canonical))) {
                continue;
            }
            String snapshotId = snapshotOf(c);
            if (snapshotId == null || !scopeSnapshots.contains(snapshotId)) {
                skipped.add(skipOf(canonical, "snapshot_out_of_scope_or_missing"));
                continue;
            }
            TargetRefFormat.Parsed parsed = TargetRefFormat.parse(c.targetRef());
            if (parsed == null || c.targetType() == null) {
                skipped.add(skipOf(canonical, "unparseable_target_ref"));
                continue;
            }
            works.put(snapshotScopedKey(snapshotId, canonical),
                    new Work(c, snapshotId, canonical, parsed));
        }

        if (works.isEmpty()) {
            ctx.putAttribute("hydrateInputCount", candidates.size());
            ctx.putAttribute("hydrateSkipped", skipped);
            return SlotValues.of("hydratedEvidence", List.of());
        }

        List<HydratedEvidence> evidence = hydrate(works.values(), mode, windowRadius,
                maxParentTokens, maxDocumentTokens, skipped, ctx);
        ctx.putAttribute("hydrateInputCount", candidates.size());
        ctx.putAttribute("hydrateWorkCount", works.size());
        ctx.putAttribute("hydrateSkipped", skipped);
        ctx.putAttribute("hydrateOutputCount", evidence.size());
        return SlotValues.of("hydratedEvidence", evidence);
    }

    // -------------------------------------------------------------------------
    // Batch reads + typed expansion
    // -------------------------------------------------------------------------

    private List<HydratedEvidence> hydrate(java.util.Collection<Work> works, String mode,
                                           int windowRadius, int maxParentTokens,
                                           int maxDocumentTokens,
                                           List<Map<String, String>> skipped, ExecContext ctx) {
        List<String> snapshots = distinct(works, Work::snapshotId);
        List<String> canonicals = distinct(works, Work::canonicalId);

        // ---- Phase A：批量事实（系统性失败在此抛出，不吞） ----
        Map<String, UnitV2Row> reps = new LinkedHashMap<>();
        for (UnitV2Row row : mapper.selectCanonicalRepresentations(snapshots, canonicals)) {
            reps.putIfAbsent(rowKey(row.getSnapshotId(), row.getCanonicalEvidenceId()), row);
        }
        List<String> targetRefs = works.stream()
                .map(w -> w.candidate().targetRef()).filter(r -> r != null && !r.isEmpty()).distinct().toList();
        Map<String, StructureNodeRow> nodes = new LinkedHashMap<>();
        for (StructureNodeRow n : targetRefs.isEmpty() ? List.<StructureNodeRow>of()
                : mapper.selectStructureNodes(snapshots, targetRefs)) {
            nodes.putIfAbsent(nodeKey(n.getSnapshotId(), n.getRef()), n);
        }
        Map<String, EvidenceDocumentRow> docSources = new LinkedHashMap<>();
        for (EvidenceDocumentRow d : mapper.selectDocumentSources(snapshots)) {
            docSources.putIfAbsent(d.getSnapshotId(), d);
        }
        Map<String, Long> docTokens = new LinkedHashMap<>();
        for (var t : mapper.selectDocumentTokenTotals(snapshots)) {
            docTokens.put(t.snapshotId(), t.totalTokens() == null ? 0L : t.totalTokens());
        }

        // ---- 计算展开需求（batch Phase B 的入参） ----
        Map<String, StructureNodeRow> segmentNodes = new LinkedHashMap<>();
        for (Work w : works) {
            if (TargetRefFormat.SEGMENT.equals(w.parsed().targetType())) {
                StructureNodeRow n = nodes.get(nodeKey(w.snapshotId(), w.candidate().targetRef()));
                if (n != null) {
                    segmentNodes.put(nodeKey(w.snapshotId(), w.candidate().targetRef()), n);
                }
            }
        }

        List<EvidenceSourceV2Mapper.WindowAnchor> anchors = new ArrayList<>();
        Set<String> parentRefs = new LinkedHashSet<>();
        Set<String> wholeDocSnapshots = new LinkedHashSet<>();
        Set<String> tableRefs = new LinkedHashSet<>();
        for (Work w : works) {
            UnitV2Row rep = reps.get(rowKey(w.snapshotId(), w.canonicalId()));
            String targetType = w.parsed().targetType();
            switch (targetType) {
                case TargetRefFormat.SEGMENT -> {
                    StructureNodeRow n = segmentNodes.get(nodeKey(w.snapshotId(), w.candidate().targetRef()));
                    if (n != null && n.getParentRef() != null) {
                        if (MODE_WINDOW.equals(mode) || MODE_AUTO.equals(mode)) {
                            anchors.add(new EvidenceSourceV2Mapper.WindowAnchor(
                                    w.snapshotId(), n.getParentRef(),
                                    n.getOrdinal() == null ? 0 : n.getOrdinal(), windowRadius));
                        }
                        if (MODE_PARENT.equals(mode) || MODE_AUTO.equals(mode)) {
                            parentRefs.add(n.getParentRef());
                        }
                    }
                    if (MODE_WHOLE_DOCUMENT.equals(mode) && documentFits(w.snapshotId(), docTokens, maxDocumentTokens)) {
                        wholeDocSnapshots.add(w.snapshotId());
                    }
                }
                case TargetRefFormat.SECTION -> {
                    parentRefs.add(w.candidate().targetRef());
                    if (MODE_WHOLE_DOCUMENT.equals(mode)
                            && documentFits(w.snapshotId(), docTokens, maxDocumentTokens)) {
                        wholeDocSnapshots.add(w.snapshotId());
                    }
                }
                case TargetRefFormat.DOCUMENT -> {
                    // 整文只在显式请求且 fits 时取全量；auto 走有界聚合
                    if (documentFits(w.snapshotId(), docTokens, maxDocumentTokens)) {
                        wholeDocSnapshots.add(w.snapshotId());
                    }
                }
                case TargetRefFormat.TABLE, TargetRefFormat.TABLE_ROW -> {
                    String tableRef = TargetRefFormat.tableRefOf(w.parsed());
                    if (tableRef == null && rep != null && rep.getContainerRef() != null) {
                        tableRef = rep.getContainerRef();
                    }
                    if (tableRef != null) {
                        tableRefs.add(tableRef);
                    }
                }
                default -> { }
            }
        }

        // ---- Phase B：批量展开素材 ----
        Map<String, List<SegmentTextRow>> windowRows = new LinkedHashMap<>();
        if (!anchors.isEmpty()) {
            for (SegmentTextRow r : mapper.selectWindowSegments(anchors)) {
                windowRows.computeIfAbsent(windowKey(r.getSnapshotId(), r.getParentRef()),
                        k -> new ArrayList<>()).add(r);
            }
        }
        Map<String, List<SegmentTextRow>> sectionRows = new LinkedHashMap<>();
        if (!parentRefs.isEmpty()) {
            for (SegmentTextRow r : mapper.selectSectionSegments(snapshots, new ArrayList<>(parentRefs),
                    MAX_SECTION_ROWS)) {
                sectionRows.computeIfAbsent(windowKey(r.getSnapshotId(), r.getParentRef()),
                        k -> new ArrayList<>()).add(r);
            }
        }
        Map<String, List<SegmentTextRow>> documentRows = new LinkedHashMap<>();
        if (!wholeDocSnapshots.isEmpty()) {
            for (SegmentTextRow r : mapper.selectDocumentSegments(new ArrayList<>(wholeDocSnapshots),
                    MAX_DOCUMENT_ROWS)) {
                documentRows.computeIfAbsent(r.getSnapshotId(), k -> new ArrayList<>()).add(r);
            }
        }
        Map<String, TableAssetRow> tableAssets = new LinkedHashMap<>();
        Map<String, List<TableCellRow>> tableCells = new LinkedHashMap<>();
        if (!tableRefs.isEmpty()) {
            for (TableAssetRow a : mapper.selectTableAssets(snapshots, new ArrayList<>(tableRefs))) {
                tableAssets.putIfAbsent(windowKey(a.getSnapshotId(), a.getTableRef()), a);
            }
            for (TableCellRow c : mapper.selectTableCells(snapshots, new ArrayList<>(tableRefs),
                    MAX_TABLE_CELLS)) {
                tableCells.computeIfAbsent(windowKey(c.getSnapshotId(), c.getTableRef()),
                        k -> new ArrayList<>()).add(c);
            }
        }

        // ---- 类型化展开（单候选失败留痕跳过） ----
        Map<String, Integer> modeCounts = new LinkedHashMap<>();
        List<HydratedEvidence> out = new ArrayList<>(works.size());
        for (Work w : works) {
            try {
                HydratedEvidence e = expand(w, mode, windowRadius, maxParentTokens, maxDocumentTokens,
                        reps, segmentNodes, nodes, docSources, docTokens, windowRows, sectionRows,
                        documentRows, tableAssets, tableCells);
                if (e == null) {
                    skipped.add(skipOf(w.canonicalId(), "source_unresolvable"));
                    continue;
                }
                modeCounts.merge(e.expansionMode(), 1, Integer::sum);
                out.add(e);
            } catch (Exception ex) {
                // §6.8-8：证据读取失败按候选留痕跳过；不中断其余候选
                log.warn("[evidence_hydrate] candidate skipped canonical={} reason={}",
                        w.canonicalId(), ex.getMessage());
                skipped.add(skipOf(w.canonicalId(), "hydrate_failed:" + ex.getMessage()));
            }
        }
        ctx.putAttribute("hydrateModeCounts", modeCounts);
        ctx.putAttribute("hydrateBatchCounts", Map.of(
                "representations", reps.size(),
                "structureNodes", nodes.size(),
                "windowSegments", windowRows.values().stream().mapToInt(List::size).sum(),
                "sectionSegments", sectionRows.values().stream().mapToInt(List::size).sum(),
                "documentSegments", documentRows.values().stream().mapToInt(List::size).sum(),
                "tableCells", tableCells.values().stream().mapToInt(List::size).sum()));
        return out;
    }

    /** 单候选类型化展开；null = 不可回源（调用方留痕跳过）。 */
    private HydratedEvidence expand(Work w, String mode, int windowRadius, int maxParentTokens,
                                    int maxDocumentTokens,
                                    Map<String, UnitV2Row> reps,
                                    Map<String, StructureNodeRow> segmentNodes,
                                    Map<String, StructureNodeRow> nodes,
                                    Map<String, EvidenceDocumentRow> docSources,
                                    Map<String, Long> docTokens,
                                    Map<String, List<SegmentTextRow>> windowRows,
                                    Map<String, List<SegmentTextRow>> sectionRows,
                                    Map<String, List<SegmentTextRow>> documentRows,
                                    Map<String, TableAssetRow> tableAssets,
                                    Map<String, List<TableCellRow>> tableCells) {

        UnitV2Row rep = reps.get(rowKey(w.snapshotId(), w.canonicalId()));
        String targetType = w.parsed().targetType();
        return switch (targetType) {
            case TargetRefFormat.SEGMENT -> expandSegment(w, mode, windowRadius, maxParentTokens,
                    maxDocumentTokens, rep, segmentNodes, docSources, docTokens, windowRows,
                    sectionRows, documentRows);
            case TargetRefFormat.SECTION -> expandSection(w, mode, maxParentTokens,
                    maxDocumentTokens, rep, nodes, docSources, docTokens, sectionRows, documentRows);
            case TargetRefFormat.DOCUMENT -> expandDocument(w, mode, maxParentTokens,
                    maxDocumentTokens, docSources, documentRows);
            case TargetRefFormat.TABLE -> expandTable(w, rep, docSources, tableAssets, tableCells);
            case TargetRefFormat.TABLE_ROW -> expandTableRow(w, rep, docSources, tableAssets,
                    tableCells);
            default -> null;
        };
    }

    // ---- segment（prose/code/list/formula/figure_caption）: exact + 同 section 有界邻窗；父 fits 可 parent --

    private HydratedEvidence expandSegment(Work w, String mode, int windowRadius,
                                           int maxParentTokens, int maxDocumentTokens,
                                           UnitV2Row rep, Map<String, StructureNodeRow> segmentNodes,
                                           Map<String, EvidenceDocumentRow> docSources,
                                           Map<String, Long> docTokens,
                                           Map<String, List<SegmentTextRow>> windowRows,
                                           Map<String, List<SegmentTextRow>> sectionRows,
                                           Map<String, List<SegmentTextRow>> documentRows) {
        String nodeKey = nodeKey(w.snapshotId(), w.candidate().targetRef());
        StructureNodeRow node = segmentNodes.get(nodeKey);
        if (node == null && rep == null) {
            return null;
        }
        String parentRef = node != null ? node.getParentRef() : null;
        Integer ordinal = node != null ? node.getOrdinal()
                : TargetRefFormat.segmentOrdinal(w.parsed());
        String sectionPath = sectionPathOf(rep);

        // whole_document（显式且 fits）优先
        if (MODE_WHOLE_DOCUMENT.equals(mode)
                && documentFits(w.snapshotId(), docTokens, maxDocumentTokens)) {
            List<SegmentTextRow> rows = documentRows.get(w.snapshotId());
            if (rows != null && !rows.isEmpty()) {
                return build(w, rep, "document", parentRef, ordinal, null, null,
                        fragmentsOfRows(rows, "document", sectionPath), MODE_WHOLE_DOCUMENT,
                        structureRefs(parentRef), parentRef != null, provenance(rep, Map.of()),
                        docSources);
            }
        }

        // parent：同 section 直接子行聚合，fits 才用（auto 就大原则）
        if ((MODE_PARENT.equals(mode) || MODE_AUTO.equals(mode)) && parentRef != null) {
            List<SegmentTextRow> rows = sectionRows.get(windowKey(w.snapshotId(), parentRef));
            Bounded<SegmentTextRow> bounded = boundedByTokens(rows, maxParentTokens);
            if (rows != null && !rows.isEmpty() && bounded.complete()) {
                return build(w, rep, publicTypeOf(rep, w), parentRef, ordinal,
                        firstOrdinal(rows), lastOrdinal(rows),
                        fragmentsOfRows(bounded.items(), "section", sectionPath), MODE_PARENT,
                        structureRefs(parentRef), true, provenance(rep, Map.of()), docSources);
            }
        }

        // window：同 parent_ref 下 ordinal 相邻 ±radius
        if ((MODE_WINDOW.equals(mode) || MODE_AUTO.equals(mode)) && parentRef != null
                && ordinal != null && windowRadius > 0) {
            List<SegmentTextRow> rows = windowRows.get(windowKey(w.snapshotId(), parentRef));
            if (rows != null && !rows.isEmpty()) {
                return build(w, rep, publicTypeOf(rep, w), parentRef, ordinal,
                        firstOrdinal(rows), lastOrdinal(rows),
                        fragmentsOfRows(rows, ordinal, sectionPath), MODE_WINDOW,
                        structureRefs(parentRef), true, provenance(rep, Map.of()), docSources);
            }
        }

        // exact：源表示 content_text（= raw segment 原文）
        if (rep != null && rep.getContentText() != null && !rep.getContentText().isEmpty()) {
            return build(w, rep, publicTypeOf(rep, w), parentRef, ordinal,
                    ordinal, ordinal,
                    List.of(new HydratedEvidence.EvidenceFragment("exact", rep.getContentText(),
                            sectionPath, null, w.candidate().targetRef())),
                    MODE_EXACT, structureRefs(parentRef), parentRef != null,
                    provenance(rep, Map.of()), docSources);
        }
        return null;
    }

    // ---- section：聚合直接子内容；整文 fits 且显式请求才 whole document ----

    private HydratedEvidence expandSection(Work w, String mode, int maxParentTokens,
                                           int maxDocumentTokens, UnitV2Row rep,
                                           Map<String, StructureNodeRow> nodes,
                                           Map<String, EvidenceDocumentRow> docSources,
                                           Map<String, Long> docTokens,
                                           Map<String, List<SegmentTextRow>> sectionRows,
                                           Map<String, List<SegmentTextRow>> documentRows) {
        StructureNodeRow node = nodes.get(nodeKey(w.snapshotId(), w.candidate().targetRef()));
        String sectionPath = TargetRefFormat.sectionPathOf(w.parsed());

        if (MODE_WHOLE_DOCUMENT.equals(mode)
                && documentFits(w.snapshotId(), docTokens, maxDocumentTokens)) {
            List<SegmentTextRow> rows = documentRows.get(w.snapshotId());
            if (rows != null && !rows.isEmpty()) {
                return build(w, rep, "section", parentRefOf(node), null, null, null,
                        fragmentsOfRows(rows, "document", sectionPath), MODE_WHOLE_DOCUMENT,
                        structureRefs(w.candidate().targetRef()), true,
                        provenance(rep, Map.of()), docSources);
            }
        }

        List<SegmentTextRow> rows = sectionRows.get(windowKey(w.snapshotId(), w.candidate().targetRef()));
        if (rows != null && !rows.isEmpty()) {
            Bounded<SegmentTextRow> bounded = boundedByTokens(rows, maxParentTokens);
            Map<String, Object> extra = bounded.complete()
                    ? Map.of() : Map.of("truncated", true, "childRows", rows.size());
            return build(w, rep, "section", parentRefOf(node), null,
                    firstOrdinal(bounded.items()), lastOrdinal(bounded.items()),
                    fragmentsOfRows(bounded.items(), "section", sectionPath), MODE_PARENT,
                    structureRefs(w.candidate().targetRef()), true,
                    provenance(rep, extra), docSources);
        }

        // 结构缺失但有源表示（section 表示自带有界直接内容）→ exact 回退
        if (rep != null && rep.getContentText() != null && !rep.getContentText().isEmpty()) {
            return build(w, rep, "section", parentRefOf(node), null, null, null,
                    List.of(new HydratedEvidence.EvidenceFragment("exact", rep.getContentText(),
                            sectionPath, null, w.candidate().targetRef())),
                    MODE_EXACT, structureRefs(w.candidate().targetRef()), node != null,
                    provenance(rep, Map.of("fallback", "section_representation")), docSources);
        }
        return null;
    }

    // ---- document：整文 fits 且 whole_document 才全量；否则有界前缀聚合 ----

    private HydratedEvidence expandDocument(Work w, String mode, int maxParentTokens,
                                            int maxDocumentTokens,
                                            Map<String, EvidenceDocumentRow> docSources,
                                            Map<String, List<SegmentTextRow>> documentRows) {
        List<SegmentTextRow> rows = documentRows.get(w.snapshotId());
        if (rows == null || rows.isEmpty()) {
            return null;
        }
        Bounded<SegmentTextRow> whole = boundedByTokens(rows, maxDocumentTokens);
        if (MODE_WHOLE_DOCUMENT.equals(mode) && whole.complete()
                && rows.size() <= MAX_DOCUMENT_ROWS) {
            return build(w, null, "document", null, null, null, null,
                    fragmentsOfRows(rows, "document", null), MODE_WHOLE_DOCUMENT,
                    structureRefs(w.candidate().targetRef()), true,
                    provenance(null, Map.of("documentTokens", tokensOf(rows))), docSources);
        }
        // auto / 超 budget：有界前缀（documentBounded 留痕）
        Bounded<SegmentTextRow> bounded = boundedByTokens(rows, maxParentTokens);
        return build(w, null, "document", null, null, null, null,
                fragmentsOfRows(bounded.items(), "section", null), MODE_PARENT,
                structureRefs(w.candidate().targetRef()), true,
                provenance(null, Map.of("truncated", true, "documentBounded", true,
                        "documentTokens", tokensOf(rows))), docSources);
    }

    // ---- table：有界整表；过大时 caption/header/命中区域 + truncated ----

    private HydratedEvidence expandTable(Work w, UnitV2Row rep,
                                         Map<String, EvidenceDocumentRow> docSources,
                                         Map<String, TableAssetRow> tableAssets,
                                         Map<String, List<TableCellRow>> tableCells) {
        String tableRef = TargetRefFormat.tableRefOf(w.parsed());
        String assetRef = TargetRefFormat.tableAssetRef(w.parsed().documentRef(), tableRef);
        TableAssetRow asset = tableAssets.get(windowKey(w.snapshotId(), tableRef));
        List<TableCellRow> cells = tableCells.get(windowKey(w.snapshotId(), tableRef));

        List<HydratedEvidence.EvidenceFragment> fragments = new ArrayList<>();
        appendCaptionFragment(fragments, rep);
        List<String> header = headerOf(asset, cells);
        if (!header.isEmpty()) {
            fragments.add(new HydratedEvidence.EvidenceFragment("header",
                    "表头: " + String.join(" / ", header), null, null, assetRef));
        }
        boolean truncated = false;
        if (cells != null && !cells.isEmpty()) {
            List<String> lines = renderTableLines(cells);
            truncated = asset != null && asset.getRowCount() != null
                    && lines.size() < asset.getRowCount();
            fragments.add(new HydratedEvidence.EvidenceFragment("row",
                    String.join("\n", lines), null, null, assetRef));
        } else if (rep != null && rep.getContentText() != null && !rep.getContentText().isEmpty()) {
            fragments.add(new HydratedEvidence.EvidenceFragment("exact", rep.getContentText(),
                    null, null, assetRef));
        } else {
            return null;
        }
        Map<String, Object> extra = truncated
                ? Map.of("truncated", true, "renderedRows", fragments.size()) : Map.of();
        return build(w, rep, "table", null, null, null, null, fragments, MODE_EXACT,
                structureRefs(assetRef), asset != null, provenance(rep, extra), docSources);
    }

    // ---- table_row：caption + 表头回填 + 命中行值；保留 cell/source refs ----

    private HydratedEvidence expandTableRow(Work w, UnitV2Row rep,
                                            Map<String, EvidenceDocumentRow> docSources,
                                            Map<String, TableAssetRow> tableAssets,
                                            Map<String, List<TableCellRow>> tableCells) {
        // 2026-09-01 用户反馈：行命中展示整表（表格才是可读单元；行只作命中定位）。
        // cells 已按 tableRef 批量在手——直接重建整表视图；evidenceType 升 "table"，
        // 同表多行/整表命中由 assemble 的同 ref 互含去重合并为一条。cells 缺失时
        // 回退旧行文本（自描述行 + 表头回填），保持"读得到"优先。
        String tableRef = TargetRefFormat.tableRefOf(w.parsed());
        Integer rowIndex = TargetRefFormat.rowIndexOf(w.parsed());
        String assetRef = TargetRefFormat.tableAssetRef(w.parsed().documentRef(), tableRef);
        TableAssetRow asset = tableAssets.get(windowKey(w.snapshotId(), tableRef));
        List<TableCellRow> cells = tableCells.get(windowKey(w.snapshotId(), tableRef));

        List<HydratedEvidence.EvidenceFragment> fragments = new ArrayList<>();
        appendCaptionFragment(fragments, rep);
        List<String> header = headerOf(asset, cells);
        if (!header.isEmpty()) {
            fragments.add(new HydratedEvidence.EvidenceFragment("header",
                    "表头: " + String.join(" / ", header), null, null, assetRef));
        }

        if (cells != null && !cells.isEmpty()) {
            // 整表重建：按行分组渲染"列=值"（与行片同构的自描述格式，保持一致性）
            Map<Integer, List<TableCellRow>> byRow = new TreeMap<>();
            for (TableCellRow c : cells) {
                if (!Boolean.TRUE.equals(c.getIsHeader())) {
                    byRow.computeIfAbsent(c.getRowIndex(), k -> new ArrayList<>()).add(c);
                }
            }
            for (Map.Entry<Integer, List<TableCellRow>> entry : byRow.entrySet()) {
                List<TableCellRow> rowCells = entry.getValue();
                rowCells.sort(java.util.Comparator.comparing(
                        TableCellRow::getColumnIndex, java.util.Comparator.nullsLast(Integer::compareTo)));
                List<String> values = new ArrayList<>(rowCells.size());
                for (TableCellRow c : rowCells) {
                    values.add((c.getColumnName() != null ? c.getColumnName() + "=" : "")
                            + (c.getValue() == null ? "" : c.getValue()));
                }
                String text = String.join(" | ", values);
                fragments.add(new HydratedEvidence.EvidenceFragment("row", text,
                        sectionPathOf(rep), null, assetRef));
            }
            Map<String, Object> extra = rowIndex != null ? Map.of("rowIndex", rowIndex) : Map.of();
            return build(w, rep, "table", tableRef, null, null, null, fragments, MODE_EXACT,
                    structureRefs(assetRef), asset != null, provenance(rep, extra), docSources);
        }

        // cells 缺失（如未过 readiness）→ 源表示行文本回退（旧行为）
        List<String> rowValues = new ArrayList<>();
        if (rowIndex != null && cells != null) {
            for (TableCellRow c : cells) {
                if (rowIndex.equals(c.getRowIndex()) && !Boolean.TRUE.equals(c.getIsHeader())) {
                    rowValues.add((c.getColumnName() != null ? c.getColumnName() + "=" : "")
                            + (c.getValue() == null ? "" : c.getValue()));
                }
            }
        }
        if (!rowValues.isEmpty()) {
            fragments.add(new HydratedEvidence.EvidenceFragment("row",
                    String.join(" | ", rowValues), sectionPathOf(rep), null, assetRef));
        } else if (rep != null && rep.getContentText() != null && !rep.getContentText().isEmpty()) {
            fragments.add(new HydratedEvidence.EvidenceFragment("row", rep.getContentText(),
                    sectionPathOf(rep), null, assetRef));
        } else {
            return null;
        }
        Map<String, Object> extra = rowIndex != null ? Map.of("rowIndex", rowIndex) : Map.of();
        return build(w, rep, "table_row", tableRef, null, null, null, fragments, MODE_EXACT,
                structureRefs(assetRef), asset != null, provenance(rep, extra), docSources);
    }

    // -------------------------------------------------------------------------
    // 共享构造/渲染
    // -------------------------------------------------------------------------

    private HydratedEvidence build(Work w, UnitV2Row rep, String evidenceType, String parentRef,
                                   Integer ordinal, Integer windowFrom, Integer windowTo,
                                   List<HydratedEvidence.EvidenceFragment> fragments,
                                   String expansionMode, List<String> structureRefs,
                                   boolean navigable, Map<String, Object> provenance,
                                   Map<String, EvidenceDocumentRow> docSources) {
        EvidenceDocumentRow doc = docSources.get(w.snapshotId());
        // section 展示：rep facets 的 section_path 优先（prose/table_row 均带），
        // section target 回退到 ref 自带的标题面包屑
        String section = sectionPathOf(rep);
        if (section == null && TargetRefFormat.SECTION.equals(w.parsed().targetType())) {
            section = TargetRefFormat.sectionPathOf(w.parsed());
        }
        HydratedEvidence.SourceProjection source = new HydratedEvidence.SourceProjection(
                doc != null ? doc.getKbName() : null,
                doc != null ? doc.getDocumentName() : null,
                doc != null ? doc.getRelativePath() : null,
                w.parsed().documentRef(),
                section, null);
        String content = joinFragments(fragments);
        return new HydratedEvidence(
                w.snapshotId(), w.canonicalId(), w.candidate().targetType(),
                w.candidate().targetRef(), evidenceType, w.parsed().documentRef(), parentRef,
                ordinal, windowFrom, windowTo, fragments, expansionMode, structureRefs, navigable,
                false, estimateTokens(content), source, provenance);
    }

    private Map<String, Object> provenance(UnitV2Row rep, Map<String, Object> extra) {
        Map<String, Object> p = new LinkedHashMap<>();
        if (rep != null) {
            p.put("sourceRepresentationType", rep.getRepresentationType());
            p.put("representationId", rep.getRepresentationId());
        }
        p.putAll(extra);
        return p;
    }

    /** 窗口片段：命中行 kind=exact，邻行 kind=window，按 ordinal 有序。 */
    private static List<HydratedEvidence.EvidenceFragment> fragmentsOfRows(
            List<SegmentTextRow> rows, Integer hitOrdinal, String sectionPath) {
        List<HydratedEvidence.EvidenceFragment> out = new ArrayList<>();
        for (SegmentTextRow r : rows) {
            String kind = hitOrdinal != null && hitOrdinal.equals(r.getOrdinal())
                    ? "exact" : "window";
            out.add(new HydratedEvidence.EvidenceFragment(kind, r.getRawText(), sectionPath,
                    null, r.getRef()));
        }
        return out;
    }

    private static List<HydratedEvidence.EvidenceFragment> fragmentsOfRows(
            List<SegmentTextRow> rows, String kind, String sectionPath) {
        List<HydratedEvidence.EvidenceFragment> out = new ArrayList<>();
        for (SegmentTextRow r : rows) {
            out.add(new HydratedEvidence.EvidenceFragment(kind, r.getRawText(), sectionPath,
                    null, r.getRef()));
        }
        return out;
    }

    private static void appendCaptionFragment(List<HydratedEvidence.EvidenceFragment> fragments,
                                              UnitV2Row rep) {
        if (rep != null && rep.getStructuralContext() != null
                && !rep.getStructuralContext().isEmpty()) {
            fragments.add(new HydratedEvidence.EvidenceFragment("caption",
                    rep.getStructuralContext(), sectionPathOf(rep), null, null));
        }
    }

    /** 表头：columns_json 优先，缺失回退该表的 header cells。 */
    private static List<String> headerOf(TableAssetRow asset, List<TableCellRow> cells) {
        if (asset != null && asset.getColumnsJson() != null && !asset.getColumnsJson().isBlank()) {
            List<String> cols = parseStringArray(asset.getColumnsJson());
            if (!cols.isEmpty()) {
                return cols;
            }
        }
        if (cells == null) {
            return List.of();
        }
        return cells.stream()
                .filter(c -> Boolean.TRUE.equals(c.getIsHeader()))
                .map(TableCellRow::getColumnName)
                .filter(n -> n != null && !n.isEmpty())
                .toList();
    }

    /** JSON 字符串数组解析（columns_json）；失败/非数组 → 空列表。 */
    private static List<String> parseStringArray(String json) {
        try {
            List<?> raw = JsonUtils.mapper().readValue(json, List.class);
            return raw.stream().map(String::valueOf).filter(s -> !s.isEmpty()).toList();
        } catch (Exception e) {
            return List.of();
        }
    }

    /** 表格行渲染：每行 "列=值 | 列=值"，按 row_index 有序。 */
    private static List<String> renderTableLines(List<TableCellRow> cells) {
        Map<Integer, List<String>> byRow = new LinkedHashMap<>();
        for (TableCellRow c : cells) {
            if (Boolean.TRUE.equals(c.getIsHeader()) || c.getRowIndex() == null) {
                continue;
            }
            byRow.computeIfAbsent(c.getRowIndex(), k -> new ArrayList<>())
                    .add((c.getColumnName() != null ? c.getColumnName() + "=" : "")
                            + (c.getValue() == null ? "" : c.getValue()));
        }
        List<String> lines = new ArrayList<>(byRow.size());
        for (List<String> parts : byRow.values()) {
            lines.add(String.join(" | ", parts));
        }
        return lines;
    }

    private static List<String> structureRefs(String... refs) {
        List<String> out = new ArrayList<>();
        for (String r : refs) {
            if (r != null && !r.isEmpty()) {
                out.add(r);
            }
        }
        return out;
    }

    /** token 预算内截取（≈ chars/4，与既有上下文预算口径一致）；返回是否完整。 */
    private static <T> Bounded<T> boundedByTokens(List<T> rows, int maxTokens) {
        if (rows == null || rows.isEmpty()) {
            return new Bounded<>(List.of(), true);
        }
        List<T> kept = new ArrayList<>();
        int used = 0;
        for (T row : rows) {
            int cost = row instanceof SegmentTextRow s
                    ? (s.getTokenCount() != null ? s.getTokenCount()
                        : estimateTokens(s.getRawText()))
                    : 0;
            if (used + cost > maxTokens && !kept.isEmpty()) {
                return new Bounded<>(List.copyOf(kept), false);
            }
            kept.add(row);
            used += cost;
        }
        return new Bounded<>(List.copyOf(kept), true);
    }

    private record Bounded<T>(List<T> items, boolean complete) {}

    static int estimateTokens(String text) {
        if (text == null || text.isEmpty()) {
            return 0;
        }
        return (text.length() + 3) / 4;
    }

    private static int tokensOf(List<SegmentTextRow> rows) {
        int total = 0;
        for (SegmentTextRow r : rows) {
            total += r.getTokenCount() != null ? r.getTokenCount() : estimateTokens(r.getRawText());
        }
        return total;
    }

    private static boolean documentFits(String snapshotId, Map<String, Long> docTokens,
                                        int maxDocumentTokens) {
        Long total = docTokens.get(snapshotId);
        return total != null && total <= maxDocumentTokens;
    }

    private static String publicTypeOf(UnitV2Row rep, Work w) {
        if (rep != null && rep.getRepresentationType() != null) {
            String mapped = com.coremasterkb.serving.operator.api.EvidenceTypeVocabulary
                    .toPublicType(rep.getRepresentationType());
            if (mapped != null) {
                return mapped;
            }
        }
        return "segment".equals(w.candidate().targetType()) ? "prose" : w.candidate().targetType();
    }

    private static String sectionPathOf(UnitV2Row rep) {
        if (rep == null || rep.getFacetsJson() == null) {
            return null;
        }
        Object path = JsonUtils.safeJsonParse(rep.getFacetsJson()).get("section_path");
        return path instanceof String s && !s.isEmpty() ? s : null;
    }

    private static String joinFragments(List<HydratedEvidence.EvidenceFragment> fragments) {
        StringBuilder sb = new StringBuilder();
        for (HydratedEvidence.EvidenceFragment f : fragments) {
            if (sb.length() > 0) {
                sb.append('\n');
            }
            sb.append(f.text() == null ? "" : f.text());
        }
        return sb.toString();
    }

    private static String snapshotOf(RetrievalCandidate c) {
        Object snapshot = c.metadata() != null ? c.metadata().get("snapshot_id") : null;
        return snapshot instanceof String s && !s.isEmpty() ? s : null;
    }

    /** 节点 mode + 请求级 expansion.mode 覆盖（显式请求优先；均非法则 auto）。 */
    private static String normalizedMode(Params params, String requestMode) {
        if (requestMode != null) {
            switch (requestMode) {
                case MODE_AUTO, MODE_EXACT, MODE_WINDOW, MODE_PARENT, MODE_WHOLE_DOCUMENT:
                    return requestMode;
                default: // 非法请求值不静默生效——按未覆盖处理
            }
        }
        String mode = params.getString("mode", MODE_AUTO);
        return switch (mode == null ? "" : mode) {
            case MODE_EXACT, MODE_WINDOW, MODE_PARENT, MODE_WHOLE_DOCUMENT -> mode;
            default -> MODE_AUTO;
        };
    }

    private static Map<String, String> skipOf(String canonical, String reason) {
        Map<String, String> m = new LinkedHashMap<>();
        m.put("canonical", canonical == null ? "" : canonical);
        m.put("reason", reason);
        return m;
    }

    private static String snapshotScopedKey(String snapshotId, String canonical) {
        return snapshotId + "|" + canonical;
    }

    private static String rowKey(String snapshotId, String canonical) {
        return snapshotId + "|" + canonical;
    }

    private static String nodeKey(String snapshotId, String ref) {
        return snapshotId + "|" + ref;
    }

    private static String windowKey(String snapshotId, String parentRef) {
        return snapshotId + "|" + parentRef;
    }

    private static String parentRefOf(StructureNodeRow node) {
        return node != null ? node.getParentRef() : null;
    }

    private static Integer firstOrdinal(List<SegmentTextRow> rows) {
        return rows != null && !rows.isEmpty() ? rows.get(0).getOrdinal() : null;
    }

    private static Integer lastOrdinal(List<SegmentTextRow> rows) {
        return rows != null && !rows.isEmpty() ? rows.get(rows.size() - 1).getOrdinal() : null;
    }

    private static <T> List<String> distinct(java.util.Collection<T> items,
                                             java.util.function.Function<T, String> key) {
        Set<String> seen = new LinkedHashSet<>();
        for (T item : items) {
            String k = key.apply(item);
            if (k != null && !k.isEmpty()) {
                seen.add(k);
            }
        }
        return new ArrayList<>(seen);
    }

    private record Work(RetrievalCandidate candidate, String snapshotId, String canonicalId,
                        TargetRefFormat.Parsed parsed) {}
}
