package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.mapper.result.StructureNodeRow;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;

import java.util.List;

/**
 * A0-2（34 号 §P0）：结构节点的解析入口——document ref 双变体兼容。
 *
 * <p>历史快照的 document 节点 ref 是裸 {@code {doc}}（mining structure_projection
 * 修复前），新快照与 retrieval document target_ref 一致为 {@code {doc}#document}。
 * st_ 按任一身份编码都必须能解析到该快照里真实存在的 document 节点行：
 * 精确匹配 miss 时按 {@link #documentVariants} 回退；两个变体都不存在返回 null
 * （调用方按 invalid_ref 拒绝——绝不默默跳到错误文档）。</p>
 *
 * <p>非 document 形式（segment/section/table）只有自身一个候选——不做变体猜测。</p>
 */
final class StructureNodeLookup {

    private static final String DOCUMENT_SUFFIX = "#document";

    private StructureNodeLookup() {}

    /** document ref 的候选身份（首选在前）：{@code X#document ↔ X}；非 document 形式仅自身。 */
    static List<String> documentVariants(String ref) {
        if (ref == null || ref.isEmpty()) {
            return List.of();
        }
        if (ref.endsWith(DOCUMENT_SUFFIX)) {
            String bare = ref.substring(0, ref.length() - DOCUMENT_SUFFIX.length());
            return bare.isEmpty() ? List.of(ref) : List.of(ref, bare);
        }
        if (ref.indexOf('#') < 0) {
            return List.of(ref, ref + DOCUMENT_SUFFIX);
        }
        return List.of(ref);
    }

    /** 按 ref 解析节点；document 形式 miss 时按变体回退；全 miss → null。 */
    static StructureNodeRow find(StructureToolMapper mapper, String snapshotId, String ref) {
        StructureNodeRow node = mapper.selectNode(snapshotId, ref);
        if (node != null) {
            return node;
        }
        for (String alt : documentVariants(ref)) {
            if (alt.equals(ref)) {
                continue;
            }
            node = mapper.selectNode(snapshotId, alt);
            if (node != null) {
                return node;
            }
        }
        return null;
    }
}
