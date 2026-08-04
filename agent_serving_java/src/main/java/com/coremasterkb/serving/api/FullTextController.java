package com.coremasterkb.serving.api;

import com.coremasterkb.serving.application.FullTextService;
import com.coremasterkb.serving.domain.FullTextRequest;
import com.coremasterkb.serving.domain.FullTextResponse;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

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

    public FullTextController(FullTextService fullTextService) {
        this.fullTextService = fullTextService;
    }

    @PostMapping("/segments/fulltext")
    public ResponseEntity<FullTextResponse> fulltext(
            @RequestBody FullTextRequest request,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        return ResponseEntity.ok(fullTextService.fetch(request, kbUser));
    }
}
