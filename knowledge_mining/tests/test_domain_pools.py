"""DomainPoolManager 懒初始化并发安全（2026-08-24 Docker 登录冻结事故回归）。

事故链：async_pool 曾把全量 DDL 重放同步跑在事件循环线程上；并发请求里
一个未提交的 kb_users 事务让 ALTER TABLE 在服务端排队等锁，事件循环线程
阻塞在 ALTER 调用上，持事务的协程永远无法被调度去 commit——两者互等，
mining 全服务冻结（前端表现为页面无限转圈）。
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.api.domain_pools import DomainPoolManager
from knowledge_mining.mining.infra.pg_config import MiningDbConfig


async def _wait_until(predicate, timeout: float = 2.0) -> bool:
    """轮询等待，绝不阻塞事件循环（被测对象正是循环冻结问题）。"""
    for _ in range(int(timeout / 0.05)):
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


class FakeAsyncPool:
    def __init__(self, *args, **kwargs):
        self.opened = False

    async def open(self):
        self.opened = True

    async def close(self):
        pass


class FakeSyncPool:
    def __init__(self, *args, **kwargs):
        pass

    def open(self):
        pass

    def close(self):
        pass


def make_manager(ensure_schema_fn=None):
    if ensure_schema_fn is None:
        ensure_schema_fn = lambda resolved: None  # noqa: E731

    return DomainPoolManager(
        MiningDbConfig(pg_host="db"),
        domain_resolver=lambda domain: {"name": domain},
        database_resolver=lambda entry, default: SimpleNamespace(
            conninfo=f"host=db port=5432 dbname={entry['name']} user=u",
            pool_min=1,
            pool_max=2,
        ),
        ensure_schema_fn=ensure_schema_fn,
        async_pool_factory=FakeAsyncPool,
        sync_pool_factory=FakeSyncPool,
    )


@pytest.mark.asyncio
async def test_ensure_runs_off_event_loop_and_loop_stays_alive():
    """修复核心：DDL 重放必须在工作线程跑，阻塞期间事件循环仍可调度协程。"""
    release = threading.Event()
    ensure_thread_ids: list[int] = []

    def blocking_ensure(resolved):
        ensure_thread_ids.append(threading.get_ident())
        assert release.wait(5), "test never released the ensure lock"

    manager = make_manager(blocking_ensure)
    task = asyncio.create_task(manager.async_pool("d1"))
    try:
        assert await _wait_until(lambda: bool(ensure_thread_ids)), "ensure did not start"
        loop_alive = asyncio.Event()

        async def ping():
            loop_alive.set()

        asyncio.create_task(ping())
        # 修复前：ensure 同步占死循环线程，这里必然超时
        await asyncio.wait_for(loop_alive.wait(), timeout=1.0)
        assert all(
            tid != threading.get_ident() for tid in ensure_thread_ids
        ), "ensure_schema ran on the event loop thread"
    finally:
        release.set()
        pool = await asyncio.wait_for(task, timeout=2.0)
        assert pool.opened


@pytest.mark.asyncio
async def test_concurrent_first_calls_run_ensure_once_and_share_pool():
    calls = {"n": 0}
    lock = threading.Lock()
    started = threading.Event()
    release = threading.Event()

    def slow_ensure(resolved):
        with lock:
            calls["n"] += 1
        started.set()
        assert release.wait(5), "test never released the ensure lock"

    manager = make_manager(slow_ensure)
    tasks = [asyncio.create_task(manager.async_pool("d1")) for _ in range(5)]
    assert await _wait_until(started.is_set), "ensure never started"
    await asyncio.sleep(0.2)  # 让其余调用堆到 _async_lock 上
    release.set()
    pools = await asyncio.gather(*tasks)
    assert calls["n"] == 1
    assert all(p is pools[0] for p in pools)


@pytest.mark.asyncio
async def test_second_call_returns_cached_pool_without_re_ensure():
    calls = {"n": 0}

    def counting_ensure(resolved):
        calls["n"] += 1

    manager = make_manager(counting_ensure)
    first = await manager.async_pool("d1")
    second = await manager.async_pool("d1")
    assert first is second
    assert calls["n"] == 1


def test_sync_pool_returns_sync_factory_instance():
    manager = make_manager()
    pool = manager.sync_pool("d1")
    assert isinstance(pool, FakeSyncPool)
