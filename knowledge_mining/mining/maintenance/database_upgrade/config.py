"""Pure configuration convergence logic.

The deployment package never carries production configuration.  This module
transforms the existing in-network documents only after database verification.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping

import yaml


class ConfigConvergenceError(ValueError):
    """Configuration does not describe the supported single-database topology."""


@dataclass(frozen=True, slots=True)
class ConfigWriteResult:
    backup_dir: Path
    database_path: Path
    registry_path: Path


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigConvergenceError(f"{label} 缺失或不是对象")
    return dict(value)


def _physical_signature(
    database: Mapping[str, Any],
) -> tuple[str, int, str, str, str, str, str]:
    host = str(database.get("host") or "").strip().lower()
    dbname = str(database.get("dbname") or "").strip()
    if not host or not dbname:
        raise ConfigConvergenceError("数据库配置缺少 host/dbname")
    try:
        port = int(database.get("port", 5432))
    except (TypeError, ValueError) as exc:
        raise ConfigConvergenceError("数据库 port 非法") from exc
    return (
        host,
        port,
        dbname,
        str(database.get("user") or ""),
        str(database.get("password") or ""),
        str(database.get("sslmode") or "disable"),
        str(database.get("gssencmode") or "disable"),
    )


def converge_database_config(
    *,
    database_document: Mapping[str, Any],
    registry_document: Mapping[str, Any],
    source_dbname: str,
    target_dbname: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return converged copies without mutating the supplied documents.

    Every enabled domain must resolve to the same physical source database.
    Inline database blocks are then removed so the global default is the only
    connection source after cutover.
    """

    source = source_dbname.strip()
    target = target_dbname.strip()
    if not source or not target:
        raise ConfigConvergenceError("源/目标数据库名不能为空")
    if source == target:
        raise ConfigConvergenceError("目标数据库必须与源数据库不同")

    database_copy = deepcopy(dict(database_document))
    registry_copy = deepcopy(dict(registry_document))
    default_database = _require_mapping(database_copy.get("default"), "database.default")
    default_signature = _physical_signature(default_database)
    if default_signature[2] != source:
        raise ConfigConvergenceError(
            f"源数据库不匹配：配置为 {default_signature[2]!r}，请求为 {source!r}"
        )

    domains = _require_mapping(registry_copy.get("domains"), "domain_registry.domains")
    signatures = {default_signature}
    for domain_id, raw_entry in domains.items():
        entry = _require_mapping(raw_entry, f"domain {domain_id}")
        inline = entry.get("database")
        signatures.add(
            default_signature
            if inline is None
            else _physical_signature(_require_mapping(inline, f"domain {domain_id}.database"))
        )
    if len(signatures) != 1:
        locations = sorted(
            f"{signature[0]}:{signature[1]}/{signature[2]}" for signature in signatures
        )
        raise ConfigConvergenceError(
            "检测到多个物理数据库，拒绝自动合并：" + ", ".join(locations)
        )

    default_database["dbname"] = target
    database_copy["default"] = default_database
    converged_domains: dict[str, Any] = {}
    for domain_id, raw_entry in domains.items():
        entry = deepcopy(_require_mapping(raw_entry, f"domain {domain_id}"))
        entry.pop("database", None)
        converged_domains[domain_id] = entry
    registry_copy["domains"] = converged_domains
    return database_copy, registry_copy


def _atomic_write_yaml(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            yaml.safe_dump(
                dict(document),
                handle,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_converged_config(
    *,
    config_dir: Path,
    backup_root: Path,
    migration_id: str,
    source_dbname: str,
    target_dbname: str,
) -> ConfigWriteResult:
    """Back up and atomically converge the two database configuration files."""

    if not re.fullmatch(r"[A-Za-z0-9._-]+", migration_id):
        raise ConfigConvergenceError("migration_id 只能包含字母、数字、点、下划线和短横线")
    database_path = config_dir / "system" / "database.yaml"
    registry_path = config_dir / "domain_registry.yaml"
    for path in (database_path, registry_path):
        if not path.is_file():
            raise ConfigConvergenceError(f"缺少配置文件：{path}")
    database_document = yaml.safe_load(database_path.read_text(encoding="utf-8"))
    registry_document = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    converged_database, converged_registry = converge_database_config(
        database_document=_require_mapping(database_document, "database.yaml"),
        registry_document=_require_mapping(registry_document, "domain_registry.yaml"),
        source_dbname=source_dbname,
        target_dbname=target_dbname,
    )

    backup_dir = backup_root / migration_id
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_dir, 0o700)
    database_backup = backup_dir / "database.yaml"
    registry_backup = backup_dir / "domain_registry.yaml"
    # A failed post-validation config write may be retried. Never overwrite the
    # first known-good pre-cutover backup with a partially switched document.
    if not database_backup.exists():
        shutil.copy2(database_path, database_backup)
    if not registry_backup.exists():
        shutil.copy2(registry_path, registry_backup)
    os.chmod(database_backup, 0o600)
    os.chmod(registry_backup, 0o600)

    try:
        _atomic_write_yaml(database_path, converged_database)
        _atomic_write_yaml(registry_path, converged_registry)
    except Exception:
        shutil.copy2(database_backup, database_path)
        shutil.copy2(registry_backup, registry_path)
        raise
    return ConfigWriteResult(backup_dir, database_path, registry_path)


def restore_config_backup(
    *, config_dir: Path, backup_root: Path, migration_id: str
) -> ConfigWriteResult:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", migration_id):
        raise ConfigConvergenceError("migration_id 非法")
    backup_dir = backup_root / migration_id
    database_backup = backup_dir / "database.yaml"
    registry_backup = backup_dir / "domain_registry.yaml"
    if not database_backup.is_file() or not registry_backup.is_file():
        raise ConfigConvergenceError(f"缺少配置回滚备份：{backup_dir}")
    database_path = config_dir / "system" / "database.yaml"
    registry_path = config_dir / "domain_registry.yaml"
    database_document = _require_mapping(
        yaml.safe_load(database_backup.read_text(encoding="utf-8")), "database backup"
    )
    registry_document = _require_mapping(
        yaml.safe_load(registry_backup.read_text(encoding="utf-8")), "registry backup"
    )
    _atomic_write_yaml(database_path, database_document)
    _atomic_write_yaml(registry_path, registry_document)
    return ConfigWriteResult(backup_dir, database_path, registry_path)


__all__ = [
    "ConfigConvergenceError",
    "ConfigWriteResult",
    "converge_database_config",
    "write_converged_config",
    "restore_config_backup",
]
