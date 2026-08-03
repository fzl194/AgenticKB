package com.coremasterkb.serving.api;

import com.coremasterkb.serving.application.SearchService;
import com.coremasterkb.serving.domain.ContextPack;
import com.coremasterkb.serving.domain.SearchRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.Map;

@RestController
@RequestMapping("/api/v1")
public class SearchController {

    private final SearchService searchService;

    public SearchController(SearchService searchService) {
        this.searchService = searchService;
    }

    @PostMapping("/search")
    public ResponseEntity<Map<String, Object>> search(
            @RequestBody SearchRequest request,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        ContextPack pack = searchService.search(request, kbUser);

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("query", pack.query());
        response.put("items", pack.items());
        response.put("relations", pack.relations());
        response.put("sources", pack.sources());
        response.put("evidence_groups", pack.evidenceGroups());
        response.put("issues", pack.issues());
        response.put("suggestions", pack.suggestions());

        if (Boolean.TRUE.equals(request.debug()) && pack.debug() != null) {
            response.put("debug", pack.debug());
        }

        return ResponseEntity.ok(response);
    }
}
