package com.coremasterkb.serving.operator.paradigm;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

/**
 * Seeds the official default paradigm (固定 id {@link ParadigmService#OFFICIAL_DEFAULT_ID})
 * on startup — the "official" rung of the four-tier resolution ladder (16 号方案 §2).
 *
 * <p>Runs after {@link ParadigmSchemaInitializer} (higher {@code @Order} value = later).
 * Best-effort: control DB unavailable or name collision → log warn and retry next boot;
 * the resolve ladder simply reports "no paradigm configured" until the seed lands.</p>
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
            paradigmService.ensureOfficialDefault();
        } catch (Exception e) {
            log.warn("Official default paradigm seeding skipped ({}). Resolve will report "
                    + "'no paradigm configured' until it lands on a later boot.", e.getMessage());
        }
    }
}
