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
        operator = new ScopeResolveOperator(assetRepository, kbAccessService);
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
}
