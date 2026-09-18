package com.coremasterkb.serving.domainpack;

import javax.sql.DataSource;

/**
 * Hook invoked by {@link DomainPoolManager} once per resolved DataSource so
 * its migration-ledger contract can be validated before serving reads.
 *
 * <p>Implementations fail closed. Schema changes belong to the deployment
 * migration command and must never happen while creating a request-time pool.</p>
 */
@FunctionalInterface
public interface DomainSchemaEnsurer {

    /** No-op ensurer, used by tests and by the legacy two-arg constructor. */
    DomainSchemaEnsurer NOOP = (dataSource, domain) -> { };

    void ensure(DataSource dataSource, String domain);
}
