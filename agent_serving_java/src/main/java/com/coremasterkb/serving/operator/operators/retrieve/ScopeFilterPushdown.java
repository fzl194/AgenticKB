package com.coremasterkb.serving.operator.operators.retrieve;

import com.coremasterkb.serving.domain.ActiveScope;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 把 {@code ActiveScope.hardFilters}（R1 透传的显式 hard constraints，25 号 §7.1）映射为
 * 召回算子 Top-K 前可下推的 SQL 约束参数（批次8 R2）。
 *
 * <p>映射是<b>确定性的白名单映射</b>，不是推断——只有 hardFilters 里显式出现的键才生成约束：</p>
 * <ul>
 *   <li>{@code document_refs} → facets_json {@code @>} 参数化 JSONB（每个 ref 一条
 *       {@code {"document":"<ref>"}}，facets.document 是标量，多值用 OR 语义由 SQL foreach 承担）；</li>
 *   <li>{@code evidence_types} → {@code representation_type IN (...)}（§5.3 类型枚举）；</li>
 *   <li>{@code asset_types} → {@code content_type IN (...)}（源内容类型，facets.content_type 同源）；</li>
 *   <li>{@code section_refs} → {@code section_ref IN (...)}（A2：单元章节归属物理列，
 *       覆盖章节内全部正文/表格/列表表示；{@code section_ref IS NULL} 的存量行回落
 *       {@code target_ref IN}（37 号 D4 的旧语义，回填后自然消失）；
 *       {@code section_scope=descendants} 时两侧均改为章节闭包（SQL 内递归 CTE，
 *       FTS/dense 共用同一 predicate——越界率=0 的结构性保证）。</li>
 * </ul>
 *
 * <p>{@code relative_path_prefix/date_range} 等当前 v2 表无可下推列的键原样保留在 hardFilters
 * 中（不丢弃、不擅自降级），待后续波次补列或由结构工具消费。</p>
 */
public final class ScopeFilterPushdown {

    private final List<String> documentJsonParams;
    private final List<String> representationTypes;
    private final List<String> contentTypes;
    private final List<String> targetRefs;
    private final boolean sectionScopeDescendants;

    private ScopeFilterPushdown(
            List<String> documentJsonParams, List<String> representationTypes,
            List<String> contentTypes, List<String> targetRefs,
            boolean sectionScopeDescendants) {
        this.documentJsonParams = documentJsonParams;
        this.representationTypes = representationTypes;
        this.contentTypes = contentTypes;
        this.targetRefs = targetRefs;
        this.sectionScopeDescendants = sectionScopeDescendants;
    }

    /** No-filter scope（宽检索）：所有约束为空。 */
    public static ScopeFilterPushdown none() {
        return new ScopeFilterPushdown(List.of(), List.of(), List.of(), List.of(), false);
    }

    /** 从 ActiveScope 的 hardFilters 构造（null scope / 空 filters → none）。 */
    public static ScopeFilterPushdown from(ActiveScope scope) {
        if (scope == null) return none();
        return fromFilters(scope.hardFilters());
    }

    /** 从显式 filter map 构造；未知键忽略（透传留在 ActiveScope，不在这里猜语义）。 */
    public static ScopeFilterPushdown fromFilters(Map<String, Object> hardFilters) {
        if (hardFilters == null || hardFilters.isEmpty()) return none();
        return new ScopeFilterPushdown(
                documentJsonParams(hardFilters.get("document_refs")),
                stringValues(hardFilters.get("evidence_types")),
                stringValues(hardFilters.get("asset_types")),
                stringValues(hardFilters.get("section_refs")),
                "descendants".equals(hardFilters.get("section_scope")));
    }

    /** facets_json @> 参数化 JSONB containment 串列表（{"document":"<ref>"}）。 */
    public List<String> documentJsonParams() { return documentJsonParams; }

    /** representation_type IN (...) 值列表（evidence_types）。 */
    public List<String> representationTypes() { return representationTypes; }

    /** content_type IN (...) 值列表（asset_types）。 */
    public List<String> contentTypes() { return contentTypes; }

    /** section_refs 值列表（section_ref / target_ref / 闭包种子的同一入参）。 */
    public List<String> targetRefs() { return targetRefs; }

    /** A2：section_scope=descendants（闭包展开；false = exact 或未传）。 */
    public boolean sectionScopeDescendants() { return sectionScopeDescendants; }

    public boolean isEmpty() {
        return documentJsonParams.isEmpty() && representationTypes.isEmpty()
                && contentTypes.isEmpty() && targetRefs.isEmpty();
    }

    private static List<String> documentJsonParams(Object refs) {
        List<String> values = stringValues(refs);
        List<String> params = new ArrayList<>(values.size());
        for (String ref : values) {
            params.add("{\"document\":" + jsonQuote(ref) + "}");
        }
        return List.copyOf(params);
    }

    private static List<String> stringValues(Object value) {
        if (!(value instanceof List<?> list) || list.isEmpty()) return List.of();
        List<String> out = new ArrayList<>();
        for (Object v : list) {
            if (v != null) {
                String s = v.toString().trim();
                if (!s.isEmpty()) out.add(s);
            }
        }
        return List.copyOf(out);
    }

    /** JSON 字符串字面量（仅转义反斜杠与双引号——ref 值域为标识符，控制字符不出现）。 */
    static String jsonQuote(String value) {
        return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }
}
