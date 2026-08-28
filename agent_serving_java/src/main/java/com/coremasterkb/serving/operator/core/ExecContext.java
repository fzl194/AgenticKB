package com.coremasterkb.serving.operator.core;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Per-execution shared context. Operators are stateless singletons; all mutable request state
 * lives here so operator instances stay thread-safe and cacheable.
 *
 * <p>{@code attributes} carries cross-operator helper data (e.g. {@code releaseId}, {@code buildId}
 * stuffed by {@code scope_resolve}). {@code nodeTraces} accumulates per-node timing when
 * {@code debug} is on.</p>
 */
public final class ExecContext {

    /** A single node's execution trace entry. */
    public record NodeTrace(String nodeId, String operatorType, long durationMs, String summary) {}

    private final String requestId;
    private final String domain;
    private final String channel;
    private final boolean debug;
    private final String username;
    private volatile String query;
    private volatile List<String> requestKbIds;
    private final Map<String, Object> attributes = new ConcurrentHashMap<>();
    private final List<NodeTrace> nodeTraces = new CopyOnWriteArrayList<>();

    public ExecContext(String requestId, String domain, String channel, boolean debug) {
        this(requestId, domain, channel, debug, null);
    }

    public ExecContext(String requestId, String domain, String channel, boolean debug,
                       String username) {
        this.requestId = requestId;
        this.domain = domain;
        this.channel = channel;
        this.debug = debug;
        this.username = username;
    }

    /** The request query, available to entry operators (e.g. request_input). */
    public String query() { return query; }
    public void setQuery(String query) { this.query = query; }

    /**
     * Request-level KB scope (阶段 A "菜谱+运行时范围")：搜索请求现场指定的库组合。
     * 只对图内 {@code scope_resolve.kbIds} 留空的范式生效——写死 kbIds 的专属范式
     * 优先按图执行（请求值被忽略并留痕 attribute）。空列表 = 未指定（域级 release）。
     */
    public List<String> requestKbIds() { return requestKbIds; }
    public void setRequestKbIds(List<String> kbIds) {
        this.requestKbIds = (kbIds == null) ? List.of() : List.copyOf(kbIds);
    }

    public String requestId() { return requestId; }
    public String domain()    { return domain; }
    public String channel()   { return channel; }
    public boolean debug()    { return debug; }

    /** Caller identity from the {@code X-KB-User} header; null when anonymous. */
    public String username()  { return username; }

    public Map<String, Object> attributes() { return attributes; }

    public void putAttribute(String key, Object value) {
        if (value != null) attributes.put(key, value);
    }

    public List<NodeTrace> nodeTraces() { return nodeTraces; }

    public void addNodeTrace(NodeTrace trace) {
        nodeTraces.add(trace);
    }
}
