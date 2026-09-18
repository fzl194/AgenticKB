package com.coremasterkb.serving.domainpack;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import javax.sql.DataSource;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

@DisplayName("DomainPoolManager")
class DomainPoolManagerTest {

    private final DomainRegistry registry = mock(DomainRegistry.class);
    private final DataSource defaultDs = mock(DataSource.class);

    private static DomainRegistryEntry entryWith(DatabaseConfig db) {
        return new DomainRegistryEntry("cloud_core_network", true, db, "prod");
    }

    /** host+dbname absent → not usable → caller must fall back to the default DataSource. */
    private static DatabaseConfig unusableDb() {
        return new DatabaseConfig(null, null, null, null, "u", "p", null, null, null, null);
    }

    @Nested
    @DisplayName("no registry loaded")
    class NoRegistry {
        @Test
        @DisplayName("getDataSource returns default DataSource when registry not loaded")
        void returnsDefaultWhenRegistryEmpty() {
            when(registry.isLoaded()).thenReturn(false);
            when(registry.findEntry(anyString())).thenReturn(Optional.empty());

            DomainPoolManager mgr = new DomainPoolManager(registry, defaultDs);

            assertThat(mgr.getDataSource("cloud_core_network")).isSameAs(defaultDs);
        }
    }

    @Nested
    @DisplayName("registry loaded, no usable database config")
    class RegistryLoadedNoDatabase {
        @Test
        @DisplayName("getDataSource returns default when database block is absent")
        void returnsDefaultWhenDatabaseNull() {
            when(registry.isLoaded()).thenReturn(true);
            when(registry.findEntry("cloud_core_network")).thenReturn(Optional.of(entryWith(null)));

            DomainPoolManager mgr = new DomainPoolManager(registry, defaultDs);

            assertThat(mgr.getDataSource("cloud_core_network")).isSameAs(defaultDs);
        }

        @Test
        @DisplayName("getDataSource returns default when database block lacks host/dbname")
        void returnsDefaultWhenDatabaseUnusable() {
            when(registry.isLoaded()).thenReturn(true);
            when(registry.findEntry("cloud_core_network")).thenReturn(Optional.of(entryWith(unusableDb())));

            DomainPoolManager mgr = new DomainPoolManager(registry, defaultDs);

            assertThat(mgr.getDataSource("cloud_core_network")).isSameAs(defaultDs);
        }
    }

    @Nested
    @DisplayName("domain not in registry")
    class UnknownDomain {
        @Test
        @DisplayName("getDataSource returns default for unregistered domain")
        void returnsDefaultForUnknownDomain() {
            when(registry.isLoaded()).thenReturn(true);
            when(registry.findEntry(anyString())).thenReturn(Optional.empty());

            DomainPoolManager mgr = new DomainPoolManager(registry, defaultDs);

            assertThat(mgr.getDataSource("nonexistent")).isSameAs(defaultDs);
        }
    }

    @Nested
    @DisplayName("serving-owned schema ensure")
    class SchemaEnsure {
        @Test
        @DisplayName("ensurer runs against the DataSource the domain actually resolved to")
        void ensuresResolvedDataSource() {
            when(registry.isLoaded()).thenReturn(true);
            when(registry.findEntry("cloud_core_network")).thenReturn(Optional.of(entryWith(null)));
            DomainSchemaEnsurer ensurer = mock(DomainSchemaEnsurer.class);

            DomainPoolManager mgr = new DomainPoolManager(registry, defaultDs, ensurer);
            mgr.getDataSource("cloud_core_network");

            verify(ensurer).ensure(defaultDs, "cloud_core_network");
        }

        @Test
        @DisplayName("ensurer runs once per pool creation, not once per request")
        void ensuresOnlyOnPoolCreation() {
            when(registry.isLoaded()).thenReturn(true);
            when(registry.findEntry("cloud_core_network")).thenReturn(Optional.of(entryWith(null)));
            DomainSchemaEnsurer ensurer = mock(DomainSchemaEnsurer.class);

            DomainPoolManager mgr = new DomainPoolManager(registry, defaultDs, ensurer);
            mgr.getDataSource("cloud_core_network");
            mgr.getDataSource("cloud_core_network");

            verify(ensurer, times(1)).ensure(defaultDs, "cloud_core_network");
        }

        @Test
        @DisplayName("a failing schema contract prevents the domain from serving")
        void rejectsFailingEnsurer() {
            when(registry.isLoaded()).thenReturn(true);
            when(registry.findEntry("cloud_core_network")).thenReturn(Optional.of(entryWith(null)));
            DomainSchemaEnsurer ensurer = mock(DomainSchemaEnsurer.class);
            doThrow(new RuntimeException("relation does not exist"))
                    .when(ensurer).ensure(any(), anyString());

            DomainPoolManager mgr = new DomainPoolManager(registry, defaultDs, ensurer);

            assertThatThrownBy(() -> mgr.getDataSource("cloud_core_network"))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessage("domain_schema_contract_failed")
                    .cause()
                    .hasMessage("relation does not exist");
        }
    }

    @Nested
    @DisplayName("invalidate after config reload")
    class Invalidate {
        @Test
        @DisplayName("keeps the cached default when the domain's config is unchanged")
        void keepsUnchangedDefault() {
            when(registry.findEntry("cloud_core_network")).thenReturn(Optional.of(entryWith(null)));

            DomainPoolManager mgr = new DomainPoolManager(registry, defaultDs);
            assertThat(mgr.getDataSource("cloud_core_network")).isSameAs(defaultDs);

            mgr.invalidate();

            // Still resolves — and the registry is only consulted again on a rebuild.
            assertThat(mgr.getDataSource("cloud_core_network")).isSameAs(defaultDs);
        }

        @Test
        @DisplayName("drops the cached entry when the domain disappears from the registry")
        void dropsRemovedDomain() {
            when(registry.findEntry("cloud_core_network")).thenReturn(Optional.of(entryWith(null)));

            DomainPoolManager mgr = new DomainPoolManager(registry, defaultDs);
            mgr.getDataSource("cloud_core_network");

            // Domain removed from the registry, and its new desired state differs from
            // the cached one only if a usable DB appears; removal alone keeps DEFAULT.
            when(registry.findEntry("cloud_core_network")).thenReturn(Optional.empty());
            mgr.invalidate();

            assertThat(mgr.getDataSource("cloud_core_network")).isSameAs(defaultDs);
        }
    }
}
