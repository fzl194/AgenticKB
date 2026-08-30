package com.coremasterkb.serving.operator.api;

import com.coremasterkb.serving.operator.core.ExecContext;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 批次8 R8（25 号 §7.1）：{@code /paradigm/{id}/search} 显式 within/filters/top_k/expansion
 * 的入参契约——显式传入 = hard filter（R1 requestFilters 通道）；非法值 400 而非静默忽略。
 */
@DisplayName("ParadigmRequests filters/top_k/expansion")
class ParadigmRequestsFiltersTest {

    private static final ObjectMapper M = new ObjectMapper();

    @Test
    @DisplayName("within + filters 平铺合并为 requestFilters（显式 = hard filter）")
    void withinAndFiltersMerged() throws Exception {
        var body = M.readTree("""
                {"query": "q", "within": {"document_refs": ["doc:/a"], "section_refs": ["st_x"]},
                 "filters": {"asset_types": ["table"], "evidence_types": ["table_row"]}}
                """);
        var args = ParadigmRequests.toRunArgs(body, "alice");

        assertThat(args.filters()).containsEntry("document_refs", java.util.List.of("doc:/a"))
                .containsEntry("section_refs", java.util.List.of("st_x"))
                .containsEntry("asset_types", java.util.List.of("table"))
                .containsEntry("evidence_types", java.util.List.of("table_row"));
    }

    @Test
    @DisplayName("29fix R06a: 错误值类型/枚举/ref kind → typed 400（不退化为宽检索）")
    void malformedFilterValuesRejected() throws Exception {
        // 非数组值：此前 stringValues 静默返回空 → 宽检索
        assertThatThrownBy(() -> ParadigmRequests.toRunArgs(M.readTree(
                "{\"query\": \"q\", \"filters\": {\"asset_types\": \"table\"}}"), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("filter_value_invalid:asset_types");

        // ref kind 错配：document_refs 不接受 st_
        assertThatThrownBy(() -> ParadigmRequests.toRunArgs(M.readTree(
                "{\"query\": \"q\", \"within\": {\"document_refs\": [\"st_abcd\"]}}"), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("filter_value_invalid:document_refs");

        // evidence_types 枚举外值
        assertThatThrownBy(() -> ParadigmRequests.toRunArgs(M.readTree(
                "{\"query\": \"q\", \"filters\": {\"evidence_types\": [\"vector\"]}}"), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("filter_value_invalid:evidence_types");

        // 空串元素
        assertThatThrownBy(() -> ParadigmRequests.toRunArgs(M.readTree(
                "{\"query\": \"q\", \"filters\": {\"document_refs\": [\" \"]}}"), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("filter_value_invalid:document_refs");
    }

    @Test
    @DisplayName("27fix: 未支持 filter 键在请求边界显式 400（不静默忽略）")
    void unsupportedFilterKeysRejectedAtBoundary() throws Exception {
        assertThatThrownBy(() -> ParadigmRequests.toRunArgs(M.readTree(
                "{\"query\": \"q\", \"filters\": {\"date_range\": {\"from\": \"2026-01-01\"}}}"), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("unsupported_scope_filter:date_range");

        assertThatThrownBy(() -> ParadigmRequests.toRunArgs(M.readTree(
                "{\"query\": \"q\", \"within\": {\"include_descendants\": true}}"), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("unsupported_scope_filter:include_descendants");
    }

    @Test
    @DisplayName("未传 within/filters → 空 map（宽检索，不加任何隐式约束）")
    void absentFiltersStayEmpty() throws Exception {
        var args = ParadigmRequests.toRunArgs(M.readTree("{\"query\": \"q\"}"), null);
        assertThat(args.filters()).isEmpty();
    }

    @Test
    @DisplayName("top_k：正整数透传；非法 → IllegalArgumentException(400)")
    void topKValidation() throws Exception {
        assertThat(ParadigmRequests.toRunArgs(
                M.readTree("{\"query\": \"q\", \"top_k\": 30}"), null).topK()).isEqualTo(30);
        assertThat(ParadigmRequests.toRunArgs(
                M.readTree("{\"query\": \"q\"}"), null).topK()).isNull();

        assertThatThrownBy(() -> ParadigmRequests.toRunArgs(
                M.readTree("{\"query\": \"q\", \"top_k\": -1}"), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("top_k_invalid");
        assertThatThrownBy(() -> ParadigmRequests.toRunArgs(
                M.readTree("{\"query\": \"q\", \"top_k\": \"20\"}"), null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("expansion.mode：白名单透传；非法 → IllegalArgumentException(400)")
    void expansionValidation() throws Exception {
        assertThat(ParadigmRequests.toRunArgs(
                M.readTree("{\"query\": \"q\", \"expansion\": {\"mode\": \"parent\"}}"), null)
                .expansion()).isEqualTo("parent");
        assertThat(ParadigmRequests.toRunArgs(
                M.readTree("{\"query\": \"q\"}"), null).expansion()).isNull();

        assertThatThrownBy(() -> ParadigmRequests.toRunArgs(
                M.readTree("{\"query\": \"q\", \"expansion\": {\"mode\": \"everything\"}}"), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("expansion_invalid");
    }

    @Test
    @DisplayName("ExecContext.resolveTopK：显式请求优先并 clamp；未传用节点参数")
    void resolveTopKOverridesNodeValue() {
        ExecContext ctx = new ExecContext("r", "odn", "default", false);
        assertThat(ctx.resolveTopK(20, 200)).isEqualTo(20);

        ctx.setRequestTopK(30);
        assertThat(ctx.resolveTopK(20, 200)).isEqualTo(30);
        ctx.setRequestTopK(80);
        assertThat(ctx.resolveTopK(20, 50)).isEqualTo(50); // clamp 到算子上限
        assertThat(ctx.resolveTopK(20, 200)).isEqualTo(80); // 大上限内不 clamp

        ctx.setRequestTopK(null);
        assertThat(ctx.resolveTopK(20, 200)).isEqualTo(20);
    }

    @Test
    @DisplayName("RunArgs 透传链：withKbIds/withParadigm 不丢 filters/topK/expansion")
    void runArgsWithersCarryNewFields() throws Exception {
        var args = ParadigmRequests.toRunArgs(
                M.readTree("{\"query\": \"q\", \"top_k\": 5, \"expansion\": {\"mode\": \"exact\"},"
                        + " \"within\": {\"section_refs\": [\"st_x\"]}}"), "alice")
                .withKbIds(java.util.List.of("kb-1"))
                .withParadigm("pd-1", 2);

        assertThat(args.kbIds()).containsExactly("kb-1");
        assertThat(args.paradigmId()).isEqualTo("pd-1");
        assertThat(args.paradigmVersion()).isEqualTo(2);
        assertThat(args.topK()).isEqualTo(5);
        assertThat(args.expansion()).isEqualTo("exact");
        assertThat((Map<String, Object>) args.filters()).containsKey("section_refs");
    }
}
