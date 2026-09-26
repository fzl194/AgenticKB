package com.coremasterkb.serving.infrastructure;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class ControlPlaneInternalAuthTest {
    @TempDir Path tempDir;

    @Test
    void environmentTakesPriorityOverLocalFile() throws Exception {
        Path auth = tempDir.resolve("auth.yaml");
        Files.writeString(auth, "internal_verify_secret: file-secret\n");
        assertThat(ControlPlaneInternalAuth.resolve(
                Map.of(ControlPlaneInternalAuth.SECRET_ENV, "env-secret"), List.of(auth)))
                .isEqualTo("env-secret");
    }

    @Test
    void localAuthYamlIsCompatibleFallback() throws Exception {
        Path auth = tempDir.resolve("auth.yaml");
        Files.writeString(auth, "internal_verify_secret: file-secret\n");
        assertThat(ControlPlaneInternalAuth.resolve(Map.of(), List.of(auth)))
                .isEqualTo("file-secret");
    }
}
