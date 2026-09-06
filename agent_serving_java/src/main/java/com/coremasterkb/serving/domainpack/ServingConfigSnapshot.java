package com.coremasterkb.serving.domainpack;

import java.util.Map;

/**
 * An immutable point-in-time view of all per-domain config Serving consumes,
 * as fetched from main_control's {@code GET /api/v1/serving-config} (or built from
 * the local-file fallback). Fed atomically into {@link DomainRegistry} and
 * {@link DomainPoolManager} on startup and on reload.
 *
 * <p>瘦身批次5：DomainConfig 的 {@code serving} 块（route_policy /
 * query_understanding / extractor_rules / intent_strategy）已随 DomainPackReader
 * 退役——生产代码从未读取过该缓存。</p>
 */
public record ServingConfigSnapshot(Map<String, DomainConfig> domains) {

    public ServingConfigSnapshot {
        if (domains == null) domains = Map.of();
    }

    /**
     * One domain's slice of the snapshot.
     *
     * @param domainId       domain identifier
     * @param enabled        whether the domain accepts traffic
     * @param defaultChannel release channel when the caller omits one
     * @param database       inline DB connection, or {@code null} → use default DataSource
     */
    public record DomainConfig(
            String domainId,
            boolean enabled,
            String defaultChannel,
            DatabaseConfig database
    ) {
        public DomainConfig {
            if (defaultChannel == null || defaultChannel.isBlank()) defaultChannel = "prod";
        }
    }
}
