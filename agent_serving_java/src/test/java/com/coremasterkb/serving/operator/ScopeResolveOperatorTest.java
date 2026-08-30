package com.coremasterkb.serving.operator;

import com.coremasterkb.serving.application.KbAccessService;
import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.operator.core.ExecContext;
import com.coremasterkb.serving.operator.core.Params;
import com.coremasterkb.serving.operator.core.SlotValues;
import com.coremasterkb.serving.operator.operators.output.ScopeResolveOperator;
import com.coremasterkb.serving.repository.AssetRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

@DisplayName("ScopeResolveOperator")
class ScopeResolveOperatorTest {

    private static final ObjectMapper M = new ObjectMapper();

    private AssetRepository assetRepository;
    private KbAccessService kbAccessService;
    private ScopeResolveOperator operator;

    @BeforeEach
    void setUp() {
        assetRepository = mock(AssetRepository.class);
        kbAccessService = mock(KbAccessService.class);
        operator = new ScopeResolveOperator(assetRepository, kbAccessService, null);
        when(assetRepository.resolveActiveScope(anyString(), anyString(), any()))
                .thenReturn(new ActiveScope("rel1", "b1", List.of("snap1"), Map.of()));
    }

    private static Params params(String json) {
        try { return new Params(M.readTree(json)); } catch (Exception e) { throw new RuntimeException(e); }
    }

    private static ExecContext ctx(String username) {
        return new ExecContext("r", "cloud_core_network", "prod", false, username);
    }

    @Test
    @DisplayName("declares kbIds so the editor can render it")
    void paramSchemaDeclaresKbIds() {
        assertThat(operator.definition().paramSchemaJson()).contains("kbIds");
    }

    @Test
    @DisplayName("the x-widget UI hint does not affect param validation")
    void xWidgetIsIgnoredByValidation() throws Exception {
        // x-widget is a non-standard keyword. Draft-07 says unknown keywords are ignored, but if
        // the validator ever tightened, every scope_resolve node would fail to compile — so assert
        // it rather than trust it.
        var schemaNode = M.readTree(operator.definition().paramSchemaJson());
        assertThat(schemaNode.at("/properties/kbIds/x-widget").asText()).isEqualTo("kb-picker");

        var schema = JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V7).getSchema(schemaNode);
        assertThat(schema.validate(M.readTree("{\"kbIds\":[\"kb1\"]}"))).isEmpty();
        assertThat(schema.validate(M.readTree("{}"))).isEmpty();
        // Still enforces the declared type.
        assertThat(schema.validate(M.readTree("{\"kbIds\":\"kb1\"}"))).isNotEmpty();
    }

    @Test
    @DisplayName("no kbIds param — resolves the domain's release scope")
    void withoutKbIds() {
        when(kbAccessService.authorize(anyString(), any(), any())).thenReturn(List.of());

        var out = operator.execute(new SlotValues(), params("{}"), ctx("alice"));

        assertThat(out.getScope("scope").releaseId()).isEqualTo("rel1");
        verify(assetRepository).resolveActiveScope("cloud_core_network", "prod", List.of());
    }

    @Test
    @DisplayName("kbIds param is authorized against the caller, then narrows the scope")
    void withKbIdsAuthorizes() {
        when(kbAccessService.authorize(anyString(), any(), any())).thenReturn(List.of("kb1"));

        operator.execute(new SlotValues(), params("{\"kbIds\":[\"kb1\"]}"), ctx("alice"));

        verify(kbAccessService).authorize(eq("cloud_core_network"), eq(List.of("kb1")), eq("alice"));
        verify(assetRepository).resolveActiveScope("cloud_core_network", "prod", List.of("kb1"));
    }

    @Test
    @DisplayName("a stored graph cannot read a KB the caller may not open")
    void deniedKbAbortsBeforeResolving() {
        when(kbAccessService.authorize(anyString(), any(), any()))
                .thenThrow(new IllegalArgumentException("kb_not_found"));

        assertThatThrownBy(() ->
                operator.execute(new SlotValues(), params("{\"kbIds\":[\"kb1\"]}"), ctx("mallory")))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_not_found");

        verify(assetRepository, never()).resolveActiveScope(anyString(), anyString(), any());
    }

    // ---- R1: explicit hard filters channel (25 号 §6.2) ------------------------------------

    @Test
    @DisplayName("R1: supported filter keys pass through verbatim into ActiveScope.hardFilters")
    void requestFiltersPassThroughVerbatim() {
        when(kbAccessService.authorize(anyString(), any(), any())).thenReturn(List.of());
        ExecContext c = ctx("alice");
        Map<String, Object> filters = Map.of(
                "document_refs", List.of("doc-1"),
                "evidence_types", List.of("table_row"));
        c.setRequestFilters(filters);

        var out = operator.execute(new SlotValues(), params("{}"), c);

        assertThat(out.getScope("scope").hardFilters()).isEqualTo(filters);
        assertThat(c.attributes().get("hardFilterKeys"))
                .asInstanceOf(org.assertj.core.api.InstanceOfAssertFactories.LIST)
                .containsExactlyInAnyOrder("document_refs", "evidence_types");
    }

    @Test
    @DisplayName("27fix: unsupported filter keys are rejected explicitly (not silently ignored)")
    void unsupportedFilterKeysAreRejected() {
        when(kbAccessService.authorize(anyString(), any(), any())).thenReturn(List.of());
        ExecContext c = ctx("alice");
        c.setRequestFilters(Map.of(
                "document_refs", List.of("doc-1"),
                "relative_path_prefix", "规范/接入网"));

        // 静默忽略会让调用方以为过滤生效——必须显式 400（GlobalExceptionHandler
        // 映射 unsupported_scope_filter:*）。
        assertThatThrownBy(() -> operator.execute(new SlotValues(), params("{}"), c))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("unsupported_scope_filter:relative_path_prefix");

        c.setRequestFilters(Map.of("date_range", Map.of("from", "2026-01-01")));
        assertThatThrownBy(() -> operator.execute(new SlotValues(), params("{}"), c))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("unsupported_scope_filter:date_range");
    }

    @Test
    @DisplayName("27fix: opaque doc_/st_ refs decode to internal refs before SQL comparison")
    void opaqueRefsDecodeBeforePushdown() {
        when(kbAccessService.authorize(anyString(), any(), any())).thenReturn(List.of("kb1"));
        com.coremasterkb.serving.structure.StructureRefService refService =
                mock(com.coremasterkb.serving.structure.StructureRefService.class);
        when(refService.resolve(
                eq("doc_abc"), eq("cloud_core_network"), eq(List.of("kb1")), eq("alice")))
                .thenReturn(new com.coremasterkb.serving.evidence.EvidenceRefResolver.ResolvedRef(
                        "snap1",
                        com.coremasterkb.serving.evidence.EvidenceRefResolver.RefKind.DOCUMENT,
                        "manual.md"));
        ScopeResolveOperator decoding = new ScopeResolveOperator(
                assetRepository, kbAccessService, refService);

        ExecContext c = ctx("alice");
        Map<String, Object> filters = new java.util.LinkedHashMap<>();
        filters.put("document_refs", List.of("doc_abc", "plain-internal.md"));
        c.setRequestFilters(filters);

        var out = decoding.execute(new SlotValues(), params("{}"), c);

        // opaque ref 解码为内部 ref；明文内部 ref 原样透传（直连 API 契约）
        assertThat(out.getScope("scope").hardFilters().get("document_refs"))
                .isEqualTo(List.of("manual.md", "plain-internal.md"));
    }

    @Test
    @DisplayName("R1: no request filters — hardFilters stays empty (宽检索)")
    void noFiltersYieldsEmptyHardFilters() {
        when(kbAccessService.authorize(anyString(), any(), any())).thenReturn(List.of());
        ExecContext c = ctx("alice");

        var out = operator.execute(new SlotValues(), params("{}"), c);

        assertThat(out.getScope("scope").hardFilters()).isEmpty();
        assertThat(c.attributes()).doesNotContainKey("hardFilterKeys");
    }

    @Test
    @DisplayName("R1: filters are never inferred from the query text")
    void filtersAreNeverInferredFromQuery() {
        when(kbAccessService.authorize(anyString(), any(), any())).thenReturn(List.of());
        ExecContext c = ctx("alice");
        c.setQuery("2026年 规范/接入网 doc-1 的表格");

        var out = operator.execute(new SlotValues(), params("{}"), c);

        // Query mentions dates/paths/documents — none of that may become a hard filter.
        assertThat(out.getScope("scope").hardFilters()).isEmpty();
    }
}
