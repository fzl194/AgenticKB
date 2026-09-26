package com.coremasterkb.serving.infrastructure;

import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;

import java.io.IOException;
import java.io.Reader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;

/** Resolves the credential used only for reading trusted control-plane config. */
final class ControlPlaneInternalAuth {
    static final String SECRET_ENV = "CONTROL_PLANE_INTERNAL_AUTH_SECRET";
    static final String AUTH_PATH_ENV = "CONTROL_PLANE_AUTH_CONFIG_PATH";

    private ControlPlaneInternalAuth() {}

    static String load() {
        Map<String, String> env = System.getenv();
        List<Path> paths = new ArrayList<>();
        String configured = env.getOrDefault(AUTH_PATH_ENV, "").trim();
        if (!configured.isEmpty()) paths.add(Path.of(configured));
        paths.add(Path.of("main_control_service/config/system/auth.yaml"));
        paths.add(Path.of("../main_control_service/config/system/auth.yaml"));
        return resolve(env, List.copyOf(new LinkedHashSet<>(paths)));
    }

    static String resolve(Map<String, String> env, List<Path> paths) {
        String fromEnv = valid(env.get(SECRET_ENV));
        if (!fromEnv.isEmpty()) return fromEnv;
        Yaml yaml = new Yaml(new SafeConstructor(new LoaderOptions()));
        for (Path path : paths) {
            try (Reader reader = Files.newBufferedReader(path)) {
                Object loaded = yaml.load(reader);
                if (loaded instanceof Map<?, ?> data) {
                    String fromFile = valid(data.get("internal_verify_secret"));
                    if (!fromFile.isEmpty()) return fromFile;
                }
            } catch (IOException | RuntimeException ignored) {
                // Try the next colocated path; empty result makes main_control fail closed.
            }
        }
        return "";
    }

    private static String valid(Object value) {
        String secret = value instanceof String text ? text.trim() : "";
        return secret.isEmpty() || secret.startsWith("change-me") ? "" : secret;
    }
}
