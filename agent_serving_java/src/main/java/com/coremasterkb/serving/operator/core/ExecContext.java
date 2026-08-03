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
