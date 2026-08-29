package com.coremasterkb.serving.operator.paradigm;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.core.annotation.Order;

/**
 * 批次8 R0 停 seed：官方默认图依赖已退役算子（weighted_rrf 等），seeding 停用（去
 * {@code @Component}，不再被 Spring 扫描）。R8 按 25 号文档的两套新检索预置重建本类。
 *
 * <p>原职责：startup 时 seed 官方默认范式（固定 id
 * {@link ParadigmService#OFFICIAL_DEFAULT_ID}），作为解析链的 "official" 层。停用后该层
 * 只读库中既有范式，resolve 在无预置时明确报"未配置检索范式"。</p>
 */
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
            paradigmService.ensureOfficialDefault();
        } catch (Exception e) {
            log.warn("Official default paradigm seeding skipped ({}). Resolve will report "
                    + "'no paradigm configured' until it lands on a later boot.", e.getMessage());
        }
    }
}
