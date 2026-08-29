package com.coremasterkb.serving.operator.engine;

import com.coremasterkb.serving.domainpack.DomainContext;
import com.coremasterkb.serving.infrastructure.LlmClient;
import com.coremasterkb.serving.operator.core.ErrorPolicy;
import com.coremasterkb.serving.operator.core.ExecContext;
import com.coremasterkb.serving.operator.core.Operator;
import com.coremasterkb.serving.operator.core.OperatorDef;
import com.coremasterkb.serving.operator.core.Params;
import com.coremasterkb.serving.operator.core.SlotDecl;
import com.coremasterkb.serving.operator.core.SlotType;
import com.coremasterkb.serving.operator.core.SlotValues;
import com.coremasterkb.serving.operator.core.exceptions.OperatorException;
import com.coremasterkb.serving.operator.registry.OperatorRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executor;

/**
 * Executes a compiled {@link ParadigmGraph}: topological wave scheduling with Java 21 virtual
 * threads. Nodes whose predecessors are all done run in parallel within a wave.
 *
 * <p>Each node runs with the request's {@link DomainContext} set on its (virtual) thread so the
 * routed MyBatis mappers hit the right domain DB, and with the LLM client's knowledge-domain set
 * for billing/audit — mirroring the existing {@code SearchService} concurrency model.</p>
 *
 * <p>Error isolation follows each operator's {@link ErrorPolicy}: {@code SKIP_WITH_EMPTY} yields
 * empty outputs and lets downstream continue; {@code FAIL_FAST}/{@code FALLBACK} abort the run.</p>
 */
@Component
public class ParadigmExecutor {

    private static final Logger log = LoggerFactory.getLogger(ParadigmExecutor.class);

    private final OperatorRegistry registry;
    private final LlmClient llmClient;
    private final Executor executor;

    public ParadigmExecutor(OperatorRegistry registry, LlmClient llmClient, Executor pipelineExecutor) {
        this.registry = registry;
        this.llmClient = llmClient;
        this.executor = pipelineExecutor;
    }

    /**
     * Execute the graph and return the terminal node's output-slot value.
     *
     * @param graph       compiled graph
     * @param ctx         per-execution context (domain/channel/debug)
     * @param entryInputs request-derived entry slot values (e.g. {@code query}, optional {@code scope})
     * @return the value of {@code graph.outputSlot()} on {@code graph.outputNodeId()}
     */
    public Object execute(ParadigmGraph graph, ExecContext ctx, Map<String, Object> entryInputs) {
        Map<String, SlotValues> results = new ConcurrentHashMap<>();
        Map<String, Object> entries = entryInputs != null ? entryInputs : Map.of();

        // Predecessor sets (distinct upstream nodes) drive readiness; remaining[] counts undone preds.
        Map<String, Set<String>> preds = new HashMap<>();
        Map<String, List<String>> succs = new HashMap<>();
        for (String id : graph.nodes().keySet()) {
            preds.put(id, new HashSet<>());
            succs.put(id, new ArrayList<>());
        }
        for (EdgeDef e : graph.edges()) {
            if (preds.get(e.toNode()).add(e.fromNode())) {
                succs.get(e.fromNode()).add(e.toNode());
            }
        }
        Map<String, Integer> remaining = new HashMap<>();
        preds.forEach((id, p) -> remaining.put(id, p.size()));

        Set<String> done = new HashSet<>();
        while (done.size() < graph.nodes().size()) {
            List<String> ready = new ArrayList<>();
            for (var entry : remaining.entrySet()) {
                if (entry.getValue() == 0 && !done.contains(entry.getKey())) {
                    ready.add(entry.getKey());
                }
            }
            if (ready.isEmpty()) {
                // Should never happen on an acyclic graph (compiler guarantees), defensive guard.
                throw new OperatorException("execution stalled — no ready nodes (cycle or orphan?)");
            }

            List<CompletableFuture<Void>> futures = new ArrayList<>();
            for (String nodeId : ready) {
                futures.add(CompletableFuture.runAsync(
                        () -> runNode(graph, nodeId, ctx, results, entries), executor));
            }
            try {
                CompletableFuture.allOf(futures.toArray(CompletableFuture[]::new)).join();
            } catch (CompletionException ce) {
                Throwable cause = ce.getCause() != null ? ce.getCause() : ce;
                if (cause instanceof OperatorException oe) {
                    throw oe;
                }
                throw new OperatorException("paradigm execution failed: " + cause.getMessage(), cause);
            }

            for (String nodeId : ready) {
                done.add(nodeId);
                for (String s : succs.get(nodeId)) {
                    remaining.merge(s, -1, Integer::sum);
                }
            }
        }

        SlotValues outValues = results.get(graph.outputNodeId());
        return outValues != null ? outValues.get(graph.outputSlot()) : null;
    }

    // -------------------------------------------------------------------------
    // Single-node execution
    // -------------------------------------------------------------------------

    private void runNode(
            ParadigmGraph graph,
            String nodeId,
            ExecContext ctx,
            Map<String, SlotValues> results,
            Map<String, Object> entries) {

        NodeDef node = graph.nodes().get(nodeId);
        Operator op = registry.get(node.operatorType());
        OperatorDef def = op.definition();

        // Virtual threads do not inherit ThreadLocal — set domain explicitly per node (as SearchService does).
        DomainContext.set(ctx.domain());
        llmClient.setKnowledgeDomain(ctx.domain());
        long t0 = System.nanoTime();
        try {
            SlotValues inputs = SlotBinder.bindInputs(node, def, graph, results, entries);
            SlotValues outputs;
            try {
                outputs = op.execute(inputs, new Params(node.params()), ctx);
                SlotBinder.validateOutputs(def, outputs);
            } catch (Exception e) {
                outputs = handleNodeFailure(node, def, e);
            }
            results.put(nodeId, outputs != null ? outputs : new SlotValues());

            if (ctx.debug()) {
                long ms = (System.nanoTime() - t0) / 1_000_000;
                ctx.addNodeTrace(new ExecContext.NodeTrace(nodeId, def.type(), ms, summarize(outputs, def)));
            }
        } finally {
            llmClient.clearKnowledgeDomain();
            DomainContext.clear();
        }
    }

    /** Apply the operator's error policy to a thrown exception. */
    private SlotValues handleNodeFailure(NodeDef node, OperatorDef def, Exception e) {
        ErrorPolicy policy = def.errorPolicy();
        if (policy == ErrorPolicy.SKIP_WITH_EMPTY) {
            log.warn("[operator] node={} type={} failed, SKIP_WITH_EMPTY: {}",
                    node.nodeId(), def.type(), e.getMessage());
            return emptyOutputs(def);
        }
        // FAIL_FAST / FALLBACK (already exhausted internally) → abort the run
        throw new OperatorException(
                "operator '" + def.type() + "' (node " + node.nodeId() + ") failed: " + e.getMessage(), e);
    }

    /** Build empty outputs for SKIP_WITH_EMPTY: list slots → empty list, others → absent. */
    private SlotValues emptyOutputs(OperatorDef def) {
        SlotValues sv = new SlotValues();
        for (SlotDecl slot : def.outputSlots()) {
            SlotType t = slot.type();
            if (t == SlotType.CANDIDATE_LIST || t == SlotType.CANDIDATE_LIST_MULTI
                    || t == SlotType.STRING_LIST || t == SlotType.HYDRATED_EVIDENCE_LIST) {
                sv.put(slot.name(), new ArrayList<>());
            }
        }
        return sv;
    }

    private static String summarize(SlotValues outputs, OperatorDef def) {
        if (outputs == null) return "null";
        StringBuilder sb = new StringBuilder();
        for (SlotDecl slot : def.outputSlots()) {
            Object v = outputs.get(slot.name());
            if (sb.length() > 0) sb.append(", ");
            sb.append(slot.name()).append('=');
            if (v instanceof List<?> list) {
                sb.append("list[").append(list.size()).append(']');
            } else if (v instanceof float[] arr) {
                sb.append("vec[").append(arr.length).append(']');
            } else {
                sb.append(v == null ? "null" : v.getClass().getSimpleName());
            }
        }
        return sb.toString();
    }
}
