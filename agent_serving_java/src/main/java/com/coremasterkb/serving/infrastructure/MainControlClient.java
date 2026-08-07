package com.coremasterkb.serving.infrastructure;

import com.coremasterkb.serving.domainpack.DatabaseConfig;
import com.coremasterkb.serving.domainpack.ServingConfigSnapshot;
import com.coremasterkb.serving.domainpack.ServingConfigSnapshot.DomainConfig;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestTemplate;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Reads serving's config from main_control over HTTP. Serving does not read config files
 * directly — the local files are only a fallback, see {@code ConfigReloadService}.
 *
 * <p>Two endpoints, two audiences:</p>
 * <ul>
 *   <li>{@code GET /api/v1/serving-config} — the per-domain snapshot (inline {@code database}
 *       blocks + the scenario pack's {@code serving} section), reloadable at runtime.</li>
 *   <li>{@code GET /api/v1/system/database} — the global {@code default} block of
 *       {@code system/database.yaml}, read once at startup to build the default DataSource.</li>
 * </ul>
 */
public class MainControlClient {

    private static final ParameterizedTypeReference<Map<String, Object>> MAP_TYPE =
            new ParameterizedTypeReference<>() {};

    private final RestTemplate restTemplate;
    private final String baseUrl;

    public MainControlClient(RestTemplate restTemplate, String baseUrl) {
        this.restTemplate = restTemplate;
        this.baseUrl = baseUrl != null ? baseUrl.replaceAll("/+$", "") : "";
    }

    public boolean isConfigured() {
        return baseUrl != null && !baseUrl.isBlank();
    }

    /**
     * Fetch and parse the serving-config snapshot.
     *
     * @throws ConfigFetchException on any transport / parse failure (caller decides fallback)
     */
    public ServingConfigSnapshot fetchServingConfig() {
        if (!isConfigured()) {
            throw new ConfigFetchException("main_control base-url not configured");
        }
        try {
            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    baseUrl + "/api/v1/serving-config",
                    HttpMethod.GET,
                    null,
                    MAP_TYPE);
            Map<String, Object> body = response.getBody();
            if (body == null) {
                throw new ConfigFetchException("empty body from main_control");
            }
            return parse(body);
        } catch (ConfigFetchException e) {
            throw e;
        } catch (Exception e) {
            throw new ConfigFetchException("fetch from main_control failed: " + e.getMessage(), e);
        }
    }

    /**
     * Fetch the global default database — the {@code default} block of
     * {@code main_control_service/config/system/database.yaml}, served as parsed JSON by
     * {@code GET /api/v1/system/database}. This is the same file mining reads (via the
     * {@code /raw} YAML variant), so both lines resolve the DB address from one source.
     *
     * <p>Backs {@code defaultDataSource}: the non-routed global tables ({@code operator_paradigm*})
     * plus any domain without an inline {@code database} block. Deliberately NOT folded into the
     * {@code /serving-config} snapshot — that snapshot is hot-reloadable, whereas a Hikari pool's
     * JDBC URL is immutable once built, so changing the default DB requires a serving restart.</p>
     *
     * @throws ConfigFetchException on transport failure, or when the file has no usable
     *                             {@code default} block (caller decides fallback)
     */
    public DatabaseConfig fetchDefaultDatabase() {
        if (!isConfigured()) {
            throw new ConfigFetchException("main_control base-url not configured");
        }
        try {
            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    baseUrl + "/api/v1/system/database",
                    HttpMethod.GET,
                    null,
                    MAP_TYPE);
            Map<String, Object> body = response.getBody();
            if (body == null) {
                throw new ConfigFetchException("empty body from main_control");
            }
            DatabaseConfig db = parseDatabase(body.get("default"));
            if (db == null || !db.isUsable()) {
                throw new ConfigFetchException(
                        "system/database.yaml has no usable 'default' block (need host+dbname or jdbc_url)");
            }
            return db;
        } catch (ConfigFetchException e) {
            throw e;
        } catch (Exception e) {
            throw new ConfigFetchException(
                    "fetch default database from main_control failed: " + e.getMessage(), e);
        }
    }

    @SuppressWarnings("unchecked")
    private ServingConfigSnapshot parse(Map<String, Object> body) {
        Map<String, Object> domains = body.get("domains") instanceof Map<?, ?> m
                ? (Map<String, Object>) m : Map.of();
        Map<String, DomainConfig> parsed = new LinkedHashMap<>();
        for (var entry : domains.entrySet()) {
            String domainId = entry.getKey();
            if (!(entry.getValue() instanceof Map<?, ?> raw)) continue;
            Map<String, Object> dc = (Map<String, Object>) raw;

            boolean enabled = !Boolean.FALSE.equals(dc.get("enabled"));
            String channel = dc.get("default_channel") instanceof String s ? s : "prod";
            DatabaseConfig database = parseDatabase(dc.get("database"));
            Map<String, Object> serving = dc.get("serving") instanceof Map<?, ?> sm
                    ? (Map<String, Object>) sm : Map.of();

            parsed.put(domainId, new DomainConfig(domainId, enabled, channel, database, serving));
        }
        return new ServingConfigSnapshot(parsed);
    }

    @SuppressWarnings("unchecked")
    private DatabaseConfig parseDatabase(Object obj) {
        if (!(obj instanceof Map<?, ?> raw)) return null;
        Map<String, Object> db = (Map<String, Object>) raw;
        return new DatabaseConfig(
                str(db.get("jdbc_url")),
                str(db.get("host")),
                intOrNull(db.get("port")),
                str(db.get("dbname")),
                str(db.get("user")),
                str(db.get("password")),
                str(db.get("sslmode")),
                str(db.get("gssencmode")),
                intOrNull(db.get("pool_min")),
                intOrNull(db.get("pool_max")));
    }

    private static String str(Object o) {
        return o != null ? String.valueOf(o) : null;
    }

    private static Integer intOrNull(Object o) {
        if (o instanceof Number n) return n.intValue();
        if (o instanceof String s && !s.isBlank()) {
            try {
                return Integer.parseInt(s.trim());
            } catch (NumberFormatException ignored) {
                return null;
            }
        }
        return null;
    }

    /** Thrown when the serving config cannot be fetched/parsed from main_control. */
    public static class ConfigFetchException extends RuntimeException {
        public ConfigFetchException(String message) {
            super(message);
        }

        public ConfigFetchException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
