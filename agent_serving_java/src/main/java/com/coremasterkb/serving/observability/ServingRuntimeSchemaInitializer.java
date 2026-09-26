package com.coremasterkb.serving.observability;

import com.coremasterkb.serving.domainpack.DomainSchemaEnsurer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.Set;

/**
 * Read-only database schema contract validator for every routable DataSource.
 *
 * <p>All CREATE/ALTER/DROP work is owned by the container migration command before services
 * restart.  A missing or stale migration ledger is fatal: silently serving against a partial
 * schema would be more dangerous than refusing readiness.</p>
 */
@Component
public class ServingRuntimeSchemaInitializer implements DomainSchemaEnsurer {

    private static final Logger log = LoggerFactory.getLogger(ServingRuntimeSchemaInitializer.class);

    private static final String LEDGER_TABLE = "cmkb_schema_migrations";
    private static final String EXPECTED_SCHEMA_VERSION = "2026.09.domain-role-v1";
    private static final String EXPECTED_SCHEMA_CHECKSUM =
            "21ef7bbf68e6f92fa9e45c05725b3ef5e0e7308d758fea957e7df03ce06443ed";
    private static final String EXPECTED_SCHEMA_MARKER =
            "schema/" + EXPECTED_SCHEMA_VERSION;

    private final DataSource defaultDataSource;

    /**
     * DataSources already validated, by identity — all domains currently share the default
     * DataSource, so checking it once is sufficient.
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
        validate(dataSource, domain);
        ensured.add(dataSource);
    }

    private void validate(DataSource dataSource, String domain) {
        try (Connection conn = dataSource.getConnection()) {
            if (!ledgerExists(conn)) {
                throw new IllegalStateException("database migration required: ledger missing");
            }
            if (!expectedVersionExists(conn)) {
                throw new IllegalStateException(
                        "database migration required: expected schema " + EXPECTED_SCHEMA_VERSION);
            }
            log.info("Serving database schema validated for domain '{}': {}",
                    domain, EXPECTED_SCHEMA_VERSION);
        } catch (Exception e) {
            throw new IllegalStateException(
                    "Database schema contract failed for domain '" + domain + "': "
                            + e.getMessage(), e);
        }
    }

    private boolean ledgerExists(Connection conn) throws Exception {
        try (PreparedStatement statement = conn.prepareStatement(
                "SELECT to_regclass(?) IS NOT NULL")) {
            statement.setString(1, "public." + LEDGER_TABLE);
            try (ResultSet result = statement.executeQuery()) {
                return result.next() && result.getBoolean(1);
            }
        }
    }

    private boolean expectedVersionExists(Connection conn) throws Exception {
        try (PreparedStatement statement = conn.prepareStatement(
                "SELECT EXISTS (SELECT 1 FROM " + LEDGER_TABLE
                        + " WHERE migration_id = ? AND checksum = ?"
                        + " AND details_json->>'schema_version' = ?)")) {
            statement.setString(1, EXPECTED_SCHEMA_MARKER);
            statement.setString(2, EXPECTED_SCHEMA_CHECKSUM);
            statement.setString(3, EXPECTED_SCHEMA_VERSION);
            try (ResultSet result = statement.executeQuery()) {
                return result.next() && result.getBoolean(1);
            }
        }
    }
}
