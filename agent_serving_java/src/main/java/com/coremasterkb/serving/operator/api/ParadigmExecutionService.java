package com.coremasterkb.serving.operator.api;

import com.coremasterkb.serving.config.ServingProperties;
import com.coremasterkb.serving.domain.EvidenceResponse;
import com.coremasterkb.serving.domain.RetrievalCandidate;
import com.coremasterkb.serving.domainpack.DomainPoolManager;
import com.coremasterkb.serving.domainpack.DomainRegistry;
import com.coremasterkb.serving.operator.core.ExecContext;
import com.coremasterkb.serving.operator.engine.ParadigmCompiler;
import com.coremasterkb.serving.operator.engine.ParadigmExecutor;
import com.coremasterkb.serving.operator.engine.ParadigmGraph;
import com.fasterxml.jackson.databind.JsonNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Shared compile + execute + response-shaping for both inline ({@code /paradigm/run}) and stored
 * ({@code /paradigm/{id}/search}, {@code /dryrun}) paradigm execution. Centralises domain/channel
 * resolution, DB-reachability validation, and result shaping so the two surfaces stay consistent.
 */
@Service
public class ParadigmExecutionService {

    private static final Logger log = LoggerFactory.getLogger(ParadigmExecutionService.class);

    /**
     * Per-call execution arguments. Everything but {@code username} and the paradigm reference
     * comes from the request body; {@code username} is the {@code X-KB-User} header, needed so a
     * paradigm whose {@code scope_resolve} names knowledge bases is authorized against the actual
     * caller.
     *
     * <p>{@code paradigmId}/{@code paradigmVersion} identify a <em>stored</em> paradigm and exist
     * purely so {@code QueryLogAspect} can attribute the query log to it. Both are null for inline
     * ({@code /paradigm/run}) execution, and the version is null for a draft dry-run. They are
     * never read by execution itself.</p>
     */
    public record RunArgs(String query, String domain, String channel, boolean debug,
                          String username, List<String> kbIds,
                          Map<String, Object> filters, Integer topK, String expansion,
                          String paradigmId, Integer paradigmVersion) {

        public RunArgs(String query, String domain, String channel, boolean debug) {
            this(query, domain, channel, debug, null);
        }

        public RunArgs(String query, String domain, String channel, boolean debug, String username) {
            this(query, domain, channel, debug, username, null, null, null, null, null, null);
        }

        /** 阶段 A：{@code kbIds} = 请求级库范围（可空；只对图内 scope 留空的范式生效）。 */
        public RunArgs withKbIds(List<String> kbIds) {
            return new RunArgs(query, domain, channel, debug, username, kbIds, filters, topK,
                    expansion, paradigmId, paradigmVersion);
        }

        /** Attach the stored-paradigm reference for query-log attribution. */
        public RunArgs withParadigm(String id, Integer version) {
            return new RunArgs(query, domain, channel, debug, username, kbIds, filters, topK,
                    expansion, id, version);
        }
    }

    private final ParadigmCompiler compiler;
    private final ParadigmExecutor executor;
    private final DomainRegistry domainRegistry;
    private final DomainPoolManager domainPoolManager;
    private final String defaultDomain;

    public ParadigmExecutionService(
            ParadigmCompiler compiler,
            ParadigmExecutor executor,
            DomainRegistry domainRegistry,
            DomainPoolManager domainPoolManager,
            ServingProperties properties) {
        this.compiler = compiler;
        this.executor = executor;
        this.domainRegistry = domainRegistry;
        this.domainPoolManager = domainPoolManager;
        this.defaultDomain = properties.defaultDomain();
    }

    /**
     * Compile and execute a paradigm graph, returning a shaped response map.
     * Throws {@code ParadigmCompileException} (compile error) or {@code OperatorException} (runtime),
     * mapped to HTTP by {@code OperatorExceptionHandler}.
     */
    public Map<String, Object> run(JsonNode paradigmJson, RunArgs args) {
        if (args.query() == null || args.query().isBlank()) {
            throw new IllegalArgumentException("query_required");
        }
        String domain = (args.domain() != null && !args.domain().isBlank())
                ? args.domain() : defaultDomain;
        String channel = (args.channel() != null && !args.channel().isBlank())
                ? args.channel() : domainRegistry.getDefaultChannel(domain);

        ParadigmGraph graph = compiler.compile(paradigmJson);

        // Validate DB reachable (lazy pool build + connectivity check) before executing.
        domainPoolManager.getDataSource(domain);

        ExecContext ctx = new ExecContext(
                UUID.randomUUID().toString(), domain, channel, args.debug(), args.username());
        ctx.setQuery(args.query());
        ctx.setRequestKbIds(args.kbIds());
        // R8（§7.1）：显式 within/filters = hard filters（scope_resolve 透传进 ActiveScope）；
        // 显式 top_k/expansion 覆盖节点参数（各算子按 schema 上限 clamp）。
        ctx.setRequestFilters(args.filters());
        ctx.setRequestTopK(args.topK());
        ctx.setRequestExpansion(args.expansion());
        Object result = executor.execute(graph, ctx, Map.of("query", args.query()));

        log.info("[paradigm/exec] domain={} channel={} output={}",
                domain, channel,
                result instanceof EvidenceResponse er ? (er.evidence().size() + " evidence")
                        : result instanceof List<?> l ? (l.size() + " candidates") : "other");
        return shape(result, ctx, args.debug(), domain, channel);
    }

    private static Map<String, Object> shape(
            Object result, ExecContext ctx, boolean debug, String domain, String channel) {
        Map<String, Object> resp = new LinkedHashMap<>();
        if (result instanceof EvidenceResponse evidenceResponse) {
            resp.put("evidenceResponse", evidenceResponse);
        } else if (result instanceof List<?> list) {
            resp.put("candidates", toCandidateDtos(list));
        } else {
            resp.put("result", result);
        }
        if (debug) {
            resp.put("trace", ctx.nodeTraces());
            resp.put("domain", domain);
            resp.put("channel", channel);
        }
        return resp;
    }

    private static List<Map<String, Object>> toCandidateDtos(List<?> list) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Object o : list) {
            if (o instanceof RetrievalCandidate c) {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("id", c.retrievalUnitId());
                m.put("score", c.score());
                m.put("source", c.source());
                m.put("scoreChain", c.scoreChain());
                m.put("metadata", c.metadata());
                out.add(m);
            }
        }
        return out;
    }
}
