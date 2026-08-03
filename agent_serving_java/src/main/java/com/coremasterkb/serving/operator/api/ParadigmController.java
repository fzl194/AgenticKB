package com.coremasterkb.serving.operator.api;

import com.coremasterkb.serving.operator.paradigm.ParadigmEntity;
import com.coremasterkb.serving.operator.paradigm.ParadigmService;
import com.coremasterkb.serving.operator.paradigm.ParadigmVersionEntity;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Paradigm management + stored execution (PRD §11.2/§11.3). Persisted paradigms are created as
 * drafts, published into immutable versions, and called by {@code id + version}. Runs alongside
 * the inline {@link ParadigmRunController} and the existing {@code /api/v1/search}.
 */
@RestController
@RequestMapping("/api/v1/paradigm")
public class ParadigmController {

    private final ParadigmService paradigmService;
    private final ParadigmExecutionService executionService;
    private final ObjectMapper mapper = new ObjectMapper();

    public ParadigmController(ParadigmService paradigmService, ParadigmExecutionService executionService) {
        this.paradigmService = paradigmService;
        this.executionService = executionService;
    }

    // ---- CRUD ----------------------------------------------------------------------------

    /** Create a draft paradigm. Body: {@code {name, description?, graph?}}. */
    @PostMapping
    public Map<String, Object> create(@RequestBody JsonNode body) {
        ParadigmEntity e = paradigmService.create(
                ParadigmRequests.text(body, "name"),
                ParadigmRequests.text(body, "description"),
                ParadigmRequests.graphString(body));
        return paradigmView(e);
    }

    @GetMapping
    public Map<String, Object> list() {
        List<Map<String, Object>> views = paradigmService.listAll().stream().map(this::paradigmView).toList();
        return Map.of("paradigms", views);
    }

    /**
     * List only published (currently active) paradigms — the ones the test system can call.
     * Each entry: id, name, description, version (current published version), url (the search endpoint;
     * calling it with no {@code ?version} runs the paradigm's current_version).
     */
    @GetMapping("/published")
    public Map<String, Object> listPublished() {
        List<Map<String, Object>> views =
                paradigmService.listPublished().stream().map(this::publishedView).toList();
        return Map.of("paradigms", views);
    }

    @GetMapping("/{id}")
    public Map<String, Object> get(@PathVariable String id) {
        return paradigmView(paradigmService.getOrThrow(id));
    }

    /** Replace the editable draft graph. Body: {@code {graph}}. */
    @PutMapping("/{id}")
    public Map<String, Object> updateDraft(@PathVariable String id, @RequestBody JsonNode body) {
        return paradigmView(paradigmService.updateDraft(id, ParadigmRequests.graphString(body)));
    }

    // ---- versions / publish / rollback ---------------------------------------------------

    @GetMapping("/{id}/versions")
    public Map<String, Object> versions(@PathVariable String id) {
        List<Map<String, Object>> views =
                paradigmService.listVersions(id).stream().map(this::versionView).toList();
        return Map.of("versions", views);
    }

    @GetMapping("/{id}/versions/{version}")
    public Map<String, Object> version(@PathVariable String id, @PathVariable int version) {
        return versionView(paradigmService.getVersionOrThrow(id, version));
    }

    /** Publish: compile-validate the draft, snapshot it as a new immutable version, activate it. */
    @PostMapping("/{id}/publish")
    public Map<String, Object> publish(@PathVariable String id, @RequestBody(required = false) JsonNode body) {
        String createdBy = ParadigmRequests.text(body, "createdBy");
        return versionView(paradigmService.publish(id, createdBy));
    }

    @PostMapping("/{id}/rollback")
    public Map<String, Object> rollback(@PathVariable String id, @RequestParam int version) {
        return paradigmView(paradigmService.rollback(id, version));
    }

    /** Archive a paradigm (status → archived). */
    @PostMapping("/{id}/archive")
    public Map<String, Object> archive(@PathVariable String id) {
        return paradigmView(paradigmService.archive(id));
    }

    /** Permanently delete a paradigm + all its versions. Only allowed when already archived. */
    @DeleteMapping("/{id}")
    public Map<String, Object> delete(@PathVariable String id) {
        paradigmService.delete(id);
        return Map.of("deleted", true, "id", id);
    }

    // ---- execution -----------------------------------------------------------------------

    /** Execute a published version (or current_version if unspecified). Body: run args. */
    @PostMapping("/{id}/search")
    public Map<String, Object> search(
            @PathVariable String id,
            @RequestParam(required = false) Integer version,
            @RequestBody JsonNode body,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        JsonNode graph = paradigmService.resolveExecutableGraph(id, version);
        return executionService.run(graph, ParadigmRequests.toRunArgs(body, kbUser));
    }

    /** Compile-validate the draft (no execution). */
    @PostMapping("/{id}/validate")
    public Map<String, Object> validateDraft(@PathVariable String id) {
        JsonNode draft = paradigmService.resolveDraftGraph(id);
        var errors = paradigmService.validateDraft(draft);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("valid", errors.isEmpty());
        result.put("errors", errors);
        return result;
    }

    /** Execute the draft without persisting results (preview while editing). Body: run args. */
    @PostMapping("/{id}/dryrun")
    public Map<String, Object> dryRun(
            @PathVariable String id,
            @RequestBody JsonNode body,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        JsonNode draft = paradigmService.resolveDraftGraph(id);
        return executionService.run(draft, ParadigmRequests.toRunArgs(body, kbUser));
    }

    // ---- views ---------------------------------------------------------------------------

    /** Compact view for published paradigms: id / name / description / version / call url. */
    private Map<String, Object> publishedView(ParadigmEntity e) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", e.getId());
        m.put("name", e.getName());
        m.put("description", e.getDescription());
        m.put("version", e.getCurrentVersion());
        m.put("url", "/api/v1/paradigm/" + e.getId() + "/search");
        return m;
    }

    private Map<String, Object> paradigmView(ParadigmEntity e) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", e.getId());
        m.put("name", e.getName());
        m.put("description", e.getDescription());
        m.put("status", e.getStatus());
        m.put("currentVersion", e.getCurrentVersion());
        m.put("draftGraph", parseOrNull(e.getDraftGraphJson()));
        m.put("createdAt", e.getCreatedAt());
        m.put("updatedAt", e.getUpdatedAt());
        return m;
    }

    private Map<String, Object> versionView(ParadigmVersionEntity v) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", v.getId());
        m.put("paradigmId", v.getParadigmId());
        m.put("version", v.getVersion());
        m.put("schemaVersion", v.getSchemaVersion());
        m.put("graph", parseOrNull(v.getGraphJson()));
        m.put("createdAt", v.getCreatedAt());
        m.put("createdBy", v.getCreatedBy());
        return m;
    }

    private JsonNode parseOrNull(String json) {
        if (json == null || json.isBlank()) return null;
        try {
            return mapper.readTree(json);
        } catch (Exception e) {
            return null;
        }
    }
}
