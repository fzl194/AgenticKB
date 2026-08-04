package com.coremasterkb.serving.operator.paradigm;

import com.zaxxer.hikari.HikariDataSource;
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
import java.util.List;

/**
 * Applies the paradigm DDL against the non-routed {@code defaultDataSource} on startup.
 * Idempotent ({@code IF NOT EXISTS} throughout) and best-effort: a connection failure (e.g. the
 * control DB not yet reachable) is logged, not fatal — paradigm endpoints will then surface a
 * clear error on first use.
 *
 * <p><b>Scripts are named explicitly in {@link #SCHEMA_SCRIPTS} and run in listed order.</b>
 * Nothing scans {@code db/operator/}, so dropping a new .sql file in that directory has no effect
 * until it is added here. (Same contract as {@code pg_schema.py}'s explicit path constants on the
 * Python side.)</p>
 */
@Component
public class ParadigmSchemaInitializer {

    private static final Logger log = LoggerFactory.getLogger(ParadigmSchemaInitializer.class);

    /** Ordered, explicitly named. Append here when adding a migration — see the class Javadoc. */
    private static final List<String> SCHEMA_SCRIPTS = List.of(
            "db/operator/001_operator_paradigm.sql",
            "db/operator/002_paradigm_domain_binding.sql");

    private final DataSource defaultDataSource;

    public ParadigmSchemaInitializer(@Qualifier("defaultDataSource") HikariDataSource defaultDataSource) {
        this.defaultDataSource = defaultDataSource;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void initSchema() {
        ResourceDatabasePopulator populator = new ResourceDatabasePopulator();
        for (String script : SCHEMA_SCRIPTS) {
            populator.addScript(new ClassPathResource(script));
        }
        populator.setContinueOnError(false);
        try (Connection conn = defaultDataSource.getConnection()) {
            populator.populate(conn);
            log.info("Operator paradigm schema ensured ({} scripts: operator_paradigm, "
                    + "operator_paradigm_version, domain binding)", SCHEMA_SCRIPTS.size());
        } catch (Exception e) {
            log.warn("Operator paradigm schema init skipped — control DB unavailable? ({}). "
                    + "Paradigm persistence endpoints will fail until the schema exists.", e.getMessage());
        }
    }
}
