package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmVersionMapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;

/**
 * Which published paradigms are usable for retrieval — and for the ones that are not, why.
 *
 * <p>批次6（域绑定退役）大简化：范式跨域通用，不再按 {@code bound_domain} 过滤，也不再做
 * 图内 KB 的匿名可读预检——MCP 自批次5 起必须持用户密钥，授权在执行时按真实身份判定，
 * 清单不再需要预判可读性。剩余的 hidden 理由只有发布质量问题（collect 终点不可服务、
 * current_version 悬挂），供范式管理页提示。</p>
 *
 * <p>只读控制库：无 DomainContext 编排（原 Phase 2 域库预检已删）。</p>
 */
@Service
public class ParadigmCatalogService {

    private static final Logger log = LoggerFactory.getLogger(ParadigmCatalogService.class);

    // ---- hidden reasons (stable strings: the UI maps them to human text) ----

    /** Terminates in {@code collect} — bare candidates, evaluation-only. */
    static final String NOT_SERVABLE = "not_servable";
    /** {@code current_version} points at a version row that does not exist. */
    static final String VERSION_MISSING = "version_missing";

    private final ParadigmService paradigmService;
    private final ParadigmVersionMapper versionMapper;
    private final ObjectMapper json = new ObjectMapper();

    public ParadigmCatalogService(ParadigmService paradigmService,
                                  ParadigmVersionMapper versionMapper) {
        this.paradigmService = paradigmService;
        this.versionMapper = versionMapper;
    }

    // =====================================================================================
    // Public API
    // =====================================================================================

    /**
     * @param domainFilter 兼容参数（批次6 起忽略——范式跨域通用）
     * @param username     兼容参数（忽略——授权在执行时判定）
     */
    public Catalog build(String domainFilter, String username) {
        List<Entry> visible = new ArrayList<>();
        List<Hidden> hidden = new ArrayList<>();
        for (ParadigmEntity p : paradigmService.listPublished()) {
            JsonNode graph = graphOf(p);
            if (graph == null) {
                hidden.add(new Hidden(p.getId(), p.getName(), VERSION_MISSING, List.of(), 0));
            } else if (!ParadigmGraphs.isServable(graph)) {
                hidden.add(new Hidden(p.getId(), p.getName(), NOT_SERVABLE, List.of(), 0));
            } else {
                visible.add(new Entry(p.getId(), p.getName(), p.getDescription(),
                        p.getCurrentVersion()));
            }
        }
        log.debug("[paradigm/catalog] visible={} hidden={}", visible.size(), hidden.size());
        return new Catalog(visible, hidden);
    }

    /**
     * The published graph, or null when the version row is gone.
     *
     * <p>Read straight from the version mapper rather than through
     * {@code ParadigmService.resolveExecutableGraph}: that would re-fetch the entity we already
     * hold, and it throws on a missing version. A catalog listing must degrade one row instead of
     * failing the whole request, so a dangling {@code current_version} becomes
     * {@link #VERSION_MISSING}.</p>
     */
    private JsonNode graphOf(ParadigmEntity p) {
        try {
            ParadigmVersionEntity v =
                    versionMapper.selectByParadigmAndVersion(p.getId(), p.getCurrentVersion());
            if (v == null || v.getGraphJson() == null) {
                log.warn("[paradigm/catalog] {} current_version={} has no version row",
                        p.getId(), p.getCurrentVersion());
                return null;
            }
            return json.readTree(v.getGraphJson());
        } catch (Exception e) {
            log.warn("[paradigm/catalog] unreadable graph for {}: {}", p.getId(), e.toString());
            return null;
        }
    }

    // =====================================================================================
    // Views
    // =====================================================================================

    /** @param paradigms usable for retrieval; @param hidden why the rest are not */
    public record Catalog(List<Entry> paradigms, List<Hidden> hidden) {}

    public record Entry(String id, String name, String description, int version) {}

    public record Hidden(String id, String name, String reason,
                         List<String> details, int undisclosedCount) {}
}
