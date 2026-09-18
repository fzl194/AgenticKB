"""Load immutable, checksum-pinned database migration manifests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml


class ManifestError(ValueError):
    """The migration manifest is malformed or its files have drifted."""


class AppliedMigrationMismatch(ManifestError):
    """A published migration differs from the version recorded in the database."""


@dataclass(frozen=True, slots=True)
class Migration:
    migration_id: str
    path: Path
    checksum: str


@dataclass(frozen=True, slots=True)
class MigrationManifest:
    schema_version: str
    migrations: tuple[Migration, ...]


def _file_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_checksum(manifest: MigrationManifest) -> str:
    payload = "\n".join(
        f"{item.migration_id}:{item.checksum}" for item in manifest.migrations
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
        if not migration_id or migration_id in seen_ids:
            raise ManifestError(f"migration id 缺失或重复：{migration_id!r}")
        seen_ids.add(migration_id)
        if not relative_path or not checksum:
            raise ManifestError(f"{migration_id} 缺少 path/checksum")
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
        migrations.append(Migration(migration_id, sql_path, checksum))
    return MigrationManifest(schema_version, tuple(migrations))


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
    "load_manifest",
    "manifest_checksum",
    "pending_migrations",
]
