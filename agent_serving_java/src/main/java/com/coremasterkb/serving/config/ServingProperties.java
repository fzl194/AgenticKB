package com.coremasterkb.serving.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "serving")
public record ServingProperties(
    String scenarioPacksDir,
    String domainRegistryPath,
    String defaultDomain,
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
        // 瘦身批次4：uploadRoot（原 /api/v1/documents/{id}/raw 原件直读）已随 RawFileService
        // 退役——KB 文件下载走 mining 的 KB 文档接口。
        if (llm == null) llm = new LlmConfig("");
        if (mainControl == null) mainControl = new MainControl("http://localhost:8910", Boolean.TRUE);
        if (evidenceRef == null) evidenceRef = new EvidenceRef("");
        if (internalAuth == null) internalAuth = new InternalAuth("");
    }
}
