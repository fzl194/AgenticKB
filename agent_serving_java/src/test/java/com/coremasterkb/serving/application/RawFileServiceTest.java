package com.coremasterkb.serving.application;

import com.coremasterkb.serving.config.ServingProperties;
import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.mapper.result.DocumentFileRow;
import com.coremasterkb.serving.repository.AssetRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

/**
 * A real temp directory rather than a mocked filesystem: the whole point of the traversal guard is
 * what {@code Path} normalization actually does, and a mock would assert my idea of it instead.
 */
@DisplayName("RawFileService")
class RawFileServiceTest {

    private static final String DOMAIN = "cloud_core_network";

    @TempDir
    Path uploadRoot;

    private AssetRepository repo;
    private ScopeResolver scopeResolver;
    private RawFileService service;

    @BeforeEach
    void setUp() {
        repo = mock(AssetRepository.class);
        scopeResolver = mock(ScopeResolver.class);
        service = new RawFileService(repo, scopeResolver,
                new ServingProperties(null, null, DOMAIN, uploadRoot.toString(), null, null, null, null));
    }

    // ---------------------------------------------------------------- helpers

    /** Scope in which doc-1 is visible. */
    private void scopeSeesDoc1() {
        when(scopeResolver.resolve(eq(DOMAIN), any(), any(), any(), any(), any()))
                .thenReturn(new ActiveScope(
                        "rel-1", "build-1", List.of("snap-1"), Map.of("doc-1", "snap-1")));
    }

    private static DocumentFileRow row(String id, String kbId, String storagePath, String name) {
        DocumentFileRow row = new DocumentFileRow();
        row.setId(id);
        row.setKbId(kbId);
        row.setStoragePath(storagePath);
        row.setDocumentName(name);
        return row;
    }

    private Path writeFile(String kbId, String filename, String content) throws Exception {
        Path dir = Files.createDirectories(uploadRoot.resolve(kbId));
        Path file = dir.resolve(filename);
        Files.writeString(file, content);
        return file;
    }

    private RawFileService.RawFile resolveDoc1() {
        return service.resolve("doc-1", DOMAIN, null, null, null, null, "alice");
    }

    // ---------------------------------------------------------------- happy path

    @Test
    @DisplayName("a KB document streams with its own name and a content type from the extension")
    void streamsDocument() throws Exception {
        Path file = writeFile("kb-a", "spec.pdf", "PDF-BYTES");
        scopeSeesDoc1();
        when(repo.resolveFileLocations(eq(List.of("doc-1")), any()))
                .thenReturn(List.of(row("doc-1", "kb-a", file.toString(), "3GPP 23501.pdf")));

        RawFileService.RawFile result = resolveDoc1();

        assertThat(result.path()).isEqualTo(file.toAbsolutePath().normalize());
        assertThat(result.filename()).isEqualTo("3GPP 23501.pdf");
        assertThat(result.contentType()).isEqualTo("application/pdf");
        assertThat(result.size()).isEqualTo(Files.size(file));
    }

    @Test
    @DisplayName("an unknown extension falls back to octet-stream rather than guessing")
    void unknownExtension() throws Exception {
        Path file = writeFile("kb-a", "dump.bin", "x");
        scopeSeesDoc1();
        when(repo.resolveFileLocations(any(), any()))
                .thenReturn(List.of(row("doc-1", "kb-a", file.toString(), "dump.bin")));

        assertThat(resolveDoc1().contentType()).isEqualTo("application/octet-stream");
    }

    // ---------------------------------------------------------------- visibility

    @Test
    @DisplayName("a document outside the caller's scope is not found — and is never queried")
    void outOfScopeDocument() {
        when(scopeResolver.resolve(eq(DOMAIN), any(), any(), any(), any(), any()))
                .thenReturn(new ActiveScope(
                        "rel-1", "build-1", List.of("snap-1"), Map.of("doc-other", "snap-1")));

        assertThatThrownBy(this::resolveDoc1)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("document_not_found");

        verify(repo, never()).resolveFileLocations(any(), any());
    }

    @Test
    @DisplayName("a rejected scope propagates instead of degrading to a 404")
    void scopeRejectionPropagates() {
        when(scopeResolver.resolve(eq(DOMAIN), any(), any(), any(), any(), any()))
                .thenThrow(new IllegalArgumentException("kb_not_found"));

        assertThatThrownBy(this::resolveDoc1)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_not_found");
    }

    // ---------------------------------------------------------------- no file

    @Test
    @DisplayName("a legacy document has no original file, and says so distinctly")
    void legacyDocumentHasNoFile() {
        scopeSeesDoc1();
        // Ingested through /api/runs: no kb_id, no storage_path, no uploaded file ever existed.
        when(repo.resolveFileLocations(any(), any()))
                .thenReturn(List.of(row("doc-1", null, null, "legacy.md")));

        assertThatThrownBy(this::resolveDoc1)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("raw_file_unavailable");
    }

    @Test
    @DisplayName("a row pointing at a file that is gone reports raw_file_unavailable")
    void missingFileOnDisk() {
        scopeSeesDoc1();
        String vanished = uploadRoot.resolve("kb-a").resolve("gone.pdf").toString();
        when(repo.resolveFileLocations(any(), any()))
                .thenReturn(List.of(row("doc-1", "kb-a", vanished, "gone.pdf")));

        assertThatThrownBy(this::resolveDoc1)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("raw_file_unavailable");
    }

    // ---------------------------------------------------------------- guards

    @Test
    @DisplayName("a stored path escaping its KB directory is refused, not served")
    void traversalOutsideKbDirectory() throws Exception {
        // A path that resolves outside uploadRoot/kb-a. It should never occur — the upload flow
        // writes these rows — but an unchecked path from the database is a read of any file the
        // process can open, so the guard has to hold even when the data is wrong.
        Path outside = Files.writeString(uploadRoot.resolve("secrets.txt"), "not yours");
        scopeSeesDoc1();
        when(repo.resolveFileLocations(any(), any()))
                .thenReturn(List.of(row("doc-1", "kb-a", outside.toString(), "secrets.txt")));

        assertThatThrownBy(this::resolveDoc1)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("document_not_found");
    }

    @Test
    @DisplayName("a sibling KB's file is refused even though it is under the upload root")
    void siblingKbDirectoryIsNotReachable() throws Exception {
        Path otherKbFile = writeFile("kb-b", "private.pdf", "not yours");
        scopeSeesDoc1();
        when(repo.resolveFileLocations(any(), any()))
                .thenReturn(List.of(row("doc-1", "kb-a", otherKbFile.toString(), "private.pdf")));

        assertThatThrownBy(this::resolveDoc1)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("document_not_found");
    }

    @Test
    @DisplayName("a ../ segment is normalized away before the check, not after")
    void dotDotIsNormalized() throws Exception {
        Files.createDirectories(uploadRoot.resolve("kb-a"));
        Path escaping = uploadRoot.resolve("kb-a").resolve("..").resolve("secrets.txt");
        Files.writeString(uploadRoot.resolve("secrets.txt"), "not yours");
        scopeSeesDoc1();
        when(repo.resolveFileLocations(any(), any()))
                .thenReturn(List.of(row("doc-1", "kb-a", escaping.toString(), "secrets.txt")));

        assertThatThrownBy(this::resolveDoc1)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("document_not_found");
    }

    @Test
    @DisplayName("a symlink inside the KB directory cannot point out of it")
    void symlinkOutOfKbDirectoryIsRefused() throws Exception {
        // normalize() alone would pass this: the path string starts with uploadRoot/kb-a and only
        // the filesystem knows it leads elsewhere. Python's Path.resolve() on the mining side does
        // resolve links, so anything weaker here would not be the same check.
        Path outside = Files.writeString(uploadRoot.resolve("secrets.txt"), "not yours");
        Path kbDir = Files.createDirectories(uploadRoot.resolve("kb-a"));
        Path link = kbDir.resolve("innocent.pdf");
        try {
            Files.createSymbolicLink(link, outside);
        } catch (UnsupportedOperationException | java.io.IOException e) {
            // Windows needs developer mode or elevation to create symlinks.
            org.junit.jupiter.api.Assumptions.assumeTrue(false, "symlinks unavailable here");
        }

        scopeSeesDoc1();
        when(repo.resolveFileLocations(any(), any()))
                .thenReturn(List.of(row("doc-1", "kb-a", link.toString(), "innocent.pdf")));

        assertThatThrownBy(this::resolveDoc1)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("document_not_found");
    }

    @Test
    @DisplayName("a missing upload root is a 503 deployment fault, not a per-document 404")
    void missingUploadRootIsDistinct() {
        RawFileService broken = new RawFileService(repo, scopeResolver,
                new ServingProperties(null, null, DOMAIN,
                        uploadRoot.resolve("does-not-exist").toString(), null, null, null, null));

        // Told apart from raw_file_unavailable on purpose: a misconfigured upload root otherwise
        // presents as "none of your documents have files", which reads like a data problem.
        assertThatThrownBy(() -> broken.resolve("doc-1", DOMAIN, null, null, null, null, "alice"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("raw_file_storage_unavailable");

        verifyNoInteractions(scopeResolver, repo);
    }
}
