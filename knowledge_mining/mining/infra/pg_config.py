"""PostgreSQL connection configuration for Mining.

配置来源：main_control_service 的 ``GET /api/v1/system/database/raw``
（main_control_service/config/system/database.yaml 的 default 段）。**不再读 .env。**

- 无参 ``MiningDbConfig()``：读控制面缓存（启动时 lifespan 预填）。
- 显式 kwargs ``MiningDbConfig(pg_host=..., ...)``：直接构造（测试用）。

per-domain 连接仍由 ``resolve_domain_database`` 从 domain_registry.yaml 解析；
本类是"全局默认库"，供 workflow 目录 / KB 管理元数据等非 per-domain 表使用。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from psycopg.conninfo import make_conninfo

from .control_plane import get_database_config


def conninfo_from_url(url: str) -> str:
    """Convert a supported PostgreSQL URL to safely escaped conninfo."""
    value = str(url).strip()
    if value.startswith("jdbc:"):
        value = value[5:]

    try:
        parsed = urlparse(value)
        if parsed.scheme not in ("postgresql", "postgres"):
            raise ValueError("Invalid URL scheme for PostgreSQL database URL")
        if not parsed.hostname or not parsed.path.strip("/"):
            raise ValueError

        params: dict[str, object] = {
            "host": parsed.hostname,
            "dbname": unquote(parsed.path.strip("/")),
        }
        if parsed.port is not None:
            params["port"] = parsed.port
        if parsed.username:
            params["user"] = unquote(parsed.username)
        if parsed.password:
            params["password"] = unquote(parsed.password)

        for key, values in parse_qs(parsed.query, keep_blank_values=True).items():
            if values:
                params[key] = values[0]

        return make_conninfo(**params)
    except ValueError as exc:
        if str(exc).startswith("Invalid URL scheme"):
            raise
        raise ValueError("Invalid PostgreSQL database URL") from exc
    except Exception as exc:
        raise ValueError("Invalid PostgreSQL database URL") from exc


_DB_INT_FIELDS = ("pg_port", "pg_pool_min", "pg_pool_max")


def _coerce_db_fields(d: dict[str, Any]) -> dict[str, Any]:
    """控制面 database.default dict → MiningDbConfig 字段（带类型规整）。"""
    return {
        "pg_host": str(d["host"]),
        "pg_port": int(d.get("port", 5432)),
        "pg_dbname": str(d["dbname"]),
        "pg_user": str(d["user"]),
        "pg_password": str(d["password"]),
        "pg_sslmode": str(d.get("sslmode", "disable")),
        "pg_gssencmode": str(d.get("gssencmode", "disable")),
        "pg_pool_min": int(d.get("pool_min", 2)),
        "pg_pool_max": int(d.get("pool_max", 10)),
    }


class MiningDbConfig:
    """PostgreSQL 连接配置。无参 → 控制面；显式 kwargs → 直接构造（测试）。"""

    def __init__(self, **fields: Any) -> None:
        if not fields:
            data = get_database_config().get("default") or {}
            if not data.get("host") or not data.get("dbname"):
                raise ValueError(
                    "Database config from control plane is missing host/dbname; "
                    "check main_control_service/config/system/database.yaml and that "
                    "main_control_service is reachable."
                )
            fields = _coerce_db_fields(data)
        else:
            # 显式构造（测试）：补默认 + 整型规整
            fields = {
                "pg_host": fields.get("pg_host", ""),
                "pg_port": int(fields.get("pg_port", 5432)),
                "pg_dbname": fields.get("pg_dbname", ""),
                "pg_user": fields.get("pg_user", ""),
                "pg_password": fields.get("pg_password", ""),
                "pg_sslmode": fields.get("pg_sslmode", "disable"),
                "pg_gssencmode": fields.get("pg_gssencmode", "disable"),
                "pg_pool_min": int(fields.get("pg_pool_min", 2)),
                "pg_pool_max": int(fields.get("pg_pool_max", 10)),
            }
        self.__dict__.update(fields)

    @property
    def conninfo(self) -> str:
        """psycopg connection string."""
        return make_conninfo(
            host=self.pg_host,
            port=self.pg_port,
            dbname=self.pg_dbname,
            user=self.pg_user,
            password=self.pg_password,
            sslmode=self.pg_sslmode,
            gssencmode=self.pg_gssencmode,
        )

    @property
    def maintenance_conninfo(self) -> str:
        """Connection string for the postgres maintenance DB (used to CREATE DATABASE)."""
        return make_conninfo(
            host=self.pg_host,
            port=self.pg_port,
            dbname="postgres",
            user=self.pg_user,
            password=self.pg_password,
            sslmode=self.pg_sslmode,
            gssencmode=self.pg_gssencmode,
        )

    def __repr__(self) -> str:  # 不泄露 password
        return (
            f"MiningDbConfig(host={self.pg_host!r}, port={self.pg_port}, "
            f"dbname={self.pg_dbname!r}, user={self.pg_user!r})"
        )
