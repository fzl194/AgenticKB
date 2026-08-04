package com.coremasterkb.serving.api;

import com.coremasterkb.serving.application.FullTextService;
import com.coremasterkb.serving.application.RawFileService;
import com.coremasterkb.serving.domain.FullTextRequest;
import com.coremasterkb.serving.domain.FullTextResponse;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * Drill down from a retrieval result to the text actually stored.
 *
 * <p>Batched by design: a caller reviewing one result set usually needs several items expanded,
 * and one round-trip per item is the difference between an interactive drawer and a spinner.</p>
 */
@RestController
@RequestMapping("/api/v1")
public class FullTextController {

    private final FullTextService fullTextService;
    private final RawFileService rawFileService;

    public FullTextController(FullTextService fullTextService, RawFileService rawFileService) {
        this.fullTextService = fullTextService;
        this.rawFileService = rawFileService;
    }

    @PostMapping("/segments/fulltext")
    public ResponseEntity<FullTextResponse> fulltext(
            @RequestBody FullTextRequest request,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        return ResponseEntity.ok(fullTextService.fetch(request, kbUser));
    }

    /**
     * Stream a document's original uploaded file.
     *
     * <p>GET with query parameters rather than POST with a body so the URL can be used directly by
     * an {@code <a download>} or an inline preview frame, which cannot issue a POST.</p>
     */
    @GetMapping("/documents/{documentId}/raw")
    public ResponseEntity<Resource> raw(
            @PathVariable String documentId,
            @RequestParam(required = false) String domain,
            @RequestParam(required = false) String channel,
            @RequestParam(required = false) String paradigmId,
            @RequestParam(required = false) Integer paradigmVersion,
            @RequestParam(required = false) List<String> kbIds,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {

        RawFileService.RawFile file = rawFileService.resolve(
                documentId, domain, channel, paradigmId, paradigmVersion, kbIds, kbUser);

        // filename* (RFC 5987) rather than plain filename: document names are routinely Chinese,
        // and a bare filename= would arrive mojibake or get dropped.
        ContentDisposition disposition = ContentDisposition.attachment()
                .filename(file.filename(), StandardCharsets.UTF_8)
                .build();

        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION, disposition.toString())
                .contentType(MediaType.parseMediaType(file.contentType()))
                .contentLength(file.size())
                .body(new FileSystemResource(file.path()));
    }
}
