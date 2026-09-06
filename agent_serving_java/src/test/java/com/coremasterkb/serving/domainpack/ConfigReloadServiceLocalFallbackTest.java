package com.coremasterkb.serving.domainpack;

import com.coremasterkb.serving.config.ServingProperties;
import com.coremasterkb.serving.infrastructure.MainControlClient;
import com.coremasterkb.serving.domainpack.ServingConfigSnapshot.DomainConfig;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;

import java.nio.file.Path;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 瘦身批次5 复审补测：Domain reload 的本地文件 fallback。
 *
 * <p>main_control 不可达时 {@code ConfigReloadService.reload()} 必须回退解析本地
 * domain registry（含内联 database 块），并把结果原子交给 registry 与 pool manager。
 * 该路径此前无直接测试（DomainPackReader 时代的断言随其退役删除）。</p>
 */
@DisplayName("ConfigReloadService — 本地文件 fallback")
class ConfigReloadServiceLocalFallbackTest {

    @TempDir
    Path tempDir;

    @Test
    @DisplayName("main_control 失败 → 本地 registry 解析出域与内联数据库块并应用")
    void fallsBackToLocalRegistryAndAppliesSnapshot() throws Exception {
        String registryYaml = """
                domains:
                  d1:
                    enabled: true
                    default_channel: beta
                    database:
                      host: db.example.internal
                      port: 5432
                      dbname: coremasterkb
                      user: kb_user
                      password: secret
                      sslmode: disable
                      pool_min: 2
                      pool_max: 10
                  d2:
                    enabled: false
                """;
        Path registry = tempDir.resolve("domain_registry.yaml");
        java.nio.file.Files.writeString(registry, registryYaml);

        MainControlClient client = mock(MainControlClient.class);
        when(client.fetchServingConfig())
                .thenThrow(new MainControlClient.ConfigFetchException("main_control down"));
        DomainRegistry registryBean = mock(DomainRegistry.class);
        DomainPoolManager poolManager = mock(DomainPoolManager.class);
        ServingProperties props = new ServingProperties(
                tempDir.resolve("packs").toString(), registry.toString(), null,
                null, null, null, null);

        int applied = new ConfigReloadService(client, registryBean, poolManager, props).reload();

        assertThat(applied).isEqualTo(2);
        ArgumentCaptor<ServingConfigSnapshot> captor =
                ArgumentCaptor.forClass(ServingConfigSnapshot.class);
        verify(registryBean).apply(captor.capture());
        Map<String, DomainConfig> domains = captor.getValue().domains();
        assertThat(domains).containsOnlyKeys("d1", "d2");

        DomainConfig d1 = domains.get("d1");
        assertThat(d1.enabled()).isTrue();
        assertThat(d1.defaultChannel()).isEqualTo("beta");
        assertThat(d1.database()).isNotNull();
        assertThat(d1.database().resolvedJdbcUrl())
                .startsWith("jdbc:postgresql://db.example.internal:5432/coremasterkb");
        assertThat(d1.database().user()).isEqualTo("kb_user");
        assertThat(d1.database().poolMax()).isEqualTo(10);

        assertThat(domains.get("d2").enabled()).isFalse();
        assertThat(domains.get("d2").database()).isNull();
        verify(poolManager).invalidate();
    }

    @Test
    @DisplayName("本地 registry 不存在 → 空快照（宽松模式），不抛异常")
    void missingLocalRegistryYieldsEmptySnapshot() {
        MainControlClient client = mock(MainControlClient.class);
        when(client.fetchServingConfig())
                .thenThrow(new MainControlClient.ConfigFetchException("main_control down"));
        DomainRegistry registryBean = mock(DomainRegistry.class);
        DomainPoolManager poolManager = mock(DomainPoolManager.class);
        ServingProperties props = new ServingProperties(
                tempDir.toString(), tempDir.resolve("missing.yaml").toString(),
                null, null, null, null, null);

        int applied = new ConfigReloadService(client, registryBean, poolManager, props).reload();

        assertThat(applied).isZero();
        verify(registryBean).apply(any(ServingConfigSnapshot.class));
        verify(poolManager).invalidate();
    }
}
