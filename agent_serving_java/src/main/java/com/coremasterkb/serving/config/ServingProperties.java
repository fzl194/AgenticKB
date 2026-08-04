package com.coremasterkb.serving.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "serving")
public record ServingProperties(
    String scenarioPacksDir,
    String domainRegistryPath,
    String defaultDomain,
    String uploadRoot,
    LlmConfig llm,
    MainControl mainControl
) {
    public record LlmConfig(String baseUrl) {
        public LlmConfig {
            if (baseUrl == null) baseUrl = "";
        }
    }

    /** Config source: main_control's base URL (e.g. http://localhost:8910). */
    public record MainControl(String baseUrl) {
        public MainControl {
            if (baseUrl == null || baseUrl.isBlank()) baseUrl = "http://localhost:8910";
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
        if (mainControl == null) mainControl = new MainControl("http://localhost:8910");
    }
}
