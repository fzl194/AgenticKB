package com.coremasterkb.serving.application;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.mapper.KnowledgeBaseMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Authorizes a KB-narrowed retrieval request.
 *
 * <p>Serving has no session of its own, so identity arrives the same way it does at mining:
 * the {@code X-KB-User} header. That is an intranet trust header, not authentication — but
 * without <em>some</em> check, any caller could name a private KB's id and read it, since
 * narrowing by {@code kbIds} otherwise trusts the request completely.</p>
 *
 * <p>Runs on the caller's thread so it uses the domain-routed DataSource that
 * {@code DomainContext} has already selected.</p>
 */
@Service
public class KbAccessService {

    private static final Logger log = LoggerFactory.getLogger(KbAccessService.class);

    private final KnowledgeBaseMapper knowledgeBaseMapper;

    public KbAccessService(KnowledgeBaseMapper knowledgeBaseMapper) {
        this.knowledgeBaseMapper = knowledgeBaseMapper;
    }

    /**
     * Normalize and authorize the requested KB ids.
     *
     * <p>An empty request is the ordinary domain-wide search — no KB is involved, so nothing is
     * checked and an empty list comes back.</p>
     *
     * <p>Any requested id the caller cannot read fails the <em>whole</em> request rather than
     * being dropped: silently returning a subset would look identical to "that KB has no
     * matching content". Nonexistent and forbidden ids raise the same error, so the response
     * never reveals which knowledge bases exist.</p>
     *
     * @param username caller identity from {@code X-KB-User}; null means anonymous (public only)
     * @return the normalized, authorized kb ids (empty when none were requested)
     * @throws IllegalArgumentException("kb_not_found") if any requested id is not readable
     */
    public List<String> authorize(String domain, List<String> requestedKbIds, String username) {
        List<String> normalized = ActiveScope.normalizeKbIds(requestedKbIds);
        if (normalized.isEmpty()) {
            return List.of();
        }

        String effectiveDomain = (domain != null) ? domain : "default";
        List<String> accessible =
                knowledgeBaseMapper.selectAccessibleKbIds(effectiveDomain, normalized, username);
        Set<String> allowed = new HashSet<>(accessible != null ? accessible : List.of());

        if (!allowed.containsAll(normalized)) {
            Set<String> denied = new HashSet<>(normalized);
            denied.removeAll(allowed);
            // Logged, not returned — the caller must not learn which ids exist.
            log.warn("KB access denied: domain={}, user={}, denied={}",
                    effectiveDomain, username != null ? username : "<anonymous>", denied);
            throw new IllegalArgumentException("kb_not_found");
        }

        return normalized;
    }
}
