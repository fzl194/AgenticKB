package com.coremasterkb.serving.operator.api;

import com.coremasterkb.serving.operator.paradigm.ParadigmCatalogService;
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
    private final ParadigmCatalogService catalogService;
    private final ParadigmExecutionService executionService;
    private final ObjectMapper mapper = new ObjectMapper();

    public ParadigmController(ParadigmService paradigmService,
                              ParadigmCatalogService catalogService,
                              ParadigmExecutionService executionService) {
        this.paradigmService = paradigmService;
        this.catalogService = catalogService;
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

    /**
     * Auto-match lookup for callers (MCP): which paradigm should this search use?
     *
     * <p>阶段 A 四层判定（16 号方案 §2）：目标库库级绑定一致 → library；否则领域默认 →
     * domain；否则官方默认 → official；全无 → {@code bound:false}（调用方明确报错，MCP
     * 不再回落 legacy）。{@code kbIds} 可选：不传 = 未指定库，跳过 library 层。</p>
     *
     * <p>Returns 200 in both cases — {@code {"bound":true,...}} or {@code {"bound":false}}. An
     * unbound domain is a normal state, not an error; if it 404'd, a caller could not tell it apart
     * from a network failure or a wrong URL, and would have to treat "no paradigm configured"
     * and "the service is broken" identically.</p>
     *
     * <p>Mapped above {@code /{id}} deliberately: a literal segment outranks a path variable in
     * Spring's pattern comparator, so this never gets swallowed as {@code id="resolve"}. Pinned by
     * {@code ParadigmResolveWebMvcTest}.</p>
     */
    @GetMapping("/resolve")
    public Map<String, Object> resolve(
            @RequestParam String domain,
            @RequestParam(required = false) List<String> kbIds) {
        ParadigmService.Resolution r = paradigmService.resolveFor(domain, kbIds);
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("domain", domain);
        m.put("bound", r != null);
        if (r != null) {
            ParadigmEntity e = r.paradigm();
            m.put("paradigmId", e.getId());
            m.put("name", e.getName());
            m.put("description", e.getDescription());
            m.put("version", e.getCurrentVersion());
            m.put("url", "/api/v1/paradigm/" + e.getId() + "/search");
            m.put("source", r.source());
            if (r.degradedFrom() != null) {
                m.put("degraded", true);
                m.put("degradedFrom", r.degradedFrom());
            }
        }
        return m;
    }

    /**
     * Which published paradigms an anonymous MCP caller can actually use, and why the rest cannot.
     *
     * <p>Mapped above {@code /{id}} for the same reason as {@code /resolve}, and pinned by the same
     * kind of test.</p>
     *
     * <p>{@code hidden} is returned only to an identified caller. It names knowledge bases that an
     * anonymous one could not read, and {@code KbAccessService} deliberately does not reveal whether
     * a knowledge base exists — serving has no auth of its own, so the header is the only thing
     * separating "the operator debugging their paradigm" from "anyone who can reach the port". MCP
     * sends no header and never reads {@code hidden} anyway.</p>
     */
    @GetMapping("/mcp-catalog")
    public Map<String, Object> mcpCatalog(
            @RequestParam(required = false) String domain,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        ParadigmCatalogService.Catalog catalog = catalogService.build(domain, kbUser);
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("paradigms", catalog.paradigms());
        if (kbUser != null && !kbUser.isBlank()) {
            m.put("hidden", catalog.hidden());
        }
        return m;
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

    /**
     * Publish: compile-validate the draft, snapshot it as a new immutable version, activate it.
     *
     * <p>批次6：域绑定退役——body 的 {@code {domain, setDefault}} 同步绑定与
     * {@code PUT/DELETE /{id}/binding} 端点已移除（范式跨域通用，18 号方案 §1.3）。
     * 发布即全域可用；库级绑定在知识库侧（default_paradigm_id）管理。</p>
     */
    @PostMapping("/{id}/publish")
    public Map<String, Object> publish(@PathVariable String id, @RequestBody(required = false) JsonNode body) {
        String createdBy = ParadigmRequests.text(body, "createdBy");
        return new LinkedHashMap<>(versionView(paradigmService.publish(id, createdBy)));
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

    /**
     * Execute a published version (or current_version if unspecified). Body: run args.
     *
     * <p>Resolves the version row rather than just the graph so the query log can be attributed to
     * the <em>effective</em> version even when the caller said "current" — from the same read.</p>
     */
    @PostMapping("/{id}/search")
    public Map<String, Object> search(
            @PathVariable String id,
            @RequestParam(required = false) Integer version,
            @RequestBody JsonNode body,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        ParadigmVersionEntity ve = paradigmService.resolveExecutableVersion(id, version);
        JsonNode graph = parseOrNull(ve.getGraphJson());
        return executionService.run(
                graph, ParadigmRequests.toRunArgs(body, kbUser).withParadigm(id, ve.getVersion()));
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

    /**
     * Execute the draft without persisting results (preview while editing). Body: run args.
     *
     * <p>Attributed with the paradigm id but a null version — the draft is by definition not any
     * published version, and recording the current one would misattribute editor traffic to it.</p>
     */
    @PostMapping("/{id}/dryrun")
    public Map<String, Object> dryRun(
            @PathVariable String id,
            @RequestBody JsonNode body,
            @RequestHeader(value = "X-KB-User", required = false) String kbUser) {
        JsonNode draft = paradigmService.resolveDraftGraph(id);
        return executionService.run(
                draft, ParadigmRequests.toRunArgs(body, kbUser).withParadigm(id, null));
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
