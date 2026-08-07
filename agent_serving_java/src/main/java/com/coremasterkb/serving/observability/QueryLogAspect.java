package com.coremasterkb.serving.observability;

import com.coremasterkb.serving.domain.ContextPack;
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
 * Intercepts {@code SearchService.search()} to record query logs.
 * No business code is aware of this aspect.
 */
@Aspect
@Component
public class QueryLogAspect {

    private static final Logger log = LoggerFactory.getLogger(QueryLogAspect.class);

    private final QueryLogService queryLogService;

    public QueryLogAspect(QueryLogService queryLogService) {
        this.queryLogService = queryLogService;
    }

    @Around("execution(* com.coremasterkb.serving.application.SearchService.search(..))")
    public Object logSearch(ProceedingJoinPoint pjp) throws Throwable {
        long startMs = System.currentTimeMillis();
        String queryId = UUID.randomUUID().toString();
        SearchRequest request = (SearchRequest) pjp.getArgs()[0];

        log.info("[search] start id={} domain={} query=\"{}\"",
                queryId, request.domain(), abbreviate(request.query(), 60));

        ContextPack pack = null;
        Throwable thrown = null;
        try {
            Object result = pjp.proceed();
            if (result instanceof ContextPack cp) {
                pack = cp;
            }
            return result;
        } catch (Throwable t) {
            thrown = t;
            throw t;
        } finally {
            long durationMs = System.currentTimeMillis() - startMs;
            if (thrown != null) {
                log.warn("[search] error id={} domain={} duration={}ms error={}",
                        queryId, request.domain(), durationMs, thrown.getMessage());
            }
            queryLogService.record(queryId, request, pack, durationMs);
        }
    }

    /**
     * Same treatment for the operator-paradigm engine, which is a completely separate execution
     * path — without this, every query served by a bound paradigm (i.e. all MCP traffic once
     * auto-matching is on) would be absent from {@code serving_query_logs} entirely.
     *
     * <p>A second advice rather than a widened pointcut: the two methods share no signature. This
     * one takes {@code (JsonNode, RunArgs)} and returns a shaped {@code Map}, so the legacy
     * advice's {@code (SearchRequest) getArgs()[0]} cast would fail outright.</p>
     *
     * <p>Paradigms may terminate in {@code collect} instead of {@code assemble}, in which case
     * there is no ContextPack; those rows are still recorded, just with null result detail. That is
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

            queryLogService.record(queryId, request, extractPack(result), durationMs, metadata);
        } catch (Exception e) {
            // SearchRequest's compact constructor rejects a blank query, and a paradigm run can be
            // rejected for exactly that reason before anything else happens. Logging must not
            // convert that 400 into a 500.
            log.warn("Failed to record paradigm query log [{}]: {}", queryId, e.getMessage());
        }
    }

    private static ContextPack extractPack(Object result) {
        if (result instanceof Map<?, ?> m && m.get("contextPack") instanceof ContextPack pack) {
            return pack;
        }
        return null;
    }

    private static String outputKind(Object result) {
        if (!(result instanceof Map<?, ?> m)) return "none";
        if (m.containsKey("contextPack")) return "contextPack";
        if (m.containsKey("candidates")) return "candidates";
        return "none";
    }

    private static String abbreviate(String s, int maxLen) {
        if (s == null) return "";
        return s.length() <= maxLen ? s : s.substring(0, maxLen) + "...";
    }
}
