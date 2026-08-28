package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.mapper.KnowledgeBaseMapper;
import com.coremasterkb.serving.operator.engine.ParadigmCompiler;
import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmMapper;
import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmVersionMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 阶段 A（批次5）：库为中心的四层范式解析（16 号方案 §2）。
 * library（绑定一致且可用）> domain > official；降级留痕 degradedFrom。
 */
@ExtendWith(MockitoExtension.class)
class ParadigmServiceResolveForTest {

    @Mock ParadigmMapper paradigmMapper;
    @Mock ParadigmVersionMapper versionMapper;
    @Mock ParadigmCompiler compiler;
    @Mock KnowledgeBaseMapper knowledgeBaseMapper;

    ParadigmService service;

    @BeforeEach
    void setUp() {
        service = new ParadigmService(paradigmMapper, versionMapper, compiler, knowledgeBaseMapper);
    }

    private static ParadigmEntity published(String id) {
        ParadigmEntity e = new ParadigmEntity();
        e.setId(id);
        e.setName(id);
        e.setStatus("active");
        e.setCurrentVersion(2);
        return e;
    }

    @Test
    @DisplayName("kbIds 为空：跳过 library，直接 domain 层")
    void noKbIds_skipsLibrary() {
        ParadigmEntity domainDefault = published("pd-domain");
        when(paradigmMapper.selectDefaultByDomain("odn")).thenReturn(domainDefault);
        lenient().when(paradigmMapper.selectById(anyString())).thenReturn(null);

        ParadigmService.Resolution r = service.resolveFor("odn", List.of());

        assertThat(r).isNotNull();
        assertThat(r.source()).isEqualTo("domain");
        assertThat(r.degradedFrom()).isNull();
        verify(knowledgeBaseMapper, never()).selectDefaultParadigmIds(anyString(), anyList());
    }

    @Test
    @DisplayName("库级绑定一致且范式可用 → library")
    void consistentLibraryBindingWins() {
        when(knowledgeBaseMapper.selectDefaultParadigmIds("odn", List.of("kb1", "kb2")))
                .thenReturn(List.of("pd-lib"));
        when(paradigmMapper.selectById("pd-lib")).thenReturn(published("pd-lib"));
        lenient().when(paradigmMapper.selectDefaultByDomain("odn")).thenReturn(published("pd-domain"));

        ParadigmService.Resolution r = service.resolveFor("odn", List.of("kb1", "kb2"));

        assertThat(r.source()).isEqualTo("library");
        assertThat(r.paradigm().getId()).isEqualTo("pd-lib");
        assertThat(r.degradedFrom()).isNull();
    }

    @Test
    @DisplayName("库级绑定不一致（两个不同范式）→ 降级 domain，degradedFrom=library")
    void inconsistentLibraryBindingDegradesToDomain() {
        when(knowledgeBaseMapper.selectDefaultParadigmIds("odn", List.of("kb1", "kb2")))
                .thenReturn(List.of("pd-a", "pd-b"));
        when(paradigmMapper.selectDefaultByDomain("odn")).thenReturn(published("pd-domain"));

        ParadigmService.Resolution r = service.resolveFor("odn", List.of("kb1", "kb2"));

        assertThat(r.source()).isEqualTo("domain");
        assertThat(r.degradedFrom()).isEqualTo("library");
    }

    @Test
    @DisplayName("库级绑定的范式已归档 → 降级 domain，degradedFrom=library")
    void archivedLibraryParadigmDegrades() {
        when(knowledgeBaseMapper.selectDefaultParadigmIds("odn", List.of("kb1")))
                .thenReturn(List.of("pd-lib"));
        ParadigmEntity archived = published("pd-lib");
        archived.setStatus("archived");
        when(paradigmMapper.selectById("pd-lib")).thenReturn(archived);
        when(paradigmMapper.selectDefaultByDomain("odn")).thenReturn(published("pd-domain"));

        ParadigmService.Resolution r = service.resolveFor("odn", List.of("kb1"));

        assertThat(r.source()).isEqualTo("domain");
        assertThat(r.degradedFrom()).isEqualTo("library");
    }

    @Test
    @DisplayName("库全部未绑定 → 无降级标记，直接 domain")
    void unboundLibrariesGoStraightToDomain() {
        when(knowledgeBaseMapper.selectDefaultParadigmIds("odn", List.of("kb1", "kb2")))
                .thenReturn(List.of());
        when(paradigmMapper.selectDefaultByDomain("odn")).thenReturn(published("pd-domain"));

        ParadigmService.Resolution r = service.resolveFor("odn", List.of("kb1", "kb2"));

        assertThat(r.source()).isEqualTo("domain");
        assertThat(r.degradedFrom()).isNull();
    }

    @Test
    @DisplayName("domain 与库级皆无 → official 兜底")
    void officialIsTheLastRung() {
        when(knowledgeBaseMapper.selectDefaultParadigmIds("odn", List.of("kb1")))
                .thenReturn(List.of());
        when(paradigmMapper.selectDefaultByDomain("odn")).thenReturn(null);
        when(paradigmMapper.selectById(ParadigmService.OFFICIAL_DEFAULT_ID))
                .thenReturn(published(ParadigmService.OFFICIAL_DEFAULT_ID));

        ParadigmService.Resolution r = service.resolveFor("odn", List.of("kb1"));

        assertThat(r.source()).isEqualTo("official");
    }

    @Test
    @DisplayName("四层全无 → null（调用方明确报未配置，不回落 legacy）")
    void nothingResolvableReturnsNull() {
        when(knowledgeBaseMapper.selectDefaultParadigmIds("odn", List.of("kb1")))
                .thenReturn(List.of("pd-lib"));   // 有绑定但范式查不到 → 降级
        when(paradigmMapper.selectById("pd-lib")).thenReturn(null);
        when(paradigmMapper.selectDefaultByDomain("odn")).thenReturn(null);
        when(paradigmMapper.selectById(ParadigmService.OFFICIAL_DEFAULT_ID)).thenReturn(null);

        assertThat(service.resolveFor("odn", List.of("kb1"))).isNull();
    }
}
