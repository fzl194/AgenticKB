package com.coremasterkb.serving.config;

import com.coremasterkb.serving.domainpack.DatabaseConfig;
import com.coremasterkb.serving.domainpack.DomainPackReader;
import com.coremasterkb.serving.domainpack.DomainPoolManager;
import com.coremasterkb.serving.domainpack.DomainRoutingDataSource;
import com.coremasterkb.serving.infrastructure.EmbeddingClient;
import com.coremasterkb.serving.infrastructure.LlmClient;
import com.coremasterkb.serving.infrastructure.MainControlClient;
import com.coremasterkb.serving.mapper.AssetRetrievalEmbeddingMapper;
import com.coremasterkb.serving.mapper.AssetRetrievalUnitMapper;
import com.coremasterkb.serving.rerank.LlmServiceReranker;
import com.coremasterkb.serving.retrieval.DenseVectorRetriever;
import com.coremasterkb.serving.retrieval.EntityExactRetriever;
import com.coremasterkb.serving.retrieval.FtsRetriever;
import com.zaxxer.hikari.HikariDataSource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.autoconfigure.jdbc.DataSourceProperties;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.http.client.HttpComponentsClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;
import org.apache.hc.client5.http.config.ConnectionConfig;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.client5.http.impl.classic.HttpClients;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManager;
import org.apache.hc.core5.util.Timeout;

import javax.sql.DataSource;
import java.util.concurrent.Executor;
import java.util.concurrent.Executors;

/**
 * Explicit wiring for plain-Java components that are not annotated with
 * {@code @Component}/{@code @Service}/{@code @Repository}.
 *
 * <p>Components already picked up by component scanning (DomainPackReader,
 * QueryLogService, QueryLogAspect, AssetRepository) are NOT declared here.</p>
 */
@Configuration
@EnableConfigurationProperties(ServingProperties.class)
public class ServingBeans {

    private static final Logger log = LoggerFactory.getLogger(ServingBeans.class);

    /** Default-pool sizing when neither main_control nor spring.datasource.hikari.* says otherwise. */
    private static final int DEFAULT_POOL_MIN = 2;
    private static final int DEFAULT_POOL_MAX = 10;
    private static final int DEFAULT_CONNECTION_TIMEOUT_MS = 5_000;

    /**
     * main_control starts before serving (supervisor priority 10 vs 30) but priority does not
     * guarantee its HTTP port is accepting yet, so retry briefly instead of falling straight back.
     */
    private static final int CONTROL_FETCH_ATTEMPTS = 5;
    private static final long CONTROL_FETCH_BACKOFF_MS = 2_000;

    // -------------------------------------------------------------------------
    // DataSource configuration (DataSourceAutoConfiguration is excluded)
    // -------------------------------------------------------------------------

    /**
     * Binds spring.datasource.* connection properties (url, username, password, driver).
     * DataSourceAutoConfiguration is excluded so we own the full DataSource lifecycle.
     *
     * <p>These are the FALLBACK values only — see {@link #defaultDataSource}. They ship blank
     * so a stale hardcoded address can never silently win over main_control.</p>
     */
    @Bean
    @ConfigurationProperties("spring.datasource")
    public DataSourceProperties dataSourceProperties() {
        return new DataSourceProperties();
    }

    /**
     * Default HikariCP pool — the non-routed global tables ({@code operator_paradigm*}) and any
     * domain without an inline {@code database} block.
     *
     * <p>Its address comes from main_control ({@code GET /api/v1/system/database} → the
     * {@code default} block of {@code system/database.yaml}), the same single source of truth
     * mining reads. It used to be hardcoded in application.yml, which meant editing the YAML on
     * the host changed every service EXCEPT this pool — the jar is baked into the image and no
     * bind-mount covers it, so serving silently kept talking to the old database.</p>
     *
     * <p>Fallback order: main_control (5 attempts, 2s apart) → {@code spring.datasource.url} →
     * hard failure. The last step is deliberate: an unresolved address must stop startup rather
     * than let serving connect somewhere stale. Local dev / tests supply the fallback via
     * {@code SPRING_DATASOURCE_URL} or a test profile.</p>
     *
     * <p>Pool sizing: {@code pool_min}/{@code pool_max} from main_control, else the constants
     * above, and {@code spring.datasource.hikari.*} still binds last so a profile can override.
     * Setters are safe here — a Hikari pool only seals on first {@code getConnection()}, which
     * happens well after property binding.</p>
     */
    @Bean("defaultDataSource")
    @ConfigurationProperties("spring.datasource.hikari")
    public HikariDataSource defaultDataSource(DataSourceProperties dataSourceProperties,
                                              MainControlClient mainControlClient,
                                              ServingProperties properties) {
        DatabaseConfig remote = properties.mainControl().defaultDatabaseEnabled()
                ? fetchDefaultDatabaseWithRetry(mainControlClient)
                : null;
        HikariDataSource ds;

        if (remote != null) {
            ds = new HikariDataSource();
            ds.setJdbcUrl(remote.resolvedJdbcUrl());
            if (remote.user() != null) ds.setUsername(remote.user());
            if (remote.password() != null) ds.setPassword(remote.password());
            ds.setMinimumIdle(remote.poolMin() != null ? remote.poolMin() : DEFAULT_POOL_MIN);
            ds.setMaximumPoolSize(remote.poolMax() != null ? remote.poolMax() : DEFAULT_POOL_MAX);
            log.info("Default DataSource from main_control: {}", remote.resolvedJdbcUrl());
        } else {
            String url = dataSourceProperties.getUrl();
            if (url == null || url.isBlank()) {
                throw new IllegalStateException(
                        "default_datasource_unresolved: main_control did not serve "
                        + "system/database.yaml's 'default' block and no spring.datasource.url "
                        + "fallback is configured (set SPRING_DATASOURCE_URL to override)");
            }
            ds = dataSourceProperties.initializeDataSourceBuilder()
                    .type(HikariDataSource.class)
                    .build();
            ds.setMinimumIdle(DEFAULT_POOL_MIN);
            ds.setMaximumPoolSize(DEFAULT_POOL_MAX);
            if (properties.mainControl().defaultDatabaseEnabled()) {
                log.warn("main_control unreachable — default DataSource fell back to "
                         + "spring.datasource.url={} (may be stale)", url);
            } else {
                log.info("Default DataSource from spring.datasource.url={} "
                         + "(main_control default-database-enabled=false)", url);
            }
        }

        ds.setPoolName("hikari-default");
        ds.setConnectionTimeout(DEFAULT_CONNECTION_TIMEOUT_MS);
        return ds;
    }

    /**
     * @return the default database block, or null once every attempt has failed.
     *         Overridable so tests can exercise {@link #defaultDataSource}'s fallback branches
     *         without paying the backoff sleeps.
     */
    protected DatabaseConfig fetchDefaultDatabaseWithRetry(MainControlClient mainControlClient) {
        for (int attempt = 1; attempt <= CONTROL_FETCH_ATTEMPTS; attempt++) {
            try {
                return mainControlClient.fetchDefaultDatabase();
            } catch (MainControlClient.ConfigFetchException e) {
                if (attempt == CONTROL_FETCH_ATTEMPTS) {
                    log.warn("Default database unavailable from main_control after {} attempts: {}",
                            attempt, e.getMessage());
                    return null;
                }
                log.info("main_control not ready for default database (attempt {}/{}): {}",
                        attempt, CONTROL_FETCH_ATTEMPTS, e.getMessage());
                try {
                    Thread.sleep(CONTROL_FETCH_BACKOFF_MS);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return null;
                }
            }
        }
        return null;
    }

    /**
     * Primary DataSource that routes every JDBC connection to the pool owned by the
     * current request's domain. Falls back to {@code defaultDataSource} when no domain
     * is set on the thread (health checks, startup probes, test setup).
     *
     * <p>Named "dataSource" as an alias so that Spring Boot autoconfiguration classes
     * (JdbcTemplateAutoConfiguration, DataSourceTransactionManagerAutoConfiguration)
     * that look up {@code @Qualifier("dataSource")} find this routing bean rather than
     * failing with NoSuchBeanDefinitionException.
     */
    @Bean({"dataSource", "domainRoutingDataSource"})
    @Primary
    public DataSource domainRoutingDataSource(
            DomainPoolManager poolManager,
            @Qualifier("defaultDataSource") DataSource defaultDataSource) {
        return new DomainRoutingDataSource(poolManager, defaultDataSource);
    }

    // -------------------------------------------------------------------------
    // Infrastructure clients
    // -------------------------------------------------------------------------

    @Bean
    public RestTemplate restTemplate() {
        PoolingHttpClientConnectionManager connPool = new PoolingHttpClientConnectionManager();
        connPool.setDefaultConnectionConfig(ConnectionConfig.custom()
                .setConnectTimeout(Timeout.ofSeconds(5))
                .setSocketTimeout(Timeout.ofSeconds(60))
                .build());
        connPool.setMaxTotal(30);
        connPool.setDefaultMaxPerRoute(15);

        CloseableHttpClient httpClient = HttpClients.custom()
                .setConnectionManager(connPool)
                .build();

        HttpComponentsClientHttpRequestFactory factory = new HttpComponentsClientHttpRequestFactory(httpClient);
        return new RestTemplate(factory);
    }

    @Bean
    public MainControlClient mainControlClient(RestTemplate restTemplate, ServingProperties properties) {
        return new MainControlClient(restTemplate, properties.mainControl().baseUrl());
    }

    @Bean
    public LlmClient llmClient(RestTemplate restTemplate, ServingProperties properties) {
        LlmClient client = new LlmClient(restTemplate, properties.llm().baseUrl());
        if (client.isAvailable()) {
            // Background thread: wait for llm_service to be ready, then register templates.
            // llm_service may not have started yet (supervisor launches all services concurrently),
            // so we retry with backoff instead of failing silently.
            String baseUrl = properties.llm().baseUrl();
            Thread.ofVirtual().name("template-register").start(() -> {
                client.ensureTemplatesWithRetry(baseUrl);
            });
        } else {
            log.warn("LLM base-url is blank — template registration skipped");
        }
        return client;
    }

    @Bean
    public EmbeddingClient embeddingClient(LlmClient llmClient) {
        return new EmbeddingClient(llmClient);
    }

    // -------------------------------------------------------------------------
    // Retrieval layer
    // -------------------------------------------------------------------------

    @Bean
    public FtsRetriever ftsRetriever(AssetRetrievalUnitMapper retrievalUnitMapper) {
        return new FtsRetriever(retrievalUnitMapper);
    }

    @Bean
    public DenseVectorRetriever denseVectorRetriever(AssetRetrievalEmbeddingMapper embeddingMapper) {
        return new DenseVectorRetriever(embeddingMapper);
    }

    @Bean
    public EntityExactRetriever entityExactRetriever(AssetRetrievalUnitMapper retrievalUnitMapper) {
        return new EntityExactRetriever(retrievalUnitMapper);
    }

    // 批次8 R6：GraphExpander（旧 segment relation expander）随 ContextAssembler 一起删除
    // （25 号 §11.1——assemble 不再做关系扩展）。

    @Bean
    public Executor pipelineExecutor() {
        return Executors.newVirtualThreadPerTaskExecutor();
    }

    // -------------------------------------------------------------------------
    // Rerank layer
    // -------------------------------------------------------------------------

    @Bean
    public LlmServiceReranker llmServiceReranker(LlmClient llmClient) {
        return new LlmServiceReranker(llmClient);
    }

}
