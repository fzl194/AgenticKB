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


async def recover_startup_runs(
    *,
    domain_ids: tuple[str, ...],
    domain_pools: DomainPools,
    resume_workflow: ResumeWorkflow,
    now: str,
    max_concurrency: int = 2,
) -> StartupRecoveryResult:
    """Interrupt abandoned runs and resume only workflow runs after one-process restart."""
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

    semaphore = asyncio.Semaphore(max_concurrency)

    async def resume_one(run_id: str, domain: str) -> None:
        async with semaphore:
            try:
                result = resume_workflow(run_id, domain)
                if result is not None:
                    await result
            except Exception:
                logger.exception("Startup recovery resume failed for run %s", run_id)

    await asyncio.gather(*(resume_one(run_id, domain) for run_id, domain in workflow_runs))
    return StartupRecoveryResult(tuple(interrupted), tuple(failed_domains))
