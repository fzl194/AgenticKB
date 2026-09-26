"""Database endpoint loading and same-server clone primitives."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import re
from typing import Any, Mapping

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import yaml


class DatabaseUpgradeError(RuntimeError):
    """A safety precondition for database upgrade was not met."""


@dataclass(frozen=True, slots=True)
class DatabaseEndpoint:
    host: str
    port: int
    dbname: str
    user: str
    password: str
    sslmode: str = "disable"
    gssencmode: str = "disable"

    def with_dbname(self, dbname: str) -> "DatabaseEndpoint":
        return replace(self, dbname=validate_database_name(dbname))

    def with_credentials(self, *, user: str, password: str) -> "DatabaseEndpoint":
        if not user or not password:
            raise DatabaseUpgradeError("迁移管理员用户名和密码必须同时提供")
        return replace(self, user=user, password=password)

    @property
    def conninfo(self) -> str:
        return make_conninfo(
            host=self.host,
            port=self.port,
            dbname=self.dbname,
            user=self.user,
            password=self.password,
            sslmode=self.sslmode,
            gssencmode=self.gssencmode,
            connect_timeout=10,
            application_name="cmkb_database_upgrade",
        )

    @property
    def maintenance_conninfo(self) -> str:
        return self.with_dbname("postgres").conninfo


def validate_database_name(value: str) -> str:
    name = str(value).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name):
        raise DatabaseUpgradeError(
            "数据库名必须以字母/下划线开头，且只包含字母、数字、下划线"
        )
    return name


def load_default_endpoint(config_dir: Path) -> DatabaseEndpoint:
    path = config_dir / "system" / "database.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or not isinstance(raw.get("default"), Mapping):
        raise DatabaseUpgradeError("database.yaml 缺少 default 配置")
    default: Mapping[str, Any] = raw["default"]
    required = ("host", "dbname", "user", "password")
    missing = [key for key in required if default.get(key) in (None, "")]
    if missing:
        raise DatabaseUpgradeError("database.default 缺少：" + ", ".join(missing))
    return DatabaseEndpoint(
        host=str(default["host"]),
        port=int(default.get("port", 5432)),
        dbname=validate_database_name(str(default["dbname"])),
        user=str(default["user"]),
        password=str(default["password"]),
        sslmode=str(default.get("sslmode", "disable")),
        gssencmode=str(default.get("gssencmode", "disable")),
    )


def database_exists(maintenance_connection: Any, dbname: str) -> bool:
    row = maintenance_connection.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
    ).fetchone()
    return row is not None


def migration_admin_endpoint(endpoint: DatabaseEndpoint) -> DatabaseEndpoint:
    """Use deployment-only admin credentials when supplied; never persist them."""

    user = os.getenv("CMKB_MIGRATION_PG_USER")
    password = os.getenv("CMKB_MIGRATION_PG_PASSWORD")
    if bool(user) != bool(password):
        raise DatabaseUpgradeError(
            "CMKB_MIGRATION_PG_USER/CMKB_MIGRATION_PG_PASSWORD 必须同时设置"
        )
    return endpoint if not user else endpoint.with_credentials(user=user, password=password)


def create_empty_database(
    endpoint: DatabaseEndpoint,
    *,
    maintenance_endpoint: DatabaseEndpoint | None = None,
) -> None:
    admin = maintenance_endpoint or endpoint
    with psycopg.connect(admin.maintenance_conninfo, autocommit=True) as conn:
        role_row = conn.execute(
            "SELECT rolsuper OR rolcreatedb FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        if not role_row or not role_row[0]:
            raise DatabaseUpgradeError(
                "迁移账号缺少 CREATEDB；请仅在部署时注入 CMKB_MIGRATION_PG_*"
            )
        if database_exists(conn, endpoint.dbname):
            raise DatabaseUpgradeError(f"目标数据库已存在：{endpoint.dbname}")
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(endpoint.dbname), sql.Identifier(endpoint.user)
            )
        )


def clone_database(
    endpoint: DatabaseEndpoint,
    target_dbname: str,
    *,
    maintenance_endpoint: DatabaseEndpoint | None = None,
    replace_existing: bool = False,
) -> DatabaseEndpoint:
    """Clone one stopped source database, replacing a stale target only by opt-in."""

    target = validate_database_name(target_dbname)
    if target == endpoint.dbname:
        raise DatabaseUpgradeError("目标数据库必须与源数据库不同")
    admin = maintenance_endpoint or endpoint
    with psycopg.connect(admin.maintenance_conninfo, autocommit=True) as conn:
        role_row = conn.execute(
            "SELECT rolsuper OR rolcreatedb FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        if not role_row or not role_row[0]:
            raise DatabaseUpgradeError(
                "迁移账号缺少 CREATEDB；请仅在部署时注入 CMKB_MIGRATION_PG_*"
            )
        active_row = conn.execute(
            """SELECT count(*) FROM pg_stat_activity
                 WHERE datname = %s AND pid <> pg_backend_pid()""",
            (endpoint.dbname,),
        ).fetchone()
        active_connections = int(active_row[0]) if active_row else 0
        if active_connections:
            raise DatabaseUpgradeError(
                f"源库仍有 {active_connections} 个连接，停写屏障未成立"
            )
        if database_exists(conn, target):
            if not replace_existing:
                raise DatabaseUpgradeError(
                    f"目标数据库已存在，拒绝覆盖：{target}；确认可删除后使用 --replace-target"
                )
            target_active_row = conn.execute(
                """SELECT count(*) FROM pg_stat_activity
                     WHERE datname = %s AND pid <> pg_backend_pid()""",
                (target,),
            ).fetchone()
            target_connections = int(target_active_row[0]) if target_active_row else 0
            if target_connections:
                raise DatabaseUpgradeError(
                    f"目标数据库 {target} 仍有 {target_connections} 个连接，拒绝删除"
                )
            conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(target)))
        conn.execute(
            sql.SQL("CREATE DATABASE {} WITH TEMPLATE {} OWNER {}").format(
                sql.Identifier(target),
                sql.Identifier(endpoint.dbname),
                sql.Identifier(endpoint.user),
            )
        )
    return endpoint.with_dbname(target)


__all__ = [
    "DatabaseEndpoint",
    "DatabaseUpgradeError",
    "clone_database",
    "create_empty_database",
    "database_exists",
    "load_default_endpoint",
    "migration_admin_endpoint",
    "validate_database_name",
]
