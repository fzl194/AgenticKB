package com.coremasterkb.serving.domainpack;

import java.util.concurrent.Callable;
import java.util.function.Supplier;

/**
 * Thread-local carrier for the active domain during a search request.
 *
 * <p>Set by the entry services ({@code FullTextService}/{@code RawFileService}/
 * {@code ParadigmExecutor}) before any DB operation and cleared in a
 * finally block. {@code DomainRoutingDataSource} reads this value to pick the
 * correct connection pool for every JDBC call on the current thread.
 *
 * <p>When spawning async work (e.g. via {@code CompletableFuture}), use
 * {@link #wrapCallable} or {@link #wrapRunnable} to propagate the domain
 * value to the child thread.
 */
public final class DomainContext {

    private static final ThreadLocal<String> CURRENT = new ThreadLocal<>();

    private DomainContext() {}

    public static void set(String domain) {
        CURRENT.set(domain);
    }

    public static String get() {
        return CURRENT.get();
    }

    public static void clear() {
        CURRENT.remove();
    }

    /**
     * Wrap a {@link Callable} so the domain active on the calling thread
     * is propagated to the worker thread and cleared after execution.
     */
    public static <T> Callable<T> wrapCallable(Callable<T> task) {
        String capturedDomain = CURRENT.get();
        return () -> {
            CURRENT.set(capturedDomain);
            try {
                return task.call();
            } finally {
                CURRENT.remove();
            }
        };
    }

    /**
     * Wrap a {@link Runnable} so the domain active on the calling thread
     * is propagated to the worker thread and cleared after execution.
     */
    public static Runnable wrapRunnable(Runnable task) {
        String capturedDomain = CURRENT.get();
        return () -> {
            CURRENT.set(capturedDomain);
            try {
                task.run();
            } finally {
                CURRENT.remove();
            }
        };
    }

    /**
     * Wrap a {@link Supplier} so the domain active on the calling thread
     * is propagated to the worker thread and cleared after execution.
     */
    public static <T> Supplier<T> wrapSupplier(Supplier<T> task) {
        String capturedDomain = CURRENT.get();
        return () -> {
            CURRENT.set(capturedDomain);
            try {
                return task.get();
            } finally {
                CURRENT.remove();
            }
        };
    }
}
