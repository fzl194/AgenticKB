package com.coremasterkb.serving.domainpack;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * Holds the per-domain registry view sourced from main_control's serving-config
 * snapshot. Populated at startup and replaced atomically whenever a snapshot is applied by
 * {@link #apply(ServingConfigSnapshot)} — Serving no longer reads the registry file
 * directly (see {@link ConfigReloadService} for the fetch + file-fallback logic).
 *
 * <p>When the registry is non-empty:
 * <ul>
 *   <li>Unknown domains throw {@code IllegalArgumentException("unknown_domain")}.</li>
 *   <li>Disabled domains throw {@code IllegalArgumentException("domain_disabled")}.</li>
 * </ul>
 * When empty (main_control unreachable at startup and no local fallback), all lookups are
 * lenient and no enforcement applies.
 */
@Component
public class DomainRegistry {

    private static final Logger log = LoggerFactory.getLogger(DomainRegistry.class);

    /** Replaced wholesale on snapshot apply — volatile so readers see a consistent map. */
    private volatile Map<String, DomainRegistryEntry> entries = Map.of();

    /** Rebuild the registry from a snapshot, replacing the previous view atomically. */
    public void apply(ServingConfigSnapshot snapshot) {
        Map<String, DomainRegistryEntry> next = new LinkedHashMap<>();
        for (var e : snapshot.domains().entrySet()) {
            var dc = e.getValue();
            next.put(e.getKey(), new DomainRegistryEntry(
                    e.getKey(), dc.enabled(), dc.database(), dc.defaultChannel()));
        }
        this.entries = Collections.unmodifiableMap(next);
        log.info("Domain registry applied: {} domain(s) — {}", next.size(), next.keySet());
    }

    /**
     * Validate and return the registry entry for the given domain.
     *
     * @return entry, or {@code null} if the registry is empty (no enforcement)
     * @throws IllegalArgumentException("unknown_domain")  if registry is non-empty and domain is not listed
     * @throws IllegalArgumentException("domain_disabled") if the domain is disabled
     */
    public DomainRegistryEntry resolve(String domain) {
        Map<String, DomainRegistryEntry> snap = entries;
        if (snap.isEmpty()) return null;
        DomainRegistryEntry entry = snap.get(domain);
        if (entry == null) {
            log.warn("Unknown domain '{}', known domains: {}", domain, snap.keySet());
            throw new IllegalArgumentException("unknown_domain");
        }
        if (!entry.enabled()) {
            log.warn("Disabled domain '{}'", domain);
            throw new IllegalArgumentException("domain_disabled");
        }
        return entry;
    }

    /**
     * Return the default channel for the domain, or {@code "prod"} if not configured.
     * Never throws — safe to call after domain has already been validated.
     */
    public String getDefaultChannel(String domain) {
        DomainRegistryEntry entry = entries.get(domain);
        return (entry != null) ? entry.defaultChannel() : "prod";
    }

    /**
     * Silent lookup — returns empty if registry empty or domain not found. Never throws.
     * Use this for informational/debug reads after validation has already passed.
     */
    public Optional<DomainRegistryEntry> findEntry(String domain) {
        return Optional.ofNullable(entries.get(domain));
    }

    public boolean isLoaded() {
        return !entries.isEmpty();
    }

    public Set<String> knownDomains() {
        return Collections.unmodifiableSet(entries.keySet());
    }
}
