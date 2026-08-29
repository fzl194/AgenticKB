package com.coremasterkb.serving.observability;

import com.coremasterkb.serving.domainpack.DomainSchemaEnsurer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.sql.Connection;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.Set;

/**
 * Creates the serving-owned runtime tables ({@code serving_query_logs}) in every database this
 * service can route to.
 *
 * <p>Unlike {@code ParadigmSchemaInitializer} — whose tables are global control
 * state pinned to the non-routed default DataSource — these are written
 * through the {@code @Primary} DomainRoutingDataSource, so a domain carrying its
 * own inline {@code database:} block writes them into <em>that</em> database.
 * Hence this runs both at startup (default DataSource) and via
 * {@link DomainSchemaEnsurer} on each per-domain pool creation.</p>
 *
 * <p>批次8 R0：语义缓存表（{@code serving_query_cache}）随固定链删除（25号 §11.1）。</p>
 *
 * <p>Each script is applied independently and all failures are swallowed: query logging already
 * degrades silently when its table is missing, and a schema problem must never block
 * retrieval.</p>
 */
@Component
public class ServingRuntimeSchemaInitializer implements DomainSchemaEnsurer {

    private static final Logger log = LoggerFactory.getLogger(ServingRuntimeSchemaInitializer.class);

    private static final String[] SCRIPTS = {
            "db/serving/001_serving_query_logs.sql",
    };

    private final DataSource defaultDataSource;

    /**
     * DataSources already fully processed, by identity — unconfigured domains all resolve to
     * the same default DataSource and would otherwise re-run the DDL on every new domain.
     * A DataSource is only recorded once every script succeeded, so a DB that was down at
     * startup gets another attempt when the next domain resolves to it.
     */
    private final Set<DataSource> ensured =
            Collections.newSetFromMap(Collections.synchronizedMap(new IdentityHashMap<>()));

    public ServingRuntimeSchemaInitializer(@Qualifier("defaultDataSource") DataSource defaultDataSource) {
        this.defaultDataSource = defaultDataSource;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void initDefaultSchema() {
        ensure(defaultDataSource, "__default__");
    }

    @Override
    public void ensure(DataSource dataSource, String domain) {
        if (dataSource == null || ensured.contains(dataSource)) {
            return;
        }
        boolean allApplied = true;
        for (String script : SCRIPTS) {
            allApplied &= apply(dataSource, domain, script);
        }
        if (allApplied) {
            ensured.add(dataSource);
        }
    }

    private boolean apply(DataSource dataSource, String domain, String script) {
        ResourceDatabasePopulator populator = new ResourceDatabasePopulator();
        populator.addScript(new ClassPathResource(script));
        populator.setContinueOnError(false);
        try (Connection conn = dataSource.getConnection()) {
            populator.populate(conn);
            log.info("Serving runtime schema ensured for domain '{}': {}", domain, script);
            return true;
        } catch (Exception e) {
            log.warn("Serving runtime schema init skipped for domain '{}' ({}): {}. "
                    + "The feature backed by this table stays degraded until it exists.",
                    domain, script, e.getMessage());
            return false;
        }
    }
}
