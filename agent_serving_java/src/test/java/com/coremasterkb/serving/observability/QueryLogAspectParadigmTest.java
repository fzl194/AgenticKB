package com.coremasterkb.serving.observability;

import com.coremasterkb.serving.operator.api.ParadigmExecutionService.RunArgs;
import org.aspectj.lang.ProceedingJoinPoint;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.Instant;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class QueryLogAspectParadigmTest {
    private KnowledgeAccessRecordService recordService;
    private QueryLogAspect aspect;

    @BeforeEach
    void setUp() {
        recordService = mock(KnowledgeAccessRecordService.class);
        aspect = new QueryLogAspect(recordService);
    }

    @Test
    void recordsResultWithOriginalRunArgs() throws Throwable {
        RunArgs args = new RunArgs("q", "d", "prod", false, "alice")
                .withAccess("call-1", "web");
        ProceedingJoinPoint joinPoint = mock(ProceedingJoinPoint.class);
        when(joinPoint.getArgs()).thenReturn(new Object[]{null, args});
        when(joinPoint.proceed()).thenReturn(Map.of("result", "ok"));

        Object result = aspect.logParadigmSearch(joinPoint);

        assertThat(result).isEqualTo(Map.of("result", "ok"));
        verify(recordService).record(any(Instant.class), same(args),
                eq(Map.of("result", "ok")), isNull(), anyLong());
    }

    @Test
    void recordsFailureAndRethrowsIt() throws Throwable {
        RunArgs args = new RunArgs("q", "d", "prod", false, "alice");
        ProceedingJoinPoint joinPoint = mock(ProceedingJoinPoint.class);
        when(joinPoint.getArgs()).thenReturn(new Object[]{null, args});
        when(joinPoint.proceed()).thenThrow(new IllegalStateException("boom"));

        assertThrows(IllegalStateException.class, () -> aspect.logParadigmSearch(joinPoint));

        ArgumentCaptor<Throwable> failure = ArgumentCaptor.forClass(Throwable.class);
        verify(recordService).record(any(Instant.class), same(args), isNull(),
                failure.capture(), anyLong());
        assertThat(failure.getValue()).isInstanceOf(IllegalStateException.class);
    }

    @Test
    void unexpectedSignatureDoesNotTouchRecorder() throws Throwable {
        ProceedingJoinPoint joinPoint = mock(ProceedingJoinPoint.class);
        when(joinPoint.getArgs()).thenReturn(new Object[]{"other"});
        when(joinPoint.proceed()).thenReturn("ok");

        assertThat(aspect.logParadigmSearch(joinPoint)).isEqualTo("ok");
        verifyNoInteractions(recordService);
    }
}
