package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmVersionMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;


import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.when;

/**
 * 批次6 后的范式目录：域绑定退役，只按发布质量分类。
 *
 * <p>可见 = 已发布且图以 assemble 终点（servable）；隐藏理由只剩
 * {@code not_servable}（collect 终点，评估专用）与 {@code version_missing}
 * （current_version 悬挂）。domain 参数兼容保留但被忽略——范式跨域通用。</p>
 */
@ExtendWith(MockitoExtension.class)
@DisplayName("ParadigmCatalogService（简化后）")
class ParadigmCatalogServiceTest {

    @Mock ParadigmService paradigmService;
    @Mock ParadigmVersionMapper versionMapper;

    private ParadigmCatalogService service;
    private final List<ParadigmEntity> published = new ArrayList<>();

    private static final String SERVABLE_GRAPH = """
            {"nodes":[{"nodeId":"asm","operatorType":"assemble"}],
             "output":{"nodeId":"asm","slot":"contextPack"}}""";

    private static final String COLLECT_GRAPH = """
            {"nodes":[{"nodeId":"out","operatorType":"collect"}],
             "output":{"nodeId":"out","slot":"candidates"}}""";

    @BeforeEach
    void setUp() {
        service = new ParadigmCatalogService(paradigmService, versionMapper);
        published.clear();
        when(paradigmService.listPublished()).thenAnswer(inv -> List.copyOf(published));
    }

    private ParadigmEntity entity(String id, String name) {
        ParadigmEntity e = new ParadigmEntity();
        e.setId(id);
        e.setName(name);
        e.setStatus("active");
        e.setCurrentVersion(1);
        return e;
    }

    private void withGraph(ParadigmEntity e, String graph) {
        published.add(e);
        ParadigmVersionEntity v = new ParadigmVersionEntity();
        v.setGraphJson(graph);
        lenient().when(versionMapper.selectByParadigmAndVersion(e.getId(), 1)).thenReturn(v);
    }

    @Test
    @DisplayName("servable 范式全部可见；domain 参数被忽略（跨域通用）")
    void servableParadigmsAreListedRegardlessOfDomainFilter() {
        withGraph(entity("pd-a", "生产检索"), SERVABLE_GRAPH);

        ParadigmCatalogService.Catalog c = service.build("some-domain", null);

        assertThat(c.paradigms()).hasSize(1);
        assertThat(c.paradigms().get(0).id()).isEqualTo("pd-a");
        assertThat(c.paradigms().get(0).version()).isEqualTo(1);
        assertThat(c.hidden()).isEmpty();
    }

    @Test
    @DisplayName("collect 终点的范式隐藏为 not_servable")
    void collectTerminatedParadigmIsHiddenAsNotServable() {
        withGraph(entity("pd-eval", "评估专用"), COLLECT_GRAPH);

        ParadigmCatalogService.Catalog c = service.build(null, "alice");

        assertThat(c.paradigms()).isEmpty();
        assertThat(c.hidden()).hasSize(1);
        assertThat(c.hidden().get(0).reason())
                .isEqualTo(ParadigmCatalogService.NOT_SERVABLE);
    }

    @Test
    @DisplayName("current_version 悬挂 → version_missing，不炸整个清单")
    void danglingVersionDegradesOneRow() {
        withGraph(entity("pd-good", "好的"), SERVABLE_GRAPH);
        ParadigmEntity dangling = entity("pd-broken", "坏的");
        published.add(dangling);
        when(versionMapper.selectByParadigmAndVersion("pd-broken", 1)).thenReturn(null);

        ParadigmCatalogService.Catalog c = service.build(null, null);

        assertThat(c.paradigms()).extracting(ParadigmCatalogService.Entry::id)
                .containsExactly("pd-good");
        assertThat(c.hidden()).extracting(ParadigmCatalogService.Hidden::reason)
                .containsExactly(ParadigmCatalogService.VERSION_MISSING);
    }
}
