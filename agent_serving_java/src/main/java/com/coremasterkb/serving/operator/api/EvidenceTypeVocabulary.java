package com.coremasterkb.serving.operator.api;

import java.util.List;
import java.util.Map;

/**
 * A0-4（34 号）：对外 evidence type 统一词表——公开词与内部 representation_type 的
 * 唯一映射点（服务边界）。
 *
 * <p>规则：</p>
 * <ul>
 *   <li>对外只出现九个公开词：prose/section/document/table/table_row/list/code/
 *       formula/figure_caption——evidence[].type 输出用它，evidence_types filter 收它，
 *       Agent/前端无需知道内部 representation type；</li>
 *   <li>公开词与内部词的差异只有两对：{@code list ↔ list_group}、{@code code ↔ code_block}，
 *       转换只发生在本类（历史内部词作为输入别名兼容，不破已下发调用方）；</li>
 *   <li>{@code query_alias/summary_alias} 不在对外类型面——alias 只助召回，不作为证据，
 *       也不能作为 evidence_types 筛选值。</li>
 * </ul>
 */
public final class EvidenceTypeVocabulary {

    /** 对外公开词表（顺序即错误消息中的允许清单顺序）。 */
    public static final List<String> PUBLIC_TYPES = List.of(
            "prose", "section", "document", "table", "table_row",
            "list", "code", "formula", "figure_caption");

    /** 内部 representation_type → 公开词（仅存差异对，其余恒等）。 */
    private static final Map<String, String> INTERNAL_TO_PUBLIC = Map.of(
            "code_block", "code",
            "list_group", "list");

    /** 可对外公开的内部词全集（alias/未知类型不在公开面 → null，调用方按事实兜底）。 */
    private static final java.util.Set<String> PUBLIC_INTERNAL_TYPES = java.util.Set.of(
            "prose", "section", "document", "table", "table_row",
            "list_group", "code_block", "formula", "figure_caption");

    /** 公开词/兼容别名 → 内部 representation_type。 */
    private static final Map<String, String> TO_REPRESENTATION = Map.of(
            "code", "code_block",
            "code_block", "code_block",
            "list", "list_group",
            "list_group", "list_group");

    private EvidenceTypeVocabulary() {}

    /**
     * 内部 representation_type → 公开 evidence type；alias/未知 → {@code null}
     * （调用方按候选事实兜底，不在此猜——alias 不进入对外 evidence type 面）。
     */
    public static String toPublicType(String representationType) {
        if (representationType == null
                || !PUBLIC_INTERNAL_TYPES.contains(representationType)) {
            return null;
        }
        return INTERNAL_TO_PUBLIC.getOrDefault(representationType, representationType);
    }

    /**
     * 公开词（或历史内部别名）→ 内部 representation_type；不在值域（含 alias、空、null）
     * → {@code null}。调用方（filter 边界）对 null 按 typed 400 拒绝。
     */
    public static String toRepresentationType(String publicType) {
        if (publicType == null || publicType.isEmpty()) {
            return null;
        }
        String mapped = TO_REPRESENTATION.get(publicType);
        if (mapped != null) {
            return mapped;
        }
        // 恒等对（prose/section/document/table/table_row/formula/figure_caption）
        return PUBLIC_TYPES.contains(publicType) ? publicType : null;
    }
}
