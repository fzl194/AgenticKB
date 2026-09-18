package com.coremasterkb.serving.domainpack;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.sql.Connection;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Manages per-domain DataSource instances built from each domain's inline
 * {@link DatabaseConfig} (sourced from main_control). When a domain has a usable DB
 * config a dedicated HikariCP pool is created; otherwise the default DataSource is reused.
 *
 * <p>Pools are created lazily on first access and cached. {@link #invalidate()} is called
 * after a config reload: pools whose DB signature changed (or whose domain was removed)
 * are closed and dropped so the next request rebuilds them from the new config; unchanged
 * pools are left untouched to avoid needless reconnects.
 *
 * <p>{@link #getDataSource(String)} throws {@code IllegalStateException("domain_database_unavailable")}
 * when the domain's configured database cannot be reached.</p>
 */
@Component
public class DomainPoolManager {

    private static final Logger log = LoggerFactory.getLogger(DomainPoolManager.class);

    private static final String DEFAULT_SIGNATURE = "__default__";

    private final DomainRegistry domainRegistry;
    private final DataSource defaultDataSource;
    /** Applied to each resolved DataSource so serving-owned tables exist in every routed DB. */
    private final DomainSchemaEnsurer schemaEnsurer;

    /** domain → resolved DataSource (may be the default for unconfigured domains). */
    private final Map<String, DataSource> pools = new ConcurrentHashMap<>();
    /** domain → DB signature backing its current pool (for change detection on reload). */
    private final Map<String, String> poolSignatures = new ConcurrentHashMap<>();
    /** Dedicated Hikari pools we own so we can close them. */
    private final Map<String, HikariDataSource> ownedPools = new ConcurrentHashMap<>();

    /** Test-friendly constructor: no schema is ensured on the resolved DataSources. */
    public DomainPoolManager(DomainRegistry domainRegistry,
                             @Qualifier("defaultDataSource") DataSource defaultDataSource) {
        this(domainRegistry, defaultDataSource, DomainSchemaEnsurer.NOOP);
    }

    @Autowired
    public DomainPoolManager(DomainRegistry domainRegistry,
                             @Qualifier("defaultDataSource") DataSource defaultDataSource,
                             DomainSchemaEnsurer schemaEnsurer) {
        this.domainRegistry = domainRegistry;
        this.defaultDataSource = defaultDataSource;
        this.schemaEnsurer = schemaEnsurer;
    }

    /**
     * Returns the DataSource for the given domain, creating a dedicated pool on first use.
     *
     * @throws IllegalStateException("domain_database_unavailable") if the configured DB is unreachable
     */
    public DataSource getDataSource(String domain) {
        return pools.computeIfAbsent(domain, this::resolveDataSource);
    }

    private DataSource resolveDataSource(String domain) {
        DatabaseConfig db = domainRegistry.findEntry(domain)
                .map(DomainRegistryEntry::database)
                .orElse(null);

        if (db == null || !db.isUsable()) {
            poolSignatures.put(domain, DEFAULT_SIGNATURE);
            ensureSchema(defaultDataSource, domain);
            return defaultDataSource;
        }

        log.info("Creating dedicated pool for domain '{}'", domain);
        HikariDataSource ds = createHikariPool(domain, db);
        ownedPools.put(domain, ds);
        poolSignatures.put(domain, db.signature());
        try {
            ensureSchema(ds, domain);
        } catch (RuntimeException e) {
            ownedPools.remove(domain, ds);
            poolSignatures.remove(domain, db.signature());
            ds.close();
            throw e;
        }
        return ds;
    }

    /**
     * Verify the immutable database contract before this domain serves reads.
     * A schema mismatch is not optional: continuing would expose a partially migrated DB.
     */
    private void ensureSchema(DataSource dataSource, String domain) {
        try {
            schemaEnsurer.ensure(dataSource, domain);
        } catch (Exception e) {
            log.error("Schema contract failed for domain '{}': {}", domain, e.getMessage());
            throw new IllegalStateException("domain_schema_contract_failed", e);
        }
    }

    /**
     * Drop pools whose backing DB config changed (or whose domain was removed) so the next
     * request rebuilds them from the freshly-applied registry. Unchanged pools are kept.
     * Must be called AFTER {@link DomainRegistry#apply} so lookups see the new config.
     */
    public void invalidate() {
        for (String domain : Map.copyOf(poolSignatures).keySet()) {
            DatabaseConfig db = domainRegistry.findEntry(domain)
                    .map(DomainRegistryEntry::database)
                    .orElse(null);
            String desired = (db != null && db.isUsable()) ? db.signature() : DEFAULT_SIGNATURE;
            String current = poolSignatures.get(domain);
            if (!Objects.equals(desired, current)) {
                pools.remove(domain);
                poolSignatures.remove(domain);
                HikariDataSource owned = ownedPools.remove(domain);
                if (owned != null) {
                    try {
                        owned.close();
                        log.info("Closed stale pool for domain '{}'", domain);
                    } catch (Exception e) {
                        log.warn("Error closing pool for '{}': {}", domain, e.getMessage());
                    }
                }
            }
        }
    }

    private HikariDataSource createHikariPool(String domain, DatabaseConfig db) {
        HikariConfig cfg = new HikariConfig();
        cfg.setJdbcUrl(db.resolvedJdbcUrl());
        if (db.user() != null) cfg.setUsername(db.user());
        if (db.password() != null) cfg.setPassword(db.password());
        cfg.setPoolName("hikari-" + domain);
        cfg.setMinimumIdle(db.poolMin() != null ? db.poolMin() : 2);
        cfg.setMaximumPoolSize(db.poolMax() != null ? db.poolMax() : 10);
        cfg.setConnectionTimeout(5_000);
        cfg.setInitializationFailTimeout(5_000);
        try {
            HikariDataSource ds = new HikariDataSource(cfg);
            // Eagerly verify the connection
            try (Connection conn = ds.getConnection()) {
                if (!conn.isValid(3)) {
                    ds.close();
                    throw new IllegalStateException("domain_database_unavailable");
                }
            }
            return ds;
        } catch (IllegalStateException e) {
            throw e;
        } catch (Exception e) {
            log.error("Cannot connect to domain '{}' database: {}", domain, e.getMessage());
            throw new IllegalStateException("domain_database_unavailable");
        }
    }

    @PreDestroy
    public void shutdown() {
        for (var entry : ownedPools.entrySet()) {
            try {
                entry.getValue().close();
                log.info("Closed pool: {}", entry.getValue().getPoolName());
            } catch (Exception e) {
                log.warn("Error closing pool {}: {}", entry.getKey(), e.getMessage());
            }
        }
        ownedPools.clear();
        pools.clear();
        poolSignatures.clear();
    }
}
