package com.coremasterkb.serving.domainpack;

import com.coremasterkb.serving.AgentServingApplication;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.core.env.Environment;
import org.springframework.test.context.ActiveProfiles;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assumptions.assumeTrue;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

/**
 * Routing contract for {@link DomainPoolManager}: which domains get a dedicated pool, which reuse
 * the default DataSource, and what happens when a configured database is unreachable.
 *
 * <p>Rewritten against the mechanism that actually exists. The previous version targeted the
 * pre-unification {@code COREMASTERKB_DB_*} environment variables, which
 * {@code DomainPoolManager} no longer reads at all — it resolves the registry's inline
 * {@code database:} block. The result was one permanent failure (asserting cloud_core_network
 * falls back to the default DataSource, untrue once the shipped registry gained inline blocks)
 * plus four tests that skipped forever on {@code assumeTrue(envVar != null)}.</p>
 *
 * <p>It also opened a real pool to the production host in {@code domain_registry.yaml} on every
 * run — {@code ensureSchema} DDL included — because it called {@code getDataSource} with a real
 * domain name. This version builds its own {@link DomainPoolManager} over a stubbed
 * {@link DomainRegistry} and the local test database, so no test reaches production, and the
 * two-arg constructor's {@code DomainSchemaEnsurer.NOOP} means no DDL runs anywhere.</p>
 */
@SpringBootTest(classes = AgentServingApplication.class)
@ActiveProfiles("test-pg")
@Tag("pg-integration")
@DisplayName("DomainPoolManager IT — per-domain routing")
class DomainRoutingIT {

    @Autowired
    @Qualifier("defaultDataSource")
    private DataSource defaultDataSource;

    @Autowired
    private Environment environment;

    private DomainRegistry registry;
    private DomainPoolManager poolManager;
    private DatabaseConfig localDb;

    @BeforeEach
    void setUp() {
        try (Connection conn = defaultDataSource.getConnection()) {
            assumeTrue(conn.isValid(3), "Default PostgreSQL not reachable — skipping");
        } catch (SQLException e) {
            assumeTrue(false, "Default PostgreSQL not reachable — skipping");
        }

        localDb = new DatabaseConfig(
                environment.getProperty("spring.datasource.url"), null, null, null,
                environment.getProperty("spring.datasource.username"),
                environment.getProperty("spring.datasource.password"),
                null, null, 1, 2);

        registry = mock(DomainRegistry.class);
        when(registry.findEntry(anyString())).thenReturn(Optional.empty());
        poolManager = new DomainPoolManager(registry, defaultDataSource);
    }

    private void stubDomain(String domain, DatabaseConfig db) {
        when(registry.findEntry(eq(domain)))
                .thenReturn(Optional.of(new DomainRegistryEntry(domain, true, db, "prod")));
    }

    // ------------------------------------------------------------------ fallback to default

    @Test
    @DisplayName("domain absent from the registry reuses the default DataSource")
    void unknownDomainReusesDefault() {
        assertThat(poolManager.getDataSource("no_such_domain_" + UUID.randomUUID()))
                .isSameAs(defaultDataSource);
    }

    @Test
    @DisplayName("domain with no inline database block reuses the default DataSource")
    void domainWithoutDatabaseReusesDefault() {
        stubDomain("d_nodb", null);

        assertThat(poolManager.getDataSource("d_nodb")).isSameAs(defaultDataSource);
    }

    @Test
    @DisplayName("half-filled database block is not usable and reuses the default DataSource")
    void unusableDatabaseReusesDefault() {
        // isUsable() needs a jdbcUrl, or host AND dbname. A host alone must not build a pool.
        stubDomain("d_partial", new DatabaseConfig(
                null, "localhost", 5432, null, "u", "p", null, null, 1, 2));

        assertThat(poolManager.getDataSource("d_partial")).isSameAs(defaultDataSource);
    }

    // ------------------------------------------------------------------ dedicated pools

    @Test
    @DisplayName("domain with a usable database block gets its own working pool")
    void configuredDomainGetsDedicatedPool() throws SQLException {
        stubDomain("d_own", localDb);

        DataSource ds = poolManager.getDataSource("d_own");

        assertThat(ds).isNotSameAs(defaultDataSource);
        assertThat(currentDatabase(ds)).isNotBlank();
    }

    @Test
    @DisplayName("the pool is built once and cached per domain")
    void poolIsCachedPerDomain() {
        stubDomain("d_cached", localDb);

        assertThat(poolManager.getDataSource("d_cached"))
                .isSameAs(poolManager.getDataSource("d_cached"));
    }

    @Test
    @DisplayName("two domains sharing one database still get separate pools")
    void sameDatabaseStillMeansSeparatePools() {
        // This is the shipped registry's situation: all four domains point at one physical
        // database. Pools are keyed by domain, not by conninfo — unlike the mining side, which
        // de-duplicates pools by conninfo. Pinned so the difference stays deliberate.
        stubDomain("d_a", localDb);
        stubDomain("d_b", localDb);

        DataSource a = poolManager.getDataSource("d_a");
        DataSource b = poolManager.getDataSource("d_b");

        assertThat(a).isNotSameAs(defaultDataSource);
        assertThat(b).isNotSameAs(defaultDataSource);
        assertThat(a).isNotSameAs(b);
    }

    // ------------------------------------------------------------------ unreachable database

    @Test
    @DisplayName("unreachable database fails loudly instead of silently using the default")
    void unreachableDatabaseThrows() {
        // The source of the 503 domain_database_unavailable. Falling back to the default here
        // would be the worst outcome: queries would silently read the wrong database.
        stubDomain("d_dead", new DatabaseConfig(
                "jdbc:postgresql://127.0.0.1:1/definitely_not_there", null, null, null,
                "u", "p", null, null, 1, 2));

        assertThatThrownBy(() -> poolManager.getDataSource("d_dead"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("domain_database_unavailable");
    }

    // ------------------------------------------------------------------ reload

    @Test
    @DisplayName("invalidate drops only the pool whose database config changed")
    void invalidateDropsOnlyChangedPools() {
        stubDomain("d_stable", localDb);
        stubDomain("d_moving", localDb);
        DataSource stableBefore = poolManager.getDataSource("d_stable");
        DataSource movingBefore = poolManager.getDataSource("d_moving");

        // Same connection target, different pool sizing → different signature() → must rebuild.
        stubDomain("d_moving", new DatabaseConfig(
                localDb.jdbcUrl(), null, null, null, localDb.user(), localDb.password(),
                null, null, 3, 7));
        poolManager.invalidate();

        assertThat(poolManager.getDataSource("d_stable"))
                .as("unchanged config must not cause a reconnect")
                .isSameAs(stableBefore);
        assertThat(poolManager.getDataSource("d_moving"))
                .as("changed config must rebuild the pool")
                .isNotSameAs(movingBefore);
    }

    @Test
    @DisplayName("invalidate rebuilds as the default when a domain loses its database block")
    void invalidateFallsBackWhenDatabaseRemoved() {
        stubDomain("d_drop", localDb);
        assertThat(poolManager.getDataSource("d_drop")).isNotSameAs(defaultDataSource);

        stubDomain("d_drop", null);
        poolManager.invalidate();

        assertThat(poolManager.getDataSource("d_drop")).isSameAs(defaultDataSource);
    }

    // ------------------------------------------------------------------ helpers

    private static String currentDatabase(DataSource ds) throws SQLException {
        try (Connection conn = ds.getConnection();
             ResultSet rs = conn.createStatement().executeQuery("SELECT current_database()")) {
            return rs.next() ? rs.getString(1) : "";
        }
    }
}
