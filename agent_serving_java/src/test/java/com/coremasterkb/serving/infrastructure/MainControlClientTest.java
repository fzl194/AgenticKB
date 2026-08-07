package com.coremasterkb.serving.infrastructure;

import com.coremasterkb.serving.domainpack.ServingConfigSnapshot;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestTemplate;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * Contract test for the {@code GET /api/v1/serving-config} payload.
 *
 * <p>The body built here mirrors, key for key, what
 * {@code main_control_service.service.YamlConfigService.get_serving_config()} emits:
 * per domain — {@code enabled} / {@code default_channel} / {@code database} (the registry's
 * inline block, or null) / {@code serving} (the scenario pack's serving section). If either
 * side changes its key names, this test is what catches it — the compiler cannot.
 */
@DisplayName("MainControlClient")
class MainControlClientTest {

    private final RestTemplate restTemplate = mock(RestTemplate.class);

    @SuppressWarnings("unchecked")
    private void stubBody(Map<String, Object> body) {
        when(restTemplate.exchange(anyString(), eq(HttpMethod.GET), isNull(),
                any(ParameterizedTypeReference.class)))
                .thenReturn(ResponseEntity.ok(body));
    }

    /** Mirrors the Python payload. Credentials are dummies — shape is what matters. */
    private static Map<String, Object> servingConfigBody() {
        Map<String, Object> db = new HashMap<>();
        db.put("host", "db.example.internal");
        db.put("port", 5432);
        db.put("dbname", "coremasterkb");
        db.put("user", "kb_user");
        db.put("password", "dummy");
        db.put("sslmode", "disable");
        db.put("gssencmode", "disable");
        db.put("pool_min", 2);
        db.put("pool_max", 10);

        Map<String, Object> ccnServing = Map.of(
                "route_policy", Map.of("command_usage", Map.of(
                        "entity_exact", Map.of("weight", 1.6, "top_k", 20))),
                "query_understanding", Map.of("network_elements", List.of("SMF", "UPF")),
                "extractor_rules", List.of(Map.of("pattern", "\\bSMF\\b", "entity_type", "network_element")));

        Map<String, Object> ccn = new HashMap<>();
        ccn.put("enabled", true);
        ccn.put("default_channel", "prod");
        ccn.put("database", db);
        ccn.put("serving", ccnServing);

        // generic has no inline database block — Python emits an explicit null
        Map<String, Object> generic = new HashMap<>();
        generic.put("enabled", true);
        generic.put("default_channel", "prod");
        generic.put("database", null);
        generic.put("serving", Map.of());

        Map<String, Object> disabled = new HashMap<>();
        disabled.put("enabled", false);
        disabled.put("default_channel", "staging");
        disabled.put("database", null);
        disabled.put("serving", Map.of());

        return Map.of("domains", Map.of(
                "cloud_core_network", ccn,
                "generic", generic,
                "retired_domain", disabled));
    }

    @Nested
    @DisplayName("serving-config payload")
    class Payload {

        @Test
        @DisplayName("parses every domain in the snapshot")
        void parsesAllDomains() {
            stubBody(servingConfigBody());
            var snapshot = new MainControlClient(restTemplate, "http://localhost:8910").fetchServingConfig();

            assertThat(snapshot.domains()).containsOnlyKeys(
                    "cloud_core_network", "generic", "retired_domain");
        }

        @Test
        @DisplayName("inline database block builds a usable jdbc: URL")
        void parsesInlineDatabase() {
            stubBody(servingConfigBody());
            var snapshot = new MainControlClient(restTemplate, "http://localhost:8910").fetchServingConfig();

            var db = snapshot.domains().get("cloud_core_network").database();
            assertThat(db).isNotNull();
            assertThat(db.isUsable()).isTrue();
            // The registry stores a bare host/port/dbname; Hikari needs the jdbc: prefix.
            assertThat(db.resolvedJdbcUrl())
                    .isEqualTo("jdbc:postgresql://db.example.internal:5432/coremasterkb"
                            + "?sslmode=disable&gssencmode=disable");
            assertThat(db.user()).isEqualTo("kb_user");
            assertThat(db.poolMin()).isEqualTo(2);
            assertThat(db.poolMax()).isEqualTo(10);
        }

        @Test
        @DisplayName("a null database block means: use the default DataSource")
        void nullDatabaseMeansDefault() {
            stubBody(servingConfigBody());
            var snapshot = new MainControlClient(restTemplate, "http://localhost:8910").fetchServingConfig();

            assertThat(snapshot.domains().get("generic").database()).isNull();
        }

        @Test
        @DisplayName("the scenario pack's serving block is passed through verbatim")
        void passesServingBlockThrough() {
            stubBody(servingConfigBody());
            var snapshot = new MainControlClient(restTemplate, "http://localhost:8910").fetchServingConfig();

            var serving = snapshot.domains().get("cloud_core_network").serving();
            assertThat(serving).containsKeys("route_policy", "query_understanding", "extractor_rules");
        }

        @Test
        @DisplayName("enabled=false and default_channel are honoured")
        void parsesEnabledAndChannel() {
            stubBody(servingConfigBody());
            var snapshot = new MainControlClient(restTemplate, "http://localhost:8910").fetchServingConfig();

            assertThat(snapshot.domains().get("retired_domain").enabled()).isFalse();
            assertThat(snapshot.domains().get("retired_domain").defaultChannel()).isEqualTo("staging");
            assertThat(snapshot.domains().get("cloud_core_network").enabled()).isTrue();
        }

        @Test
        @DisplayName("a domain with no serving key still yields an empty (non-null) block")
        void missingServingKeyYieldsEmptyMap() {
            stubBody(Map.of("domains", Map.of("d1", Map.of("enabled", true))));
            var snapshot = new MainControlClient(restTemplate, "http://localhost:8910").fetchServingConfig();

            var d1 = snapshot.domains().get("d1");
            assertThat(d1.serving()).isEmpty();
            assertThat(d1.database()).isNull();
            assertThat(d1.defaultChannel()).isEqualTo("prod");
        }
    }

    @Nested
    @DisplayName("failure modes — caller falls back to local files")
    class Failures {

        @Test
        @DisplayName("unconfigured base-url throws ConfigFetchException without any HTTP call")
        void unconfiguredThrows() {
            var client = new MainControlClient(restTemplate, "");
            assertThat(client.isConfigured()).isFalse();
            assertThatThrownBy(client::fetchServingConfig)
                    .isInstanceOf(MainControlClient.ConfigFetchException.class);
            verifyNoInteractions(restTemplate);
        }

        @Test
        @DisplayName("transport error is wrapped as ConfigFetchException")
        @SuppressWarnings("unchecked")
        void transportErrorWrapped() {
            when(restTemplate.exchange(anyString(), eq(HttpMethod.GET), isNull(),
                    any(ParameterizedTypeReference.class)))
                    .thenThrow(new RuntimeException("connection refused"));

            assertThatThrownBy(() -> new MainControlClient(restTemplate, "http://localhost:8910")
                    .fetchServingConfig())
                    .isInstanceOf(MainControlClient.ConfigFetchException.class)
                    .hasMessageContaining("connection refused");
        }

        @Test
        @DisplayName("empty body is wrapped as ConfigFetchException")
        void emptyBodyWrapped() {
            stubBody(null);
            assertThatThrownBy(() -> new MainControlClient(restTemplate, "http://localhost:8910")
                    .fetchServingConfig())
                    .isInstanceOf(MainControlClient.ConfigFetchException.class);
        }

        @Test
        @DisplayName("trailing slashes in base-url are normalised")
        void trailingSlashNormalised() {
            stubBody(Map.of("domains", Map.of()));
            new MainControlClient(restTemplate, "http://localhost:8910///").fetchServingConfig();

            verify(restTemplate).exchange(eq("http://localhost:8910/api/v1/serving-config"),
                    eq(HttpMethod.GET), isNull(), any(ParameterizedTypeReference.class));
        }
    }

    /**
     * Contract test for {@code GET /api/v1/system/database} — the parsed form of
     * {@code system/database.yaml}, emitted by {@code YamlConfigService.get_system_config()}
     * (a verbatim YAML→JSON passthrough, so the keys here are the file's keys).
     *
     * <p>This block backs the default DataSource. It is the pool that used to be hardcoded in
     * application.yml, so a silent key mismatch here is a regression to "editing the YAML on the
     * host changes nothing" — the compiler cannot catch it, this test can.</p>
     */
    @Nested
    @DisplayName("system/database payload")
    class DefaultDatabase {

        /** Mirrors main_control_service/config/system/database.yaml. Credentials are dummies. */
        private Map<String, Object> databaseYamlBody() {
            Map<String, Object> def = new HashMap<>();
            def.put("host", "db.example.internal");
            def.put("port", 5432);
            def.put("dbname", "kb_db");
            def.put("user", "kb_user");
            def.put("password", "dummy");
            def.put("sslmode", "disable");
            def.put("gssencmode", "disable");
            def.put("pool_min", 2);
            def.put("pool_max", 10);

            Map<String, Object> body = new HashMap<>();
            body.put("driver", "postgresql");   // top-level sibling of default:, ignored by serving
            body.put("default", def);
            return body;
        }

        @Test
        @DisplayName("the default block builds a usable jdbc: URL with pool sizing")
        void parsesDefaultBlock() {
            stubBody(databaseYamlBody());
            var db = new MainControlClient(restTemplate, "http://localhost:8910").fetchDefaultDatabase();

            assertThat(db.isUsable()).isTrue();
            assertThat(db.resolvedJdbcUrl())
                    .isEqualTo("jdbc:postgresql://db.example.internal:5432/kb_db"
                            + "?sslmode=disable&gssencmode=disable");
            assertThat(db.user()).isEqualTo("kb_user");
            assertThat(db.password()).isEqualTo("dummy");
            assertThat(db.poolMin()).isEqualTo(2);
            assertThat(db.poolMax()).isEqualTo(10);
        }

        @Test
        @DisplayName("hits /api/v1/system/database, not the serving-config endpoint")
        @SuppressWarnings("unchecked")
        void usesSystemDatabaseEndpoint() {
            stubBody(databaseYamlBody());
            new MainControlClient(restTemplate, "http://localhost:8910/").fetchDefaultDatabase();

            verify(restTemplate).exchange(eq("http://localhost:8910/api/v1/system/database"),
                    eq(HttpMethod.GET), isNull(), any(ParameterizedTypeReference.class));
        }

        @Test
        @DisplayName("a missing default block throws so the caller can fall back")
        void missingDefaultBlockThrows() {
            stubBody(Map.of("driver", "postgresql"));
            assertThatThrownBy(() -> new MainControlClient(restTemplate, "http://localhost:8910")
                    .fetchDefaultDatabase())
                    .isInstanceOf(MainControlClient.ConfigFetchException.class)
                    .hasMessageContaining("no usable 'default' block");
        }

        @Test
        @DisplayName("a half-filled default block is rejected rather than half-applied")
        void unusableDefaultBlockThrows() {
            // user/password only — no host, no dbname: cannot build a URL
            stubBody(Map.of("default", Map.of("user", "kb_user", "password", "dummy")));
            assertThatThrownBy(() -> new MainControlClient(restTemplate, "http://localhost:8910")
                    .fetchDefaultDatabase())
                    .isInstanceOf(MainControlClient.ConfigFetchException.class)
                    .hasMessageContaining("no usable 'default' block");
        }

        @Test
        @DisplayName("transport error is wrapped as ConfigFetchException")
        @SuppressWarnings("unchecked")
        void transportErrorWrapped() {
            when(restTemplate.exchange(anyString(), eq(HttpMethod.GET), isNull(),
                    any(ParameterizedTypeReference.class)))
                    .thenThrow(new RuntimeException("connection refused"));

            assertThatThrownBy(() -> new MainControlClient(restTemplate, "http://localhost:8910")
                    .fetchDefaultDatabase())
                    .isInstanceOf(MainControlClient.ConfigFetchException.class)
                    .hasMessageContaining("connection refused");
        }

        @Test
        @DisplayName("unconfigured base-url throws without any HTTP call")
        void unconfiguredThrows() {
            var client = new MainControlClient(restTemplate, "");
            assertThatThrownBy(client::fetchDefaultDatabase)
                    .isInstanceOf(MainControlClient.ConfigFetchException.class);
            verifyNoInteractions(restTemplate);
        }
    }
}
