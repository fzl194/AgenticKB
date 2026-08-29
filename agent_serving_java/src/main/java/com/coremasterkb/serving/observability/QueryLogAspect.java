package com.coremasterkb.serving.observability;

import com.coremasterkb.serving.domain.EvidenceResponse;
import com.coremasterkb.serving.domain.SearchRequest;
import com.coremasterkb.serving.operator.api.ParadigmExecutionService.RunArgs;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Intercepts {@code ParadigmExecutionService.run()} to record query logs for every
 * operator-paradigm execution. No business code is aware of this aspect.
 *
 * <p>批次8 R0：旧固定链 {@code SearchService.search()} 的 advice 随固定链删除；本切面只保留
 * 范式执行路径的日志（25号 §11.1）。批次8 R6：终点协议从 ContextPack 换成
 * {@link EvidenceResponse}（§5.3），本切面随之改取 {@code evidenceResponse}。</p>
 */
@Aspect
@Component
public class QueryLogAspect {

    private static final Logger log = LoggerFactory.getLogger(QueryLogAspect.class);

    private final QueryLogService queryLogService;

    public QueryLogAspect(QueryLogService queryLogService) {
        this.queryLogService = queryLogService;
    }

    /**
     * The operator-paradigm engine is the only search execution path — without this advice every
     * served query would be absent from {@code serving_query_logs} entirely.
     *
     * <p>Paradigms may terminate in a non-{@code assemble} node, in which case there is no
     * EvidenceResponse; those rows are still recorded, just with null result detail. That is
     * intentional — a missing row and a candidate-only row mean very different things.</p>
     */
    @Around("execution(* com.coremasterkb.serving.operator.api.ParadigmExecutionService.run(..))")
    public Object logParadigmSearch(ProceedingJoinPoint pjp) throws Throwable {
        Object[] args = pjp.getArgs();
        if (args.length < 2 || !(args[1] instanceof RunArgs runArgs)) {
            return pjp.proceed();   // signature changed under us — never break the request over logging
        }

        long startMs = System.currentTimeMillis();
        String queryId = UUID.randomUUID().toString();

        log.info("[paradigm-search] start id={} domain={} paradigm={} query=\"{}\"",
                queryId, runArgs.domain(), runArgs.paradigmId(), abbreviate(runArgs.query(), 60));

        Throwable thrown = null;
        Object result = null;
        try {
            result = pjp.proceed();
            return result;
        } catch (Throwable t) {
            thrown = t;
            throw t;
        } finally {
            long durationMs = System.currentTimeMillis() - startMs;
            if (thrown != null) {
                log.warn("[paradigm-search] error id={} domain={} duration={}ms error={}",
                        queryId, runArgs.domain(), durationMs, thrown.getMessage());
            }
            recordParadigm(queryId, runArgs, result, durationMs);
        }
    }

    private void recordParadigm(String queryId, RunArgs runArgs, Object result, long durationMs) {
        try {
            SearchRequest request = new SearchRequest(
                    runArgs.query(), Map.of(), List.of(), runArgs.debug(),
                    runArgs.domain(), runArgs.channel(), "evidence", List.of());

            Map<String, Object> metadata = new LinkedHashMap<>();
            metadata.put("engine", "paradigm");
            if (runArgs.paradigmId() != null) metadata.put("paradigm_id", runArgs.paradigmId());
            if (runArgs.paradigmVersion() != null) metadata.put("paradigm_version", runArgs.paradigmVersion());
            metadata.put("output", outputKind(result));
            EvidenceResponse response = extractEvidence(result);
            if (response != null) {
                metadata.put("has_more", response.hasMore());
            }

            queryLogService.record(queryId, request, response, durationMs, metadata);
        } catch (Exception e) {
            // SearchRequest's compact constructor rejects a blank query, and a paradigm run can be
            // rejected for exactly that reason before anything else happens. Logging must not
            // convert that 400 into a 500.
            log.warn("Failed to record paradigm query log [{}]: {}", queryId, e.getMessage());
        }
    }

    private static EvidenceResponse extractEvidence(Object result) {
        if (result instanceof Map<?, ?> m && m.get("evidenceResponse") instanceof EvidenceResponse er) {
            return er;
        }
        return null;
    }

    private static String outputKind(Object result) {
        if (!(result instanceof Map<?, ?> m)) return "none";
        if (m.containsKey("evidenceResponse")) return "evidenceResponse";
        if (m.containsKey("candidates")) return "candidates";
        return "none";
    }

    private static String abbreviate(String s, int maxLen) {
        if (s == null) return "";
        return s.length() <= maxLen ? s : s.substring(0, maxLen) + "...";
    }
}
