package com.coremasterkb.serving.observability;

import com.coremasterkb.serving.operator.api.ParadigmExecutionService.RunArgs;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.time.Instant;

@Aspect
@Component
public class QueryLogAspect {
    private static final Logger log = LoggerFactory.getLogger(QueryLogAspect.class);

    private final KnowledgeAccessRecordService recordService;

    public QueryLogAspect(KnowledgeAccessRecordService recordService) {
        this.recordService = recordService;
    }

    @Around("execution(* com.coremasterkb.serving.operator.api.ParadigmExecutionService.run(..))")
    public Object logParadigmSearch(ProceedingJoinPoint joinPoint) throws Throwable {
        Object[] methodArgs = joinPoint.getArgs();
        if (methodArgs.length < 2 || !(methodArgs[1] instanceof RunArgs runArgs)) {
            return joinPoint.proceed();
        }

        Instant startedAt = Instant.now();
        long startedNanos = System.nanoTime();
        Object result = null;
        Throwable failure = null;
        try {
            result = joinPoint.proceed();
            return result;
        } catch (Throwable thrown) {
            failure = thrown;
            throw thrown;
        } finally {
            long durationMs = Math.max(0L, (System.nanoTime() - startedNanos) / 1_000_000L);
            recordService.record(startedAt, runArgs, result, failure, durationMs);
            if (failure != null) {
                log.warn("[knowledge-access] search failed id={} source={} duration_ms={} code={}",
                        runArgs.accessId(), runArgs.source(), durationMs,
                        failure.getClass().getSimpleName());
            }
        }
    }
}
