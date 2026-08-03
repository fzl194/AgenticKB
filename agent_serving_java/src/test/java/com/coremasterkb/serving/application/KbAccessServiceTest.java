package com.coremasterkb.serving.application;

import com.coremasterkb.serving.mapper.KnowledgeBaseMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

@DisplayName("KbAccessService")
class KbAccessServiceTest {

    private KnowledgeBaseMapper mapper;
    private KbAccessService service;

    @BeforeEach
    void setUp() {
        mapper = mock(KnowledgeBaseMapper.class);
        service = new KbAccessService(mapper);
    }

    @Test
    @DisplayName("no kbIds requested — the DB is never touched")
    void emptyRequestSkipsLookup() {
        assertThat(service.authorize("cloud_core_network", List.of(), "alice")).isEmpty();
        assertThat(service.authorize("cloud_core_network", null, "alice")).isEmpty();
        verifyNoInteractions(mapper);
    }

    @Test
    @DisplayName("all requested KBs accessible — returns them normalized")
    void allAccessible() {
        when(mapper.selectAccessibleKbIds(anyString(), any(), any()))
                .thenReturn(List.of("kb2", "kb1"));

        assertThat(service.authorize("cloud_core_network", List.of("kb2", "kb1", "kb2"), "alice"))
                .containsExactly("kb1", "kb2");
    }

    @Test
    @DisplayName("one inaccessible KB fails the whole request rather than narrowing silently")
    void partialAccessIsRejected() {
        when(mapper.selectAccessibleKbIds(anyString(), any(), any()))
                .thenReturn(List.of("kb1"));

        assertThatThrownBy(() -> service.authorize("cloud_core_network", List.of("kb1", "kb2"), "alice"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_not_found");
    }

    @Test
    @DisplayName("nothing accessible -> kb_not_found, never an empty scope")
    void noneAccessible() {
        when(mapper.selectAccessibleKbIds(anyString(), any(), any())).thenReturn(List.of());

        assertThatThrownBy(() -> service.authorize("cloud_core_network", List.of("kb1"), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_not_found");
    }

    @Test
    @DisplayName("blank ids are dropped before the lookup")
    void normalizesBeforeLookup() {
        when(mapper.selectAccessibleKbIds(anyString(), any(), any())).thenReturn(List.of("kb1"));

        assertThat(service.authorize("cloud_core_network", List.of(" kb1 ", "", "  "), "alice"))
                .containsExactly("kb1");
        verify(mapper).selectAccessibleKbIds(eq("cloud_core_network"), eq(List.of("kb1")), eq("alice"));
    }

    @Test
    @DisplayName("anonymous callers reach the mapper with a null username, not a rejection")
    void anonymousIsPassedThrough() {
        when(mapper.selectAccessibleKbIds(anyString(), any(), any())).thenReturn(List.of("kb1"));

        assertThat(service.authorize("cloud_core_network", List.of("kb1"), null))
                .containsExactly("kb1");
        verify(mapper).selectAccessibleKbIds(anyString(), any(), isNull());
    }
}
