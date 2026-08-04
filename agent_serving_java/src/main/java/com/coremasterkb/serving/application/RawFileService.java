package com.coremasterkb.serving.application;

import com.coremasterkb.serving.config.ServingProperties;
import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domainpack.DomainContext;
import com.coremasterkb.serving.mapper.result.DocumentFileRow;
import com.coremasterkb.serving.repository.AssetRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.InvalidPathException;
import java.nio.file.Path;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Stream a document's original uploaded file.
 *
 * <p>The file already exists on disk — knowledge-base uploads land under
 * {@code {upload_root}/{kb_id}/…} and mining reads them from there. Serving streams it rather than
 * redirecting to mining's own download endpoint because all six services share one container and
 * therefore one {@code uploads} volume, and because {@code mcp_server} talks to serving directly,
 * bypassing the control plane: sending an agent to a second service on a second port, with a
 * different identity header, to fetch a file that is right here would be a hop for its own sake.</p>
 *
 * <p>This complements {@link FullTextService} rather than replacing it. The original file is the
 * authoritative artifact — formatting, tables and figures intact — but nobody can locate a
 * particular segment inside a 200-page PDF, which is exactly what the segment lookup is for.</p>
 */
@Service
public class RawFileService {

    private static final Logger log = LoggerFactory.getLogger(RawFileService.class);

    private static final Map<String, String> CONTENT_TYPES = Map.ofEntries(
            Map.entry("pdf", "application/pdf"),
            Map.entry("txt", "text/plain;charset=UTF-8"),
            Map.entry("md", "text/markdown;charset=UTF-8"),
            Map.entry("csv", "text/csv;charset=UTF-8"),
            Map.entry("html", "text/html;charset=UTF-8"),
            Map.entry("htm", "text/html;charset=UTF-8"),
            Map.entry("json", "application/json;charset=UTF-8"),
            Map.entry("doc", "application/msword"),
            Map.entry("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            Map.entry("xls", "application/vnd.ms-excel"),
            Map.entry("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            Map.entry("ppt", "application/vnd.ms-powerpoint"),
            Map.entry("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
            Map.entry("png", "image/png"),
            Map.entry("jpg", "image/jpeg"),
            Map.entry("jpeg", "image/jpeg"),
            Map.entry("gif", "image/gif"),
            Map.entry("svg", "image/svg+xml"));

    private final AssetRepository assetRepository;
    private final ScopeResolver scopeResolver;
    private final String defaultDomain;
    private final Path uploadRoot;

    public RawFileService(AssetRepository assetRepository,
                          ScopeResolver scopeResolver,
                          ServingProperties properties) {
        this.assetRepository = assetRepository;
        this.scopeResolver = scopeResolver;
        this.defaultDomain = properties.defaultDomain();
        this.uploadRoot = Path.of(properties.uploadRoot());
    }

    /** A resolved file, ready to stream. */
    public record RawFile(Path path, String filename, String contentType, long size) {}

    /**
     * Resolve the original file of a document the caller may currently see.
     *
     * @throws IllegalStateException("raw_file_storage_unavailable") if the upload root is missing
     * @throws IllegalArgumentException("document_not_found") if the document is out of scope, or
     *         its stored path escapes its knowledge base's directory
     * @throws IllegalArgumentException("raw_file_unavailable") if the document has no original
     *         file (legacy ingest) or the file is gone from disk
     */
    public RawFile resolve(String documentId, String domain, String channel, String paradigmId,
                           Integer paradigmVersion, List<String> kbIds, String username) {
        // Distinguished from a per-document 404 deliberately: a misconfigured or unmounted upload
        // root would otherwise present as "none of your documents have files", which reads like a
        // data problem and sends whoever debugs it in entirely the wrong direction.
        if (!Files.isDirectory(uploadRoot)) {
            log.error("Upload root is not a directory: {} — check serving.upload-root against "
                    + "mining's upload.root", uploadRoot);
            throw new IllegalStateException("raw_file_storage_unavailable");
        }

        String effectiveDomain = (domain != null && !domain.isBlank()) ? domain : defaultDomain;
        DomainContext.set(effectiveDomain);
        try {
            ActiveScope scope = scopeResolver.resolve(
                    effectiveDomain, channel, paradigmId, paradigmVersion, kbIds, username);

            // Being in the scope's document map is what "the caller may see this document" means.
            if (!scope.documentSnapshotMap().containsKey(documentId)) {
                throw new IllegalArgumentException("document_not_found");
            }

            List<DocumentFileRow> rows =
                    assetRepository.resolveFileLocations(List.of(documentId), scope.snapshotIds());
            if (rows.isEmpty()) {
                throw new IllegalArgumentException("document_not_found");
            }
            DocumentFileRow row = rows.get(0);
            if (!row.hasRawFile()) {
                // Legacy documents came in through /api/runs and were never uploaded to a KB.
                throw new IllegalArgumentException("raw_file_unavailable");
            }

            Path file = safeResolve(row);
            if (!Files.isRegularFile(file)) {
                throw new IllegalArgumentException("raw_file_unavailable");
            }

            String filename = (row.getDocumentName() != null && !row.getDocumentName().isBlank())
                    ? row.getDocumentName()
                    : file.getFileName().toString();

            long size;
            try {
                size = Files.size(file);
            } catch (IOException e) {
                throw new IllegalArgumentException("raw_file_unavailable");
            }

            log.info("[raw-file] domain={} user={} document={} kb={} bytes={}",
                    effectiveDomain, username != null ? username : "<anonymous>",
                    documentId, row.getKbId(), size);

            return new RawFile(file, filename, contentTypeOf(filename), size);
        } finally {
            DomainContext.clear();
        }
    }

    /**
     * Confine the stored path to its own knowledge base's directory.
     *
     * <p>Mirrors the guard in mining's {@code DocumentService.download_path}. The path in the
     * database was written by that same upload flow, so this should never fire — which is the
     * reason to keep it: if a row ever is edited or corrupted, an unchecked path from the database
     * is a read of any file the process can open. Reported as {@code document_not_found}, not a
     * server error: the caller learns nothing either way, and the detail belongs in the log.</p>
     */
    private Path safeResolve(DocumentFileRow row) {
        Path base = uploadRoot.resolve(row.getKbId()).toAbsolutePath().normalize();
        Path candidate;
        try {
            candidate = Path.of(row.getStoragePath()).toAbsolutePath().normalize();
        } catch (InvalidPathException e) {
            log.warn("Unusable storage_path for document {}", row.getId());
            throw new IllegalArgumentException("document_not_found");
        }
        if (!candidate.startsWith(base)) {
            log.warn("storage_path escapes its KB directory: document={} kb={}",
                    row.getId(), row.getKbId());
            throw new IllegalArgumentException("document_not_found");
        }
        return candidate;
    }

    private static String contentTypeOf(String filename) {
        int dot = filename.lastIndexOf('.');
        if (dot < 0 || dot == filename.length() - 1) {
            return "application/octet-stream";
        }
        String ext = filename.substring(dot + 1).toLowerCase(Locale.ROOT);
        return CONTENT_TYPES.getOrDefault(ext, "application/octet-stream");
    }
}
