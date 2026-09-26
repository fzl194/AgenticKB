"""Load immutable, checksum-pinned database migration manifests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping

import yaml


class ManifestError(ValueError):
    """The migration manifest is malformed or its files have drifted."""


class AppliedMigrationMismatch(ManifestError):
    """A published migration differs from the version recorded in the database."""


class MigrationMode(str, Enum):
    """Operational safety class for a migration."""

    ONLINE_EXPAND = "online_expand"
    BACKFILL = "backfill"
    OFFLINE_REBASE = "offline_rebase"


@dataclass(frozen=True, slots=True)
class Migration:
    migration_id: str
    path: Path
    checksum: str
    mode: MigrationMode = MigrationMode.OFFLINE_REBASE
    reentrant: bool = False


@dataclass(frozen=True, slots=True)
class MigrationManifest:
    schema_version: str
    migrations: tuple[Migration, ...]


def _file_checksum(path: Path) -> str:
    # Git may check out SQL as CRLF on Windows and LF in the Linux container.
    # Migration identity is content-based, not checkout-line-ending based.
    normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def manifest_checksum(manifest: MigrationManifest) -> str:
    payload = "\n".join(
        f"{item.migration_id}:{item.mode.value}:{int(item.reentrant)}:{item.checksum}"
        for item in manifest.migrations
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_manifest(path: Path) -> MigrationManifest:
    """Read and validate one manifest without executing SQL."""

    manifest_path = path.resolve(strict=True)
    root = manifest_path.parent
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ManifestError("manifest 顶层必须是对象")
    schema_version = str(raw.get("schema_version") or "").strip()
    rows = raw.get("migrations")
    if not schema_version or not isinstance(rows, list):
        raise ManifestError("manifest 缺少 schema_version/migrations")

    seen_ids: set[str] = set()
    migrations: list[Migration] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ManifestError(f"migration[{index}] 必须是对象")
        migration_id = str(row.get("id") or "").strip()
        relative_path = str(row.get("path") or "").strip()
        checksum = str(row.get("checksum") or "").strip().lower()
        raw_mode = str(row.get("mode") or MigrationMode.OFFLINE_REBASE.value).strip()
        reentrant = row.get("reentrant", False)
        if not migration_id or migration_id in seen_ids:
            raise ManifestError(f"migration id 缺失或重复：{migration_id!r}")
        seen_ids.add(migration_id)
        if not relative_path or not checksum:
            raise ManifestError(f"{migration_id} 缺少 path/checksum")
        try:
            mode = MigrationMode(raw_mode)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in MigrationMode)
            raise ManifestError(
                f"{migration_id} mode 无效：{raw_mode!r}；允许值：{allowed}"
            ) from exc
        if not isinstance(reentrant, bool):
            raise ManifestError(f"{migration_id} reentrant 必须是布尔值")
        if reentrant and mode is not MigrationMode.BACKFILL:
            raise ManifestError(
                f"{migration_id} 仅 backfill migration 可声明 reentrant=true"
            )
        sql_path = (root / relative_path).resolve(strict=True)
        try:
            sql_path.relative_to(root)
        except ValueError as exc:
            raise ManifestError(f"{migration_id} path 越出 migrations 目录") from exc
        if sql_path.suffix.lower() != ".sql":
            raise ManifestError(f"{migration_id} 不是 SQL 文件")
        actual_checksum = _file_checksum(sql_path)
        if actual_checksum != checksum:
            raise ManifestError(
                f"{migration_id} checksum 不匹配：manifest={checksum}, actual={actual_checksum}"
            )
        migrations.append(Migration(migration_id, sql_path, checksum, mode, reentrant))
    return MigrationManifest(schema_version, tuple(migrations))


def requires_rebase(migrations: Iterable[Migration]) -> bool:
    """Return whether any pending migration requires clone-and-switch."""

    return any(
        item.mode is MigrationMode.OFFLINE_REBASE
        or (item.mode is MigrationMode.BACKFILL and not item.reentrant)
        for item in migrations
    )


def pending_migrations(
    manifest: MigrationManifest,
    applied: Mapping[str, str],
) -> tuple[Migration, ...]:
    """Return missing migrations and reject any applied checksum drift."""

    pending: list[Migration] = []
    for migration in manifest.migrations:
        applied_checksum = applied.get(migration.migration_id)
        if applied_checksum is None:
            pending.append(migration)
        elif applied_checksum != migration.checksum:
            raise AppliedMigrationMismatch(
                f"已执行迁移 {migration.migration_id} checksum 发生变化"
            )
    unknown = sorted(set(applied) - {item.migration_id for item in manifest.migrations})
    if unknown:
        raise AppliedMigrationMismatch("数据库含 manifest 未知迁移：" + ", ".join(unknown))
    return tuple(pending)


__all__ = [
    "AppliedMigrationMismatch",
    "ManifestError",
    "Migration",
    "MigrationManifest",
    "MigrationMode",
    "load_manifest",
    "manifest_checksum",
    "pending_migrations",
    "requires_rebase",
]
