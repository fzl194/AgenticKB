package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.application.KbAccessService;
import com.coremasterkb.serving.domainpack.DomainContext;
import com.coremasterkb.serving.domainpack.DomainPoolManager;
import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmVersionMapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Which published paradigms an anonymous, domain-only caller (MCP) can actually use — and for the
 * ones it cannot, why not.
 *
 * <h2>⚠️ Connection orchestration is load-bearing</h2>
 *
 * <p>This service reads from two databases whose {@code DomainContext} requirements are
 * <em>opposite</em>:</p>
 * <ul>
 *   <li>{@code operator_paradigm} / {@code operator_paradigm_version} live in the <b>control DB</b>
 *       and are reached through the non-routed default DataSource — {@code DomainContext} must be
 *       <b>clear</b> (see {@code ParadigmMapper}'s Javadoc).</li>
 *   <li>{@code knowledge_bases} / {@code kb_users} / {@code kb_members} live in the <b>domain DB</b>
 *       — {@code DomainContext} must be <b>set to that domain</b> (see {@code KbAccessService}'s
 *       Javadoc).</li>
 * </ul>
 *
 * <p>Getting this wrong <b>does not raise an error</b>. Every domain currently points at the same
 * physical {@code kb_db}, so a control-DB read performed with a domain set still returns rows; it
 * would only break the day someone genuinely splits the databases, long after the change that
 * caused it. {@code ParadigmService.applyBinding} carries the same warning for the write path, and
 * {@code ParadigmBindingService} solves it the same way this class does: <b>segregate the two kinds
 * of read into separate phases and never interleave them.</b></p>
 *
 * <pre>
 *   Phase 1  no DomainContext   read every published paradigm + its graph      (control DB)
 *   Phase 2  per-domain context group by domain, then verify KB readability    (domain DBs)
 *   Phase 3  no DomainContext   assemble the response
 * </pre>
 *
 * <p>Phase 2 groups by domain rather than iterating paradigm-by-paradigm so one domain costs one
 * context switch and one pool validation, however many of its paradigms need checking.</p>
 *
 * <p>Callers must invoke this with no {@code DomainContext} set; it restores that state before
 * returning.</p>
 */
@Service
public class ParadigmCatalogService {

    private static final Logger log = LoggerFactory.getLogger(ParadigmCatalogService.class);

    // ---- hidden reasons (stable strings: the UI maps them to human text) ----

    /** Terminates in {@code collect} — bare candidates, evaluation-only. */
    static final String NOT_SERVABLE = "not_servable";
    /** Scopes knowledge bases an anonymous caller cannot read. */
    static final String KB_NOT_READABLE = "kb_not_anonymously_readable";

    private final ParadigmService paradigmService;
    private final ParadigmVersionMapper versionMapper;
    private final DomainPoolManager poolManager;
    private final KbAccessService kbAccessService;
    private final ObjectMapper json = new ObjectMapper();

    public ParadigmCatalogService(ParadigmService paradigmService,
                                  ParadigmVersionMapper versionMapper,
                                  DomainPoolManager poolManager,
                                  KbAccessService kbAccessService) {
        this.paradigmService = paradigmService;
        this.versionMapper = versionMapper;
        this.poolManager = poolManager;
        this.kbAccessService = kbAccessService;
    }

    // =====================================================================================
    // Public API
    // =====================================================================================

    /**
     * @param domainFilter keep only paradigms usable in this domain — its own, plus the
     *                     domain-agnostic ones ({@code domain: null}). Null returns everything.
     * @param username     {@code X-KB-User}; reserved for deciding how much of {@code hidden} may
     *                     be disclosed. Visibility itself is always evaluated <em>anonymously</em>,
     *                     because that is who MCP is.
     */
    public Catalog build(String domainFilter, String username) {
        // ---- Phase 1: control DB, DomainContext clear ----
        List<Candidate> candidates = loadCandidates(domainFilter);
        Verdict[] verdicts = new Verdict[candidates.size()];
        Map<String, List<Integer>> byDomain = new LinkedHashMap<>();

        for (int i = 0; i < candidates.size(); i++) {
            Candidate c = candidates.get(i);
            if (!ParadigmGraphs.isServable(c.graph())) {
                verdicts[i] = Verdict.hidden(NOT_SERVABLE);
            } else if (c.kbIds().isEmpty()) {
                // No KB scope → resolves against the domain's active release, which any domain can
                // do. Nothing to verify against a domain DB, so no round trip at all.
                verdicts[i] = Verdict.VISIBLE;
            } else {
                byDomain.computeIfAbsent(c.domain(), d -> new ArrayList<>()).add(i);
            }
        }

        // ---- Phase 2: domain DBs, DomainContext set per group ----
        for (Map.Entry<String, List<Integer>> group : byDomain.entrySet()) {
            verifyDomainGroup(group.getKey(), group.getValue(), candidates, verdicts);
        }

        // ---- Phase 3: assemble, DomainContext clear again ----
        return assemble(candidates, verdicts);
    }

    // =====================================================================================
    // Phase 1 — control DB
    // =====================================================================================

    private List<Candidate> loadCandidates(String domainFilter) {
        List<Candidate> out = new ArrayList<>();
        for (ParadigmEntity p : paradigmService.listPublished()) {
            String domain = blankToNull(p.getBoundDomain());
            JsonNode graph = graphOf(p);
            out.add(new Candidate(p, domain, graph,
                    graph != null ? ParadigmGraphs.kbIdsOf(graph) : List.of()));
        }
        return out;
    }

    /**
     * The published graph, or null when the version row is gone.
     *
     * <p>Read straight from the version mapper rather than through
     * {@code ParadigmService.resolveExecutableGraph}: that would re-fetch the entity we already
     * hold, and it throws on a missing version. A catalog listing must degrade one row instead of
     * failing the whole request.</p>
     */
    private JsonNode graphOf(ParadigmEntity p) {
        try {
            ParadigmVersionEntity v =
                    versionMapper.selectByParadigmAndVersion(p.getId(), p.getCurrentVersion());
            if (v == null || v.getGraphJson() == null) {
                log.warn("[paradigm/catalog] {} current_version={} has no version row",
                        p.getId(), p.getCurrentVersion());
                return null;
            }
            return json.readTree(v.getGraphJson());
        } catch (Exception e) {
            log.warn("[paradigm/catalog] unreadable graph for {}: {}", p.getId(), e.toString());
            return null;
        }
    }

    // =====================================================================================
    // Phase 2 — domain DBs
    // =====================================================================================

    private void verifyDomainGroup(String domain, List<Integer> indexes,
                                   List<Candidate> candidates, Verdict[] verdicts) {
        // Build and connectivity-check the pool BEFORE setting the context, exactly as
        // ParadigmBindingService does; otherwise an unreachable domain surfaces as an opaque
        // mapper failure instead of domain_database_unavailable.
        poolManager.getDataSource(domain);

        DomainContext.set(domain);
        try {
            for (int i : indexes) {
                verdicts[i] = verifyReadable(domain, candidates.get(i).kbIds());
            }
        } finally {
            DomainContext.clear();
        }
    }

    /**
     * Is every scoped KB readable anonymously?
     *
     * <p>The decision delegates to the very {@link KbAccessService#authorize} the retrieval path
     * uses, with a null username standing in for MCP, so the catalog cannot drift from what
     * execution will actually do.</p>
     */
    private Verdict verifyReadable(String domain, List<String> kbIds) {
        try {
            kbAccessService.authorize(domain, kbIds, null);
            return Verdict.VISIBLE;
        } catch (IllegalArgumentException notReadable) {
            return Verdict.hidden(KB_NOT_READABLE);
        }
    }

    // =====================================================================================
    // Phase 3 — assemble
    // =====================================================================================

    private Catalog assemble(List<Candidate> candidates, Verdict[] verdicts) {
        List<Entry> visible = new ArrayList<>();
        List<Hidden> hidden = new ArrayList<>();
        for (int i = 0; i < candidates.size(); i++) {
            Candidate c = candidates.get(i);
            Verdict v = verdicts[i];
            if (v != null && v.visible()) {
                visible.add(new Entry(c.entity().getId(), c.entity().getName(),
                        c.entity().getDescription(), c.domain(),
                        c.entity().getCurrentVersion(), c.entity().getIsDefault()));
            } else {
                hidden.add(new Hidden(c.entity().getId(), c.entity().getName(),
                        v != null ? v.reason() : NOT_SERVABLE, List.of(), 0));
            }
        }
        log.debug("[paradigm/catalog] visible={} hidden={}", visible.size(), hidden.size());
        return new Catalog(visible, hidden);
    }

    private static String blankToNull(String s) {
        return (s == null || s.isBlank()) ? null : s;
    }

    // =====================================================================================
    // Views
    // =====================================================================================

    /**
     * @param paradigms what an anonymous MCP caller can use
     * @param hidden    why the rest cannot be — for operators, never sent to agents
     */
    public record Catalog(List<Entry> paradigms, List<Hidden> hidden) {}

    /**
     * @param domain          the bound domain, or null when the paradigm works in any of them
     * @param isDomainDefault whether domain-only callers already resolve to it without naming it
     */
    public record Entry(String id, String name, String description,
                        String domain, int version, boolean isDomainDefault) {}

    /**
     * @param details          offending kb ids the requesting user is allowed to be told about
     * @param undisclosedCount how many more there are that they are not
     */
    public record Hidden(String id, String name, String reason,
                         List<String> details, int undisclosedCount) {}

    // ---- internals ----

    private record Candidate(ParadigmEntity entity, String domain, JsonNode graph, List<String> kbIds) {}

    private record Verdict(boolean visible, String reason) {
        static final Verdict VISIBLE = new Verdict(true, null);

        static Verdict hidden(String reason) {
            return new Verdict(false, reason);
        }
    }
}
