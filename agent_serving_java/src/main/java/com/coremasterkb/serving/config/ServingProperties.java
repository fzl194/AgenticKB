package com.coremasterkb.serving.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "serving")
public record ServingProperties(
    String scenarioPacksDir,
    String domainRegistryPath,
    String defaultDomain,
    String uploadRoot,
    LlmConfig llm,
    MainControl mainControl,
    EvidenceRef evidenceRef,
    InternalAuth internalAuth
) {
    public record LlmConfig(String baseUrl) {
        public LlmConfig {
            if (baseUrl == null) baseUrl = "";
        }
    }

    /**
     * Config source: main_control's base URL (e.g. http://localhost:8910).
     *
     * @param defaultDatabaseEnabled whether the default DataSource takes its address from
     *        main_control ({@code system/database.yaml}'s {@code default} block). True in
     *        production — that file is the single source of truth. Set false where the process
     *        must own its own database regardless of what a main_control on this host would
     *        hand back: integration tests point {@code spring.datasource.*} at a throwaway DB,
     *        and picking up a developer's running main_control instead would run the startup
     *        DDL against the real one.
     */
    public record MainControl(String baseUrl, Boolean defaultDatabaseEnabled) {
        public MainControl {
            if (baseUrl == null || baseUrl.isBlank()) baseUrl = "http://localhost:8910";
            if (defaultDatabaseEnabled == null) defaultDatabaseEnabled = Boolean.TRUE;
        }
    }

    /**
     * EvidenceResponse opaque ref（ev_/doc_/st_ 前缀 HMAC 短哈希）的签名密钥。
     *
     * <p>生产经 {@code SERVING_EVIDENCE_REF_SECRET} 注入（建议与 main_control
     * {@code auth.yaml} 的 jwt_secret 同级的随机值，由部署脚本生成）；留空 = 进程内
     * 随机遇 boot 密钥（refs 重启后变化，仅适合开发/测试）。</p>
     */
    public record EvidenceRef(String secret) {
        public EvidenceRef {
            if (secret == null) secret = "";
        }
    }

    /**
     * 内部端点共享密钥（批次8 R7 {@code /api/internal/*}，对齐 mining 批次7
     * X-Internal-Auth 模式）。生产经 {@code SERVING_INTERNAL_AUTH_SECRET} 注入（mcp_server
     * 容器同值）；留空 = 内部端点整体 503（拒绝服务而非无鉴权放行）。
     */
    public record InternalAuth(String secret) {
        public InternalAuth {
            if (secret == null) secret = "";
        }
    }

    public ServingProperties {
        // 域配置的真相源是 main_control（HTTP）。下面两个本地路径只是 main_control
        // 不可达时的兜底（IntelliJ / 测试），指向它所拥有的同一份文件。
        if (scenarioPacksDir == null) scenarioPacksDir = "../main_control_service/config/scenario_packs";
        if (domainRegistryPath == null) domainRegistryPath = "../main_control_service/config/domain_registry.yaml";
        if (defaultDomain == null) defaultDomain = "cloud_core_network";
        // ⚠️ 必须与 mining 的 upload.root 指向同一个目录（system/mining.yaml 里是 ./uploads，
        // 容器内 cwd=/app）。两处没有共享真相源，会漂移——所以 RawFileService 启动即校验目录
        // 存在，不存在时整个端点返回 503 而不是逐个文档 404，好把「配置错」和「这份文档本来
        // 就没有原件」分开。真要消灭漂移，得把它并进 main_control 的 /api/v1/serving-config
        // 快照下发，那要同步改 MainControlClient 与 ConfigReloadService 两份平行解析。
        if (uploadRoot == null || uploadRoot.isBlank()) uploadRoot = "/app/uploads";
        if (llm == null) llm = new LlmConfig("");
        if (mainControl == null) mainControl = new MainControl("http://localhost:8910", Boolean.TRUE);
        if (evidenceRef == null) evidenceRef = new EvidenceRef("");
        if (internalAuth == null) internalAuth = new InternalAuth("");
    }
}
