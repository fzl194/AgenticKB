"""Single-instance startup recovery for interrupted Mining runs (P07-S1)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)


class DomainPools(Protocol):
    async def async_pool(self, domain: str): ...


ResumeWorkflow = Callable[[str, str], Awaitable[None] | None]


@dataclass(frozen=True)
class StartupRecoveryResult:
    interrupted_run_ids: tuple[str, ...]
    failed_domains: tuple[str, ...]
    workflow_runs: tuple[tuple[str, str], ...] = ()


async def recover_startup_runs(
    *,
    domain_ids: tuple[str, ...],
    domain_pools: DomainPools,
    now: str,
) -> StartupRecoveryResult:
    """Interrupt abandoned runs atomically; collect workflow runs for background resume.

    仅做标记（毫秒级），供 lifespan 同步等待；恢复是分钟级长任务，
    必须走 schedule_startup_resumes 后台执行，否则会拖住 API 启动与健康检查。
    """
    interrupted: list[str] = []
    failed_domains: list[str] = []
    workflow_runs: list[tuple[str, str]] = []
    for domain in domain_ids:
        try:
            pool = await domain_pools.async_pool(domain)
            async with pool.connection() as conn:
                cursor = await conn.execute(
                    """UPDATE mining_runs
                       SET status = 'interrupted', finished_at = %s,
                           metadata_json = COALESCE(metadata_json, '{}'::jsonb)
                               || '{\"interrupted_by\": \"restart\"}'::jsonb
                       WHERE domain = %s AND status = 'running'
                         AND finished_at IS NULL
                         AND (worker_id IS NULL OR lease_until IS NULL OR lease_until < NOW())
                       RETURNING id, domain, execution_engine""",
                    [now, domain],
                )
                rows = await cursor.fetchall()
        except Exception:
            logger.exception("Startup recovery scan failed for domain %s", domain)
            failed_domains.append(domain)
            continue
        for row in rows:
            run_id = str(row["id"])
            interrupted.append(run_id)
            if str(row.get("execution_engine") or "legacy") == "workflow":
                workflow_runs.append((run_id, str(row["domain"])))

    return StartupRecoveryResult(
        tuple(interrupted), tuple(failed_domains), tuple(workflow_runs),
    )


# 持有后台任务强引用，防止事件循环只留弱引用导致任务被 GC 中途取消。
_background_resume_tasks: set[asyncio.Task] = set()


def schedule_startup_resumes(
    result: StartupRecoveryResult,
    resume_workflow: ResumeWorkflow,
    *,
    max_concurrency: int = 2,
) -> "asyncio.Future[None]":
    """在后台恢复 workflow Run：lifespan 只拿 future 不等待，长挖掘不拖住启动。"""
    semaphore = asyncio.Semaphore(max_concurrency)

    async def resume_one(run_id: str, domain: str) -> None:
        async with semaphore:
            try:
                outcome = resume_workflow(run_id, domain)
                if outcome is not None:
                    await outcome
            except Exception:
                logger.exception("Startup recovery resume failed for run %s", run_id)

    # gather 返回的 Future 已被事件循环调度，无需（也不可）再包 create_task。
    task: "asyncio.Future[None]" = asyncio.gather(  # type: ignore[assignment]
        *(resume_one(run_id, domain) for run_id, domain in result.workflow_runs)
    )
    _background_resume_tasks.add(task)
    task.add_done_callback(_background_resume_tasks.discard)
    return task


async def _lease_expiry_sweep_once(
    *,
    domain_ids: tuple[str, ...],
    domain_pools: DomainPools,
    resume_workflow: ResumeWorkflow,
    max_concurrency: int,
) -> None:
    """重跑一次启动恢复：处理重启瞬间仍持有有效租约、随后才到期的僵尸 Run。"""
    from datetime import datetime, timezone

    recovery = await recover_startup_runs(
        domain_ids=domain_ids,
        domain_pools=domain_pools,
        now=datetime.now(timezone.utc).isoformat(),
    )
    if recovery.interrupted_run_ids:
        logger.warning(
            "Lease-expiry sweep interrupted %d abandoned run(s): %s",
            len(recovery.interrupted_run_ids),
            ", ".join(recovery.interrupted_run_ids),
        )
    if recovery.workflow_runs:
        await schedule_startup_resumes(
            recovery, resume_workflow, max_concurrency=max_concurrency,
        )


def schedule_lease_expiry_sweep(
    *,
    domain_ids: tuple[str, ...],
    domain_pools: DomainPools,
    resume_workflow: ResumeWorkflow,
    delay_seconds: float = 360.0,
    max_concurrency: int = 2,
) -> "asyncio.Task[None]":
    """租约到期补扫（P07-S2）：修复「崩溃重启 → 有效租约挡住首轮恢复 → 永不自愈」。

    worker 崩溃时租约仍剩 ≤300s 有效期；首轮 recover_startup_runs 按
    多实例语义跳过这类 Run。本任务在租约窗口（默认 300s）+ 余量后重跑同一
    恢复逻辑，把已到期的僵尸标记 interrupted 并后台 resume——单实例自愈
    时延收敛到 ~6 分钟。多实例下仍安全：活 worker 持续心跳 → 租约有效 →
    本轮照旧跳过。
    """
    async def _sweep() -> None:
        await asyncio.sleep(delay_seconds)
        try:
            await _lease_expiry_sweep_once(
                domain_ids=domain_ids,
                domain_pools=domain_pools,
                resume_workflow=resume_workflow,
                max_concurrency=max_concurrency,
            )
        except Exception:
            logger.exception("Lease-expiry sweep failed")

    task = asyncio.ensure_future(_sweep())
    _background_resume_tasks.add(task)
    task.add_done_callback(_background_resume_tasks.discard)
    return task
