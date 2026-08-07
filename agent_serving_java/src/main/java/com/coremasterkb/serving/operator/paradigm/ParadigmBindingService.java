package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.application.KbAccessService;
import com.coremasterkb.serving.domainpack.DomainContext;
import com.coremasterkb.serving.domainpack.DomainPoolManager;
import com.coremasterkb.serving.domainpack.DomainRegistry;
import com.coremasterkb.serving.mapper.KnowledgeBaseMapper;
import com.fasterxml.jackson.databind.JsonNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Binds a published paradigm to a domain so MCP (and any other domain-only caller) can resolve it
 * automatically. See {@code docs/mcp-paradigm-routing-design.md}.
 *
 * <p><b>Ordering is load-bearing.</b> Validation and persistence touch two different databases:
 * the knowledge-base check reads the <em>domain</em> DB (needs {@code DomainContext} set) while
 * {@code operator_paradigm} lives in the <em>control</em> DB (needs it clear). The transaction
 * manager sits on the routing DataSource and pins a connection for the whole transaction, so these
 * cannot be interleaved. This service therefore runs every domain-scoped read first, clears the
 * context, and only then calls the transactional
 * {@link ParadigmService#applyBinding(String, String, boolean)}.</p>
 */
@Service
public class ParadigmBindingService {

    private static final Logger log = LoggerFactory.getLogger(ParadigmBindingService.class);

    private final ParadigmService paradigmService;
    private final DomainRegistry domainRegistry;
    private final DomainPoolManager domainPoolManager;
    private final KbAccessService kbAccessService;
    private final KnowledgeBaseMapper knowledgeBaseMapper;

    public ParadigmBindingService(ParadigmService paradigmService,
                                  DomainRegistry domainRegistry,
                                  DomainPoolManager domainPoolManager,
                                  KbAccessService kbAccessService,
                                  KnowledgeBaseMapper knowledgeBaseMapper) {
        this.paradigmService = paradigmService;
        this.domainRegistry = domainRegistry;
        this.domainPoolManager = domainPoolManager;
        this.kbAccessService = kbAccessService;
        this.knowledgeBaseMapper = knowledgeBaseMapper;
    }

    /**
     * Bind a paradigm to a domain, optionally claiming the domain's auto-match slot.
     *
     * @throws ParadigmBindingException with code {@code unknown_domain},
     *         {@code paradigm_not_published}, {@code paradigm_not_servable} or
     *         {@code paradigm_requires_identity}
     */
    public ParadigmEntity bind(String id, String domain, boolean isDefault) {
        if (domain == null || domain.isBlank()) {
            throw new ParadigmBindingException("domain_required", "binding requires a domain");
        }
        String target = domain.trim();

        ParadigmEntity p = paradigmService.getOrThrow(id);
        validateDomainKnown(target);
        validatePublished(p);

        // Validate the PUBLISHED graph, not the draft: that is what will actually execute for MCP.
        JsonNode graph = paradigmService.resolveExecutableGraph(id, null);
        validateServable(graph);
        validateAnonymouslyReadable(target, graph);

        // Every domain-scoped read is done; DomainContext is clear. Safe to open the control-DB txn.
        return paradigmService.applyBinding(id, target, isDefault);
    }

    /** Remove a paradigm's binding. Always allowed — an unbound paradigm is still callable by id. */
    public ParadigmEntity unbind(String id) {
        paradigmService.getOrThrow(id);
        return paradigmService.applyBinding(id, null, false);
    }

    // ---- validations ---------------------------------------------------------------------

    /** Without this, a typo'd domain binds "successfully" and MCP silently never matches it. */
    private void validateDomainKnown(String domain) {
        try {
            domainRegistry.resolve(domain);
        } catch (IllegalArgumentException e) {
            throw new ParadigmBindingException(
                    "unknown_domain", "no such domain (or it is disabled): " + domain);
        }
    }

    private void validatePublished(ParadigmEntity p) {
        if (p.getCurrentVersion() < 1) {
            throw new ParadigmBindingException("paradigm_not_published",
                    "paradigm has no published version — publish it before binding");
        }
        if (!"active".equals(p.getStatus())) {
            throw new ParadigmBindingException("paradigm_not_published",
                    "paradigm status is '" + p.getStatus() + "', expected 'active'");
        }
    }

    /**
     * A bound paradigm must terminate in {@code assemble} (output slot {@code contextPack}).
     *
     * <p>{@code collect} exists for the evaluation harness: it returns bare candidates that never
     * went through {@code ContextAssembler}'s source drill-down, graph expansion, evidence grouping
     * and compression. Serving those to an agent is both lower quality and a different response
     * shape. Rejecting at bind time keeps that from becoming a runtime surprise.</p>
     *
     * <p>The predicate itself lives in {@link ParadigmGraphs} so that binding and any other consumer
     * of "is this servable" cannot drift apart.</p>
     */
    private void validateServable(JsonNode graph) {
        if (!ParadigmGraphs.isServable(graph)) {
            String slot = ParadigmGraphs.outputSlotOf(graph);
            throw new ParadigmBindingException("paradigm_not_servable",
                    "a domain-bound paradigm must end in 'assemble' (output slot 'contextPack'), "
                            + "found output slot: " + (slot != null ? slot : "<none>"));
        }
    }

    /**
     * MCP sends no {@code X-KB-User}, so a bound paradigm whose {@code scope_resolve} names a
     * private/shared knowledge base would fail <em>every</em> MCP call for that domain with
     * {@code kb_not_found} — and that error deliberately does not say which KB, making it painful
     * to diagnose. Check it once, here, where naming the offenders is safe.
     *
     * <p>The pass/fail decision delegates to the very same {@link KbAccessService#authorize} the
     * retrieval path uses, with a null username standing in for the anonymous caller, so this check
     * cannot drift from runtime behaviour. The mapper is queried again only on the failure path,
     * purely to name the offending knowledge bases.</p>
     */
    private void validateAnonymouslyReadable(String domain, JsonNode graph) {
        List<String> kbIds = ParadigmGraphs.kbIdsOf(graph);
        if (kbIds.isEmpty()) {
            return;
        }

        // Build/verify the domain pool before setting the context, exactly as SearchService does;
        // otherwise an unreachable domain DB surfaces as an opaque mapper failure.
        domainPoolManager.getDataSource(domain);

        DomainContext.set(domain);
        try {
            kbAccessService.authorize(domain, kbIds, null);
        } catch (IllegalArgumentException e) {
            throw new ParadigmBindingException("paradigm_requires_identity",
                    "scope_resolve references knowledge bases that an anonymous caller (MCP sends "
                            + "no X-KB-User) cannot read; make them public or drop them from the graph",
                    findNonPublic(domain, kbIds));
        } finally {
            DomainContext.clear();
        }
    }

    /** Failure path only: diff requested against anonymously-accessible to name the offenders. */
    private List<String> findNonPublic(String domain, List<String> kbIds) {
        try {
            List<String> accessible = knowledgeBaseMapper.selectAccessibleKbIds(domain, kbIds, null);
            Set<String> allowed = new LinkedHashSet<>(accessible != null ? accessible : List.of());
            List<String> denied = new ArrayList<>();
            for (String id : kbIds) {
                if (!allowed.contains(id)) {
                    denied.add(id);
                }
            }
            return denied;
        } catch (RuntimeException e) {
            log.warn("Could not enumerate non-public KBs for domain={}: {}", domain, e.getMessage());
            return List.of();
        }
    }
}
