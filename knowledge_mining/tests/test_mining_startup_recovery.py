"""P07-S1 — startup recovery scans every domain and resumes workflow runs only."""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = ()

    async def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return _Cursor(self.rows)


class _Pool:
    def __init__(self, rows):
        self.connection_impl = _Connection(rows)

    @asynccontextmanager
    async def connection(self):
        yield self.connection_impl


class _DomainPools:
    def __init__(self, pools):
        self.pools = pools

    async def async_pool(self, domain):
        pool = self.pools[domain]
        if isinstance(pool, Exception):
            raise pool
        return pool


@pytest.mark.asyncio
async def test_startup_recovery_interrupts_stale_runs_and_resumes_workflow_only():
    from knowledge_mining.mining.api.startup_recovery import recover_startup_runs

    first = _Pool([
        {"id": "workflow-1", "domain": "d1", "execution_engine": "workflow"},
        {"id": "legacy-1", "domain": "d1", "execution_engine": "legacy"},
    ])
    second = _Pool([])
    resumed = []

    async def resume_workflow(run_id, domain):
        resumed.append((run_id, domain))

    result = await recover_startup_runs(
        domain_ids=("d1", "d2"),
        domain_pools=_DomainPools({"d1": first, "d2": second}),
        now="2026-08-25T00:00:00+00:00",
    )

    assert result.interrupted_run_ids == ("workflow-1", "legacy-1")
    # 标记阶段不得触发 resume（不阻塞启动）；恢复由后台任务执行
    assert resumed == []
    assert result.workflow_runs == (("workflow-1", "d1"),)

    from knowledge_mining.mining.api.startup_recovery import schedule_startup_resumes
    task = schedule_startup_resumes(result, resume_workflow)
    await task
    assert resumed == [("workflow-1", "d1")]
    assert "UPDATE mining_runs" in first.connection_impl.sql
    assert "RETURNING id, domain, execution_engine" in first.connection_impl.sql
    assert first.connection_impl.params[0] == "2026-08-25T00:00:00+00:00"


@pytest.mark.asyncio
async def test_startup_recovery_continues_after_one_domain_fails():
    from knowledge_mining.mining.api.startup_recovery import recover_startup_runs

    healthy = _Pool([])
    result = await recover_startup_runs(
        domain_ids=("broken", "healthy"),
        domain_pools=_DomainPools({"broken": RuntimeError("offline"), "healthy": healthy}),
        now="2026-08-25T00:00:00+00:00",
    )

    assert result.failed_domains == ("broken",)
    assert "UPDATE mining_runs" in healthy.connection_impl.sql


@pytest.mark.asyncio
async def test_scheduled_resumes_run_in_background_without_blocking_caller():
    """lifespan 只拿 task 不等它 —— 长挖掘不得拖住 API 启动/健康检查。"""
    import asyncio

    from knowledge_mining.mining.api.startup_recovery import (
        StartupRecoveryResult, schedule_startup_resumes,
    )
    release = asyncio.Event()
    resumed = []

    async def slow_resume(run_id: str, domain: str) -> None:
        await release.wait()
        resumed.append(run_id)

    result = StartupRecoveryResult(
        interrupted_run_ids=("w-1",),
        failed_domains=(),
        workflow_runs=(("w-1", "d1"),),
    )
    task = schedule_startup_resumes(result, slow_resume)
    try:
        # 恢复挂起期间调用方不被拖住（可继续干别的）
        await asyncio.sleep(0.01)
        assert resumed == []
        assert not task.done()
    finally:
        release.set()
        await task
    assert resumed == ["w-1"]
