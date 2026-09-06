package com.coremasterkb.serving.domainpack;

import com.coremasterkb.serving.config.ServingProperties;
import com.coremasterkb.serving.domainpack.ServingConfigSnapshot.DomainConfig;
import com.coremasterkb.serving.infrastructure.MainControlClient;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.yaml.snakeyaml.Yaml;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Orchestrates loading the serving config and feeding it to the registry, pack reader and
 * pool manager — at startup and on every hot-reload. Fetches once from main_control; on
 * failure falls back to the local config files (keeps IntelliJ/dev and tests working when
 * main_control is not running).
 */
@Service
public class ConfigReloadService {

    private static final Logger log = LoggerFactory.getLogger(ConfigReloadService.class);

    private final MainControlClient mainControlClient;
    private final DomainRegistry domainRegistry;
    private final DomainPoolManager domainPoolManager;
    private final ServingProperties properties;

    public ConfigReloadService(MainControlClient mainControlClient,
                               DomainRegistry domainRegistry,
                               DomainPoolManager domainPoolManager,
                               ServingProperties properties) {
        this.mainControlClient = mainControlClient;
        this.domainRegistry = domainRegistry;
        this.domainPoolManager = domainPoolManager;
        this.properties = properties;
    }

    @PostConstruct
    public void init() {
        try {
            reload();
        } catch (Exception e) {
            // Never fail startup on config load — run lenient, recover via reload endpoint.
            log.warn("Initial config load failed, starting with lenient/empty config: {}", e.getMessage());
        }
    }

    /**
     * Fetch the snapshot (main_control first, local files as fallback) and apply it to the
     * registry and pool manager. Order matters: registry first (pool manager reads it),
     * then invalidate stale pools.
     *
     * @return the number of domains applied
     */
    public synchronized int reload() {
        ServingConfigSnapshot snapshot;
        String source;
        try {
            snapshot = mainControlClient.fetchServingConfig();
            source = "main_control";
        } catch (Exception e) {
            log.warn("main_control fetch failed ({}), falling back to local files", e.getMessage());
            snapshot = loadFromFiles();
            source = "local_files";
        }
        domainRegistry.apply(snapshot);
        domainPoolManager.invalidate();
        log.info("Config reload complete from {}: {} domain(s)", source, snapshot.domains().size());
        return snapshot.domains().size();
    }

    // -------------------------------------------------------------------------
    // Local-file fallback
    //
    // Reads the SAME files main_control serves from (main_control/config/), so the
    // two paths produce an equivalent snapshot: the registry's inline `database:`
    // block and each scenario pack's `serving:` block.
    // -------------------------------------------------------------------------

    @SuppressWarnings("unchecked")
    private ServingConfigSnapshot loadFromFiles() {
        Map<String, DomainConfig> domains = new LinkedHashMap<>();
        Path registryPath = Paths.get(properties.domainRegistryPath());
        if (!Files.exists(registryPath)) {
            log.info("No local registry at '{}' — empty config (lenient mode)", registryPath);
            return new ServingConfigSnapshot(domains);
        }
        try (InputStream is = Files.newInputStream(registryPath)) {
            Map<String, Object> root = new Yaml().load(is);
            Map<String, Object> registry = (Map<String, Object>) root.getOrDefault("domains", Map.of());
            for (var kv : registry.entrySet()) {
                String domainId = kv.getKey();
                if (!(kv.getValue() instanceof Map<?, ?> rawCfg)) continue;
                Map<String, Object> cfg = (Map<String, Object>) rawCfg;

                boolean enabled = !Boolean.FALSE.equals(cfg.get("enabled"));
                String channel = cfg.get("default_channel") instanceof String s ? s : "prod";
                String packRef = cfg.get("scenario_pack") instanceof String p ? p : domainId;

                DatabaseConfig database = parseDatabase(cfg.get("database"));

                domains.put(domainId, new DomainConfig(domainId, enabled, channel, database));
            }
        } catch (Exception e) {
            log.warn("Local-file fallback failed to parse registry '{}': {}", registryPath, e.getMessage());
        }
        return new ServingConfigSnapshot(domains);
    }

    /** Parse the registry's inline {@code database:} block — mirrors MainControlClient. */
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
}
