package com.coremasterkb.serving.config;

import com.coremasterkb.serving.domainpack.DatabaseConfig;
import com.coremasterkb.serving.infrastructure.MainControlClient;
import com.zaxxer.hikari.HikariDataSource;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.jdbc.DataSourceProperties;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.*;

/**
 * The default DataSource resolves its address from main_control, not from application.yml.
 *
 * <p>Why this test exists: the URL used to be hardcoded in application.yml. Because the jar is
 * baked into the image and no bind-mount covers it, editing the DB address on the host changed
 * mining and every per-domain pool but NOT this one — serving kept talking to the old database
 * with nothing in the logs to say so. These tests pin the two properties that prevent a repeat:
 * main_control wins, and an unresolved address fails loudly instead of falling back to a stale one.</p>
 */
@DisplayName("ServingBeans default DataSource")
class ServingBeansDataSourceTest {

    private static final DatabaseConfig REMOTE = new DatabaseConfig(
            null, "db.example.internal", 5432, "kb_db", "kb_user", "dummy",
            "disable", "disable", 3, 7);

    /** ServingBeans with the control-plane fetch stubbed out — no backoff sleeps, no HTTP. */
    private static ServingBeans beansResolving(DatabaseConfig result) {
        return new ServingBeans() {
            @Override
            protected DatabaseConfig fetchDefaultDatabaseWithRetry(MainControlClient client) {
                return result;
            }
        };
    }

    private static DataSourceProperties props(String url, String user, String password) {
        DataSourceProperties p = new DataSourceProperties();
        p.setUrl(url);
        p.setUsername(user);
        p.setPassword(password);
        return p;
    }

    /** Production shape: the default DataSource takes its address from main_control. */
    private static ServingProperties servingProps(boolean defaultDatabaseEnabled) {
        return new ServingProperties(null, null, null, null, null,
                new ServingProperties.MainControl("http://localhost:8910", defaultDatabaseEnabled), null, null);
    }

    @Nested
    @DisplayName("when main_control answers")
    class FromControlPlane {

        @Test
        @DisplayName("address and credentials come from main_control, not spring.datasource")
        void usesRemoteAddress() {
            // A stale application.yml value must lose — that is the whole point.
            var properties = props("jdbc:postgresql://stale.example.internal:5432/old_db", "old", "old");

            try (HikariDataSource ds = beansResolving(REMOTE)
                    .defaultDataSource(properties, mock(MainControlClient.class), servingProps(true))) {
                assertThat(ds.getJdbcUrl())
                        .isEqualTo("jdbc:postgresql://db.example.internal:5432/kb_db"
                                + "?sslmode=disable&gssencmode=disable");
                assertThat(ds.getUsername()).isEqualTo("kb_user");
                assertThat(ds.getPassword()).isEqualTo("dummy");
            }
        }

        @Test
        @DisplayName("pool sizing follows the default block's pool_min / pool_max")
        void usesRemotePoolSizing() {
            try (HikariDataSource ds = beansResolving(REMOTE)
                    .defaultDataSource(props(null, null, null), mock(MainControlClient.class), servingProps(true))) {
                assertThat(ds.getMinimumIdle()).isEqualTo(3);
                assertThat(ds.getMaximumPoolSize()).isEqualTo(7);
                assertThat(ds.getConnectionTimeout()).isEqualTo(5_000);
            }
        }

        @Test
        @DisplayName("absent pool_min / pool_max fall back to 2 / 10")
        void defaultsPoolSizing() {
            DatabaseConfig noSizing = new DatabaseConfig(
                    null, "db.example.internal", 5432, "kb_db", "kb_user", "dummy",
                    null, null, null, null);

            try (HikariDataSource ds = beansResolving(noSizing)
                    .defaultDataSource(props(null, null, null), mock(MainControlClient.class), servingProps(true))) {
                assertThat(ds.getMinimumIdle()).isEqualTo(2);
                assertThat(ds.getMaximumPoolSize()).isEqualTo(10);
            }
        }
    }

    @Nested
    @DisplayName("when main_control is unreachable")
    class Fallback {

        @Test
        @DisplayName("falls back to spring.datasource.url when one is configured")
        void fallsBackToLocalProperties() {
            var properties = props("jdbc:postgresql://fallback.example.internal:5432/kb_db", "u", "p");

            try (HikariDataSource ds = beansResolving(null)
                    .defaultDataSource(properties, mock(MainControlClient.class), servingProps(true))) {
                assertThat(ds.getJdbcUrl())
                        .isEqualTo("jdbc:postgresql://fallback.example.internal:5432/kb_db");
                assertThat(ds.getUsername()).isEqualTo("u");
            }
        }

        @Test
        @DisplayName("a blank fallback url fails startup instead of guessing an address")
        void blankFallbackFailsLoudly() {
            // application.yml ships `url: ${SPRING_DATASOURCE_URL:}` — blank, so this is the
            // production path when main_control is down. Starting up on an unknown DB is worse
            // than not starting.
            assertThatThrownBy(() -> beansResolving(null)
                    .defaultDataSource(props("", "", ""), mock(MainControlClient.class), servingProps(true)))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("default_datasource_unresolved");
        }

        @Test
        @DisplayName("a null fallback url fails the same way")
        void nullFallbackFailsLoudly() {
            assertThatThrownBy(() -> beansResolving(null)
                    .defaultDataSource(props(null, null, null), mock(MainControlClient.class), servingProps(true)))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("default_datasource_unresolved");
        }
    }

    @Nested
    @DisplayName("when default-database-enabled=false")
    class OptedOut {

        @Test
        @DisplayName("main_control is not consulted at all — spring.datasource wins")
        void neverConsultsControlPlane() {
            // Guards the integration-test setup: a developer's main_control running on this host
            // must not be able to redirect the startup DDL away from the throwaway test DB.
            ServingBeans beans = new ServingBeans() {
                @Override
                protected DatabaseConfig fetchDefaultDatabaseWithRetry(MainControlClient client) {
                    throw new AssertionError("main_control must not be consulted when opted out");
                }
            };
            var properties = props("jdbc:postgresql://localhost:15432/test_db", "zdy", "zdy1234");

            try (HikariDataSource ds = beans.defaultDataSource(
                    properties, mock(MainControlClient.class), servingProps(false))) {
                assertThat(ds.getJdbcUrl()).isEqualTo("jdbc:postgresql://localhost:15432/test_db");
            }
        }
    }

    @Nested
    @DisplayName("startup retry")
    class Retry {

        @Test
        @DisplayName("retries while main_control is still starting, then succeeds")
        void retriesUntilControlPlaneIsReady() {
            // supervisor's priority (control 10 < serving 30) orders the launches but does not
            // wait for main_control's port to accept, so the first call legitimately fails.
            MainControlClient client = mock(MainControlClient.class);
            when(client.fetchDefaultDatabase())
                    .thenThrow(new MainControlClient.ConfigFetchException("connection refused"))
                    .thenReturn(REMOTE);

            assertThat(new ServingBeans().fetchDefaultDatabaseWithRetry(client)).isEqualTo(REMOTE);
            verify(client, times(2)).fetchDefaultDatabase();
        }
    }
}
