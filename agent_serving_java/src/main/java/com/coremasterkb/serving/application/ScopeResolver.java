package com.coremasterkb.serving.application;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.domainpack.DomainRegistry;
import com.coremasterkb.serving.operator.paradigm.ParadigmGraphs;
import com.coremasterkb.serving.operator.paradigm.ParadigmService;
import com.coremasterkb.serving.repository.AssetRepository;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * Works out which corpus a drill-down request may read, and authorizes it.
 *
 * <p>Shared by every endpoint that accepts ids the caller already holds — the full-text lookup and
 * the raw-file stream. It is one class rather than a copied method because the <em>order</em> of
 * the steps is the security property: read the requested KB ids, authorize them against the
 * caller, resolve the scope, and refuse an empty one. A second copy that drifted by one step would
 * not fail any compiler check.</p>
 */
@Service
public class ScopeResolver {

    private final AssetRepository assetRepository;
    private final KbAccessService kbAccessService;
    private final ParadigmService paradigmService;
    private final DomainRegistry domainRegistry;

    public ScopeResolver(AssetRepository assetRepository,
                         KbAccessService kbAccessService,
                         ParadigmService paradigmService,
                         DomainRegistry domainRegistry) {
        this.assetRepository = assetRepository;
        this.kbAccessService = kbAccessService;
        this.paradigmService = paradigmService;
        this.domainRegistry = domainRegistry;
    }

    /**
     * Resolve the snapshot scope this caller may read.
     *
     * <p>Three sources, and they are not interchangeable: a {@code paradigmId} means "the same
     * knowledge bases that paradigm searched", read out of the stored graph so the caller never
     * gets to widen it; explicit {@code kbIds} means the caller names them and gets authorized;
     * neither means the ordinary domain-wide active release. Supplying both is rejected rather
     * than silently resolved — picking one would turn a caller's mistake into an access decision
     * nobody reviewed.</p>
     *
     * <p>Runs on the caller's thread, so {@code DomainContext} must already be set by the caller;
     * otherwise {@code DomainRoutingDataSource} silently falls back to the default database and a
     * domain with its own {@code database:} block quietly reads the wrong one.</p>
     *
     * @param username caller identity from {@code X-KB-User}; null = anonymous (public KBs only)
     * @throws IllegalArgumentException("conflicting_scope_source") if both sources are supplied
     * @throws IllegalArgumentException("kb_not_found") if any requested KB is unreadable
     * @throws IllegalArgumentException("empty_scope") if the scope contains no snapshots
     */
    public ActiveScope resolve(String domain, String channel, String paradigmId,
                               Integer paradigmVersion, List<String> kbIds, String username) {
        boolean hasParadigm = paradigmId != null && !paradigmId.isBlank();
        boolean hasKbIds = kbIds != null && !kbIds.isEmpty();
        if (hasParadigm && hasKbIds) {
            throw new IllegalArgumentException("conflicting_scope_source");
        }

        List<String> requested = hasParadigm
                ? kbIdsOfParadigm(paradigmId, paradigmVersion)
                : kbIds;

        // Authorized even when the ids came from a stored graph: a saved paradigm must not become
        // a way to read a knowledge base the caller cannot open.
        List<String> authorized = kbAccessService.authorize(domain, requested, username);

        String effectiveChannel = (channel != null && !channel.isBlank())
                ? channel
                : domainRegistry.getDefaultChannel(domain);

        ActiveScope scope = assetRepository.resolveActiveScope(domain, effectiveChannel, authorized);
        if (scope.snapshotIds().isEmpty()) {
            throw new IllegalArgumentException("empty_scope");
        }
        return scope;
    }

    /**
     * Read the {@code kbIds} param off the paradigm's {@code scope_resolve} node(s).
     *
     * <p>The walk itself lives in {@link ParadigmGraphs} — bind-time validation and the drill-down
     * path must extract the same set, or a paradigm could be bound against one scope and read
     * against another.</p>
     */
    private List<String> kbIdsOfParadigm(String paradigmId, Integer version) {
        return ParadigmGraphs.kbIdsOf(paradigmService.resolveExecutableGraph(paradigmId, version));
    }
}
