package com.coremasterkb.serving.operator.paradigm;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

/**
 * 批次8 R8 恢复启用（25 号 §9）：startup 时 seed 两套官方检索预置——
 * {@link ParadigmService#OFFICIAL_LEXICAL_ID system-lexical-retrieval}（关键词）与
 * {@link ParadigmService#OFFICIAL_DEFAULT_ID system-hybrid-retrieval}（标准混合，
 * 官方默认）。幂等：固定 id，缺失建+发布、未发布补发布、用户归档不复活；
 * R0 退役的旧官方图 id（system-official-default）不复活。
 *
 * <p>R0 曾整体停用本类（旧图依赖已退役算子 weighted_rrf）；R8 的新预置全部使用
 * 现存 7+1 算子目录（scope_resolve/query_embed/fts/dense_vector/rrf/model_rerank/
 * evidence_hydrate/assemble），终点 {@code evidenceResponse}。</p>
 */
@Component
public class OfficialParadigmSeeder {

    private static final Logger log = LoggerFactory.getLogger(OfficialParadigmSeeder.class);

    private final ParadigmService paradigmService;

    public OfficialParadigmSeeder(ParadigmService paradigmService) {
        this.paradigmService = paradigmService;
    }

    @Order(100)
    @EventListener(ApplicationReadyEvent.class)
    public void seed() {
        try {
            paradigmService.ensureOfficialParadigms();
        } catch (Exception e) {
            log.warn("Official paradigm seeding skipped ({}). Resolve will report "
                    + "'no paradigm configured' until it lands on a later boot.", e.getMessage());
        }
    }
}
