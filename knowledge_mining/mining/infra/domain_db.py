"""Resolve one domain registry entry to a PostgreSQL connection target."""
from __future__ import annotations

import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Literal, Mapping

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from .pg_config import MiningDbConfig, conninfo_from_url

#: TCP keepalive 探活参数（同 pg_config.MiningDbConfig.conninfo 注释）：
#: 大文件解析期间池化连接空闲数分钟，云上 NAT/端口转发会静默回收空闲
#: TCP；30s 探活让连接永不进入空闲判定窗口（ADR-0003 D-040）。
_KEEPALIVE_PARAMS = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}


@dataclass(frozen=True)
class ResolvedDomainDatabase:
    conninfo: str
    pool_min: int
    pool_max: int
    source: Literal["env", "inline", "global"]


def _pool_bounds(pool_min: object, pool_max: object) -> tuple[int, int]:
    if (
        isinstance(pool_min, bool)
        or isinstance(pool_max, bool)
        or not isinstance(pool_min, int)
        or not isinstance(pool_max, int)
        or pool_min < 1
        or pool_max < pool_min
    ):
        raise ValueError("Invalid database pool bounds")
    return pool_min, pool_max


def _resolved_from_url(
    value: str, *, source: Literal["env"], default: MiningDbConfig
) -> ResolvedDomainDatabase:
    pool_min, pool_max = _pool_bounds(default.pg_pool_min, default.pg_pool_max)
    return ResolvedDomainDatabase(
        make_conninfo(conninfo_from_url(value), **_KEEPALIVE_PARAMS),
        pool_min, pool_max, source,
    )


def _resolved_from_inline(database: object) -> ResolvedDomainDatabase:
    if not isinstance(database, Mapping):
        raise ValueError("Invalid inline database configuration")

    pool_min, pool_max = _pool_bounds(
        database.get("pool_min", 2), database.get("pool_max", 10)
    )
    conn_params = {
        key: database[key]
        for key in (
            "host",
            "port",
            "dbname",
            "user",
            "password",
            "sslmode",
            "gssencmode",
            "connect_timeout",
        )
        if database.get(key) is not None
    }
    if not conn_params.get("host") or not conn_params.get("dbname"):
        raise ValueError("Invalid inline database configuration")
    try:
        conninfo = make_conninfo(**{**conn_params, **_KEEPALIVE_PARAMS})
    except Exception as exc:
        raise ValueError("Invalid inline database configuration") from exc
    return ResolvedDomainDatabase(conninfo, pool_min, pool_max, "inline")


def resolve_domain_database(
    entry: Mapping[str, object],
    default: MiningDbConfig,
    environ: Mapping[str, str] | None = None,
) -> ResolvedDomainDatabase:
    """Resolve inline registry config, then global config.

    domain 的库只从 domain_registry.yaml 的内联 ``database:`` 块取；未配置则回落
    全局默认（``default`` = 控制面 database.yaml）。**不再从环境变量取**——历史
    的 ``database_url_env`` env 覆盖路径已移除，domain 库一律由 registry 决定。
    ``environ`` 形参仅为签名兼容保留，不再使用。
    """
    if entry.get("database") is not None:
        return _resolved_from_inline(entry["database"])

    pool_min, pool_max = _pool_bounds(default.pg_pool_min, default.pg_pool_max)
    return ResolvedDomainDatabase(default.conninfo, pool_min, pool_max, "global")


def ensure_domain_database_schema(resolved: ResolvedDomainDatabase) -> None:
    """Run Domain-safe schemas for a resolved database."""
    from .pg_schema import ensure_domain_schema

    values = conninfo_to_dict(resolved.conninfo)
    dbname = values.get("dbname")
    if not dbname:
        raise RuntimeError("Resolved domain database has no database name")
    cfg = SimpleNamespace(
        conninfo=resolved.conninfo,
        maintenance_conninfo=make_conninfo(resolved.conninfo, dbname="postgres"),
        pg_dbname=dbname,
    )
    ensure_domain_schema(cfg)
