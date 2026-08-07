package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.operator.core.exceptions.OperatorException;
import com.coremasterkb.serving.operator.engine.ParadigmCompiler;
import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmMapper;
import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmVersionMapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.UUID;

/**
 * Paradigm lifecycle: create draft → edit draft → publish (immutable version) → rollback.
 *
 * <p>Publishing compiles the draft first (reusing {@link ParadigmCompiler}); only a valid graph
 * becomes a version. Versions are immutable, so a {@code paradigmId + version} call always
 * reproduces the same result. Stored in the control DB via non-routed mapper calls (no
 * {@code DomainContext} set on these threads).</p>
 */
@Service
public class ParadigmService {

    private static final Logger log = LoggerFactory.getLogger(ParadigmService.class);

    private final ParadigmMapper paradigmMapper;
    private final ParadigmVersionMapper versionMapper;
    private final ParadigmCompiler compiler;
    private final ObjectMapper mapper = new ObjectMapper();

    public ParadigmService(ParadigmMapper paradigmMapper,
                           ParadigmVersionMapper versionMapper,
                           ParadigmCompiler compiler) {
        this.paradigmMapper = paradigmMapper;
        this.versionMapper = versionMapper;
        this.compiler = compiler;
    }

    // ---- CRUD ----------------------------------------------------------------------------

    /** Create a new draft paradigm. {@code graphJson} (the draft DAG) may be null/blank. */
    public ParadigmEntity create(String name, String description, String graphJson) {
        if (name == null || name.isBlank()) {
            throw new ParadigmBadRequestException("name_required");
        }
        if (paradigmMapper.selectByName(name) != null) {
            throw new ParadigmBadRequestException("paradigm_name_exists");
        }
        ParadigmEntity e = new ParadigmEntity();
        e.setId("pd-" + UUID.randomUUID().toString().substring(0, 8));
        e.setName(name);
        e.setDescription(description);
        e.setDraftGraphJson(blankToNull(graphJson));
        e.setCurrentVersion(0);
        e.setStatus("draft");
        paradigmMapper.insert(e);
        return getOrThrow(e.getId());
    }

    public ParadigmEntity getOrThrow(String id) {
        ParadigmEntity e = paradigmMapper.selectById(id);
        if (e == null) {
            throw new ParadigmNotFoundException("paradigm not found: " + id);
        }
        return e;
    }

    public List<ParadigmEntity> listAll() {
        return paradigmMapper.selectAll();
    }

    /** Published (currently active) paradigms only — those callable via {@code /{id}/search}. */
    public List<ParadigmEntity> listPublished() {
        return paradigmMapper.selectPublished();
    }

    /**
     * The paradigm a domain-only caller (MCP) should use, or null when the domain has none.
     *
     * <p>"No binding" is a normal state, not an error — callers fall back to {@code /api/v1/search}.
     * Returning null rather than throwing is what lets that stay distinguishable from a genuine
     * failure at the HTTP layer.</p>
     */
    public ParadigmEntity resolveDefaultForDomain(String domain) {
        if (domain == null || domain.isBlank()) {
            return null;
        }
        return paradigmMapper.selectDefaultByDomain(domain.trim());
    }

    /** Replace the editable draft graph. */
    public ParadigmEntity updateDraft(String id, String graphJson) {
        getOrThrow(id);
        paradigmMapper.updateDraft(id, blankToNull(graphJson));
        return getOrThrow(id);
    }

    // ---- versions / publish --------------------------------------------------------------

    public List<ParadigmVersionEntity> listVersions(String id) {
        getOrThrow(id);
        return versionMapper.selectVersionsByParadigm(id);
    }

    public ParadigmVersionEntity getVersionOrThrow(String id, int version) {
        getOrThrow(id);
        ParadigmVersionEntity v = versionMapper.selectByParadigmAndVersion(id, version);
        if (v == null) {
            throw new ParadigmNotFoundException("paradigm " + id + " has no version " + version);
        }
        return v;
    }

    /** Compile-validate the current draft, then snapshot it as a new immutable version and activate it. */
    @Transactional
    public ParadigmVersionEntity publish(String id, String createdBy) {
        ParadigmEntity p = getOrThrow(id);
        String draft = p.getDraftGraphJson();
        if (draft == null || draft.isBlank()) {
            throw new OperatorException("nothing to publish: draft graph is empty");
        }
        JsonNode graph = parse(draft);
        compiler.compile(graph); // throws ParadigmCompileException on invalid graph

        // Next version = max existing + 1 (NOT current_version + 1): after a rollback to a
        // non-latest version, current_version < max, so current+1 would collide with an
        // existing version and violate uq_operator_paradigm_version.
        Integer maxVersion = versionMapper.selectMaxVersion(id);
        int newVersion = (maxVersion == null ? 0 : maxVersion) + 1;
        ParadigmVersionEntity v = new ParadigmVersionEntity();
        v.setId("pdv-" + UUID.randomUUID().toString().substring(0, 8));
        v.setParadigmId(id);
        v.setVersion(newVersion);
        v.setGraphJson(draft);
        v.setSchemaVersion(extractSchemaVersion(graph));
        v.setCreatedBy(createdBy);
        versionMapper.insert(v);

        paradigmMapper.updatePublish(id, newVersion, "active");
        log.info("[paradigm] published {} version {}", id, newVersion);
        return v;
    }

    /** Point {@code current_version} back at an existing historical version (versions are unchanged). */
    public ParadigmEntity rollback(String id, int version) {
        getVersionOrThrow(id, version);
        paradigmMapper.updatePublish(id, version, "active");
        log.info("[paradigm] rolled back {} to version {}", id, version);
        return getOrThrow(id);
    }

    /**
     * Write a paradigm's domain binding. Clears the domain's previous default first when claiming
     * the default slot — the reverse order violates {@code uq_paradigm_domain_default} (23505).
     *
     * <p><b>Callers must have cleared {@code DomainContext} before invoking this.</b> The
     * transaction manager sits on the {@code @Primary} routing DataSource, so it binds a connection
     * based on whatever domain the thread carried when the transaction opened — with a domain set,
     * these control-DB writes would land in that domain's database instead. In production every
     * domain currently points at the same physical {@code kb_db}, so the mistake would not raise an
     * error, it would silently write to the wrong logical store. All validation that needs a domain
     * connection therefore happens in {@link ParadigmBindingService} <em>before</em> this call.</p>
     */
    @Transactional
    public ParadigmEntity applyBinding(String id, String domain, boolean isDefault) {
        getOrThrow(id);
        if (isDefault && domain != null) {
            paradigmMapper.clearDefaultForDomain(domain);
        }
        paradigmMapper.updateBinding(id, domain, isDefault);
        log.info("[paradigm] bound {} → domain={} default={}", id, domain, isDefault);
        return getOrThrow(id);
    }

    /** Archive a paradigm (status → archived); its versions and current_version are kept intact. */
    public ParadigmEntity archive(String id) {
        ParadigmEntity p = getOrThrow(id);
        paradigmMapper.updatePublish(id, p.getCurrentVersion(), "archived");
        log.info("[paradigm] archived {}", id);
        return getOrThrow(id);
    }

    /**
     * Permanently delete a paradigm and all its versions. Only allowed once the paradigm is
     * archived — a two-step guard (archive → delete) against accidental loss of a live paradigm.
     */
    @Transactional
    public void delete(String id) {
        ParadigmEntity p = getOrThrow(id);
        if (!"archived".equals(p.getStatus())) {
            throw new ParadigmBadRequestException("paradigm_not_archived");
        }
        versionMapper.deleteByParadigm(id);
        paradigmMapper.deleteById(id);
        log.info("[paradigm] deleted {}", id);
    }

    // ---- resolution for execution --------------------------------------------------------

    /**
     * Resolve the graph JSON to execute for {@code (id, version?)}: a specific version if given,
     * otherwise the paradigm's {@code current_version}. Throws if unpublished or version missing.
     */
    public JsonNode resolveExecutableGraph(String id, Integer version) {
        return parse(resolveExecutableVersion(id, version).getGraphJson());
    }

    /**
     * As {@link #resolveExecutableGraph} but returns the whole version row, so a caller that also
     * needs the <em>effective</em> version number (e.g. to attribute a query log when the request
     * said "current") gets it from this same read instead of querying again.
     */
    public ParadigmVersionEntity resolveExecutableVersion(String id, Integer version) {
        ParadigmEntity p = getOrThrow(id);
        int v = (version != null) ? version : p.getCurrentVersion();
        if (v <= 0) {
            throw new ParadigmNotFoundException("paradigm " + id + " has no published version");
        }
        ParadigmVersionEntity ve = versionMapper.selectByParadigmAndVersion(id, v);
        if (ve == null) {
            throw new ParadigmNotFoundException("paradigm " + id + " has no version " + v);
        }
        return ve;
    }

    /** Compile-validate a graph (no execution), returning the structured error list. */
    public List<com.coremasterkb.serving.operator.engine.CompileError> validateDraft(JsonNode graph) {
        return compiler.validate(graph);
    }

    /** Parse the editable draft graph (for validate / dry-run). */
    public JsonNode resolveDraftGraph(String id) {
        ParadigmEntity p = getOrThrow(id);
        if (p.getDraftGraphJson() == null || p.getDraftGraphJson().isBlank()) {
            throw new OperatorException("paradigm " + id + " has no draft graph");
        }
        return parse(p.getDraftGraphJson());
    }

    // ---- helpers -------------------------------------------------------------------------

    private JsonNode parse(String json) {
        try {
            return mapper.readTree(json);
        } catch (Exception e) {
            throw new OperatorException("stored graph_json is not valid JSON: " + e.getMessage());
        }
    }

    private static String extractSchemaVersion(JsonNode graph) {
        JsonNode sv = graph.get("schemaVersion");
        return (sv != null && sv.isTextual() && !sv.asText().isBlank()) ? sv.asText() : "1.0";
    }

    private static String blankToNull(String s) {
        return (s == null || s.isBlank()) ? null : s;
    }
}
