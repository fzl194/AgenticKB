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
                       WHERE domain = %s AND status IN ('queued', 'running')
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
