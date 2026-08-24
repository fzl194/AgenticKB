"""Lazy, shared PostgreSQL pools resolved per mining domain."""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any, Callable

from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool

from knowledge_mining.mining.infra.domain_db import (
    ResolvedDomainDatabase,
    ensure_domain_database_schema,
    resolve_domain_database,
)
from knowledge_mining.mining.infra.domain_pack import resolve_domain
from knowledge_mining.mining.infra.pg_config import MiningDbConfig


PoolKey = tuple[tuple[tuple[str, str], ...], int, int]


@dataclass
class _PoolGroup:
    async_pool: Any | None = None
    sync_pool: Any | None = None


def _pool_key(resolved: ResolvedDomainDatabase) -> PoolKey:
    canonical = tuple(sorted(conninfo_to_dict(resolved.conninfo).items()))
    return canonical, resolved.pool_min, resolved.pool_max


class DomainPoolManager:
    """Own and cache one async/sync pool pair per resolved database."""

    def __init__(
        self,
        default_config: MiningDbConfig,
        *,
        domain_resolver: Callable[[str], dict[str, Any]] = resolve_domain,
        database_resolver: Callable[..., ResolvedDomainDatabase] = resolve_domain_database,
        ensure_schema_fn: Callable[
            [ResolvedDomainDatabase], None
        ] = ensure_domain_database_schema,
        async_pool_factory: Callable[..., Any] = AsyncConnectionPool,
        sync_pool_factory: Callable[..., Any] = ConnectionPool,
    ) -> None:
        self._default = default_config
        self._domain_resolver = domain_resolver
        self._database_resolver = database_resolver
        self._ensure_schema = ensure_schema_fn
        self._async_factory = async_pool_factory
        self._sync_factory = sync_pool_factory
        self._groups: dict[PoolKey, _PoolGroup] = {}
        self._ensured: set[PoolKey] = set()
        self._ensure_locks: dict[PoolKey, threading.Lock] = {}
        self._state_lock = threading.RLock()
        self._async_lock = asyncio.Lock()
        self._closed = False

    def _resolve(self, domain: str) -> tuple[ResolvedDomainDatabase, PoolKey]:
        entry = self._domain_resolver(domain)
        resolved = self._database_resolver(entry, self._default)
        return resolved, _pool_key(resolved)

    def _ensure_once(self, key: PoolKey, resolved: ResolvedDomainDatabase) -> None:
        """对目标库跑一次全量 DDL（幂等）。按库单飞；DDL 不持有 _state_lock。

        远端库上一套重放要跑几十秒——若在 _state_lock 内执行，其他协程在
        fast-path 上的 `with self._state_lock` 会被连带冻住（那可是事件循环线程）。
        """
        with self._state_lock:
            if key in self._ensured:
                return
            ensure_lock = self._ensure_locks.setdefault(key, threading.Lock())
        with ensure_lock:
            with self._state_lock:  # 双检：等锁期间可能已被前一个完成
                if key in self._ensured:
                    return
            self._ensure_schema(resolved)
            with self._state_lock:
                self._ensured.add(key)

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("Domain pool manager is closed")

    async def async_pool(self, domain: str):
        resolved, key = self._resolve(domain)
        with self._state_lock:
            self._assert_open()
            group = self._groups.get(key)
            if group and group.async_pool is not None:
                return group.async_pool

        async with self._async_lock:
            with self._state_lock:
                self._assert_open()
                group = self._groups.get(key)
                if group and group.async_pool is not None:
                    return group.async_pool

            try:
                # ensure 必须跑在工作线程：DDL 里的 ALTER TABLE 若撞上别的连接
                # 未提交的事务会在服务端排队等锁——直接跑在事件循环上时，持有
                # 事务的协程永远轮不到 commit，两者互等即全服务冻结
                # （2026-08-24 Docker 登录并发触发的事故）。
                await asyncio.to_thread(self._ensure_once, key, resolved)
                candidate = self._async_factory(
                    resolved.conninfo,
                    min_size=resolved.pool_min,
                    max_size=resolved.pool_max,
                    open=False,
                    check=getattr(self._async_factory, "check_connection", None),
                    max_idle=300.0,
                    kwargs={"row_factory": dict_row},
                )
                await candidate.open()
            except Exception:
                if "candidate" in locals():
                    try:
                        await candidate.close()
                    except Exception:
                        pass
                raise RuntimeError("Unable to initialize domain database pool") from None

            with self._state_lock:
                group = self._groups.setdefault(key, _PoolGroup())
                group.async_pool = candidate
            return candidate

    def sync_pool(self, domain: str):
        resolved, key = self._resolve(domain)
        with self._state_lock:
            self._assert_open()
            group = self._groups.get(key)
            if group and group.sync_pool is not None:
                return group.sync_pool
            try:
                self._ensure_once(key, resolved)
                candidate = self._sync_factory(
                    resolved.conninfo,
                    min_size=resolved.pool_min,
                    max_size=resolved.pool_max,
                    open=False,
                    check=getattr(self._sync_factory, "check_connection", None),
                    max_idle=300.0,
                    kwargs={"row_factory": dict_row},
                )
                candidate.open()
            except Exception:
                if "candidate" in locals():
                    try:
                        candidate.close()
                    except Exception:
                        pass
                raise RuntimeError("Unable to initialize domain database pool") from None
            group = self._groups.setdefault(key, _PoolGroup())
            group.sync_pool = candidate
            return candidate

    async def close(self) -> None:
        async with self._async_lock:
            with self._state_lock:
                if self._closed:
                    return
                self._closed = True
                groups = list(self._groups.values())
                self._groups.clear()

            for group in groups:
                if group.async_pool is not None:
                    await group.async_pool.close()
                if group.sync_pool is not None:
                    group.sync_pool.close()
