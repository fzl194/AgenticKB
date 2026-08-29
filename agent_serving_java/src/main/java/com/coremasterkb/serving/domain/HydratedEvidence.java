package com.coremasterkb.serving.domain;

import java.util.List;
import java.util.Map;

/**
 * {@code evidence_hydrate} 的输出元素（25 号 §5.2，批次8 R5）。内部契约：可以携带内部 id
 * 供 assemble 使用，但 assemble 必须映射为 opaque public ref 并剥离实现细节。
 *
 * <p>候选的 canonical target 回源读取后的类型化展开结果：exact span 与有序内容片段
 * （{@code orderedFragments}）、source projection、expansion mode、可导航 structure refs、
 * derived 标记、token 估算与内部 provenance（仅 debug 侧通道，正常 EvidenceResponse 不暴露）。</p>
 *
 * @param snapshotId         证据所在不可变快照（授权与 opaque ref 绑定键之一）
 * @param canonicalEvidenceId 融合键 = canonical target（hydrate 后即源证据身份）
 * @param targetType         canonical target 类型（segment/section/document/table/table_row/…）
 * @param targetRef          canonical target 引用（内部结构 ref，不直接对外）
 * @param evidenceType       公开协议类型（§5.3：prose|section|document|table|table_row|list|code|formula|figure_caption）
 * @param documentRef        证据所属文档 ref（内部；assemble 投影为 doc_opaque）
 * @param parentRef          容器 ref（segment 的父 section / table_row 的表 ref）；供 assemble 父子包含去重
 * @param ordinal            segment 型 target 的源 ordinal；供 assemble span 重叠去重
 * @param windowFrom/windowTo 同 parent_ref 下展开覆盖的 ordinal 区间（含端点）；null = 未展开窗口
 * @param orderedFragments   有序内容片段（按源 ordinal/结构顺序）
 * @param expansionMode      exact|window|parent|whole_document
 * @param structureRefs      可导航/可 inspect 的 structure/asset refs（内部；navigable 时才有内容）
 * @param navigable          是否确实可导航或可 inspect（§5.3：仅此时 assemble 才给 structure_ref）
 * @param derived            内容是否来自派生表示（alias 命中回源后为 false——内容即源证据）
 * @param tokenEstimate      token 估算（≈ chars/4，与既有预算口径一致）
 * @param source             source projection（kb/file/path/document/section/page，可得则填）
 * @param provenance         内部 provenance/debug（channel、representationRefs、expansion reason、skip 痕迹等）
 */
public record HydratedEvidence(
        String snapshotId,
        String canonicalEvidenceId,
        String targetType,
        String targetRef,
        String evidenceType,
        String documentRef,
        String parentRef,
        Integer ordinal,
        Integer windowFrom,
        Integer windowTo,
        List<EvidenceFragment> orderedFragments,
        String expansionMode,
        List<String> structureRefs,
        boolean navigable,
        boolean derived,
        int tokenEstimate,
        SourceProjection source,
        Map<String, Object> provenance
) {

    public HydratedEvidence {
        if (orderedFragments == null) orderedFragments = List.of();
        if (structureRefs == null) structureRefs = List.of();
        if (provenance == null) provenance = Map.of();
    }

    /** 拼接全部片段的完整内容文本（assemble 的 evidence content 来源）。 */
    public String contentText() {
        StringBuilder sb = new StringBuilder();
        for (EvidenceFragment f : orderedFragments) {
            if (sb.length() > 0) sb.append('\n');
            sb.append(f.text() == null ? "" : f.text());
        }
        return sb.toString();
    }

    /**
     * 有序内容片段。kind ∈ exact|window|section|document|caption|header|row；
     * sectionPath/page/structureRef 可得则填（内部定位信息，assemble 择要投影）。
     */
    public record EvidenceFragment(
            String kind,
            String text,
            String sectionPath,
            Integer page,
            String structureRef
    ) {}

    /** source projection：kb/file/path/document/section/page（可得则填）。 */
    public record SourceProjection(
            String knowledgeBase,
            String fileName,
            String relativePath,
            String documentRef,
            String section,
            Integer page
    ) {}
}
