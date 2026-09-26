"""Container-executed database convergence command."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

import psycopg

from .bootstrap import bootstrap_empty_database
from .bridge_51 import (
    apply_51_bridge,
    load_bridge_51_policy,
    preflight_51_bridge,
)
from .config import restore_config_backup, write_converged_config
from .database import (
    DatabaseEndpoint,
    DatabaseUpgradeError,
    clone_database,
    database_exists,
    load_default_endpoint,
    migration_admin_endpoint,
    validate_database_name,
)
from .ledger import (
    acquire_lock,
    ledger_exists,
    load_applied,
    release_lock,
    rollback_online_migration_records,
    schema_complete,
)
from .manifest import load_manifest, manifest_checksum, pending_migrations, requires_rebase
from .runner import apply_manifest
from .validation import (
    compare_retained_table_counts,
    validate_schema,
    validate_supported_rebase_source,
)


PENDING_EXIT_CODE = 10


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _release_version(repo_root: Path) -> str:
    path = repo_root / "releases.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown"
    return str(raw.get("current") or "unknown")


def _default_target(source: str) -> str:
    suffix = "_converged"
    if source.endswith(suffix):
        target = source[: -len(suffix)]
        if not target:
            raise DatabaseUpgradeError("源数据库名不能只有 _converged 后缀")
        return validate_database_name(target)
    if len(source) + len(suffix) > 63:
        raise DatabaseUpgradeError(
            "源数据库名过长，无法构造可逆的 <x>_converged 目标名；请使用 --target-db"
        )
    return validate_database_name(source + suffix)


def _paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    repo_root = Path(args.repo_root).resolve()
    config_dir = Path(args.config_dir).resolve()
    manifest_path = Path(args.manifest).resolve()
    backup_root = Path(args.backup_root).resolve()
    return repo_root, config_dir, manifest_path, backup_root


def _safe_summary(endpoint: DatabaseEndpoint) -> str:
    return f"{endpoint.host}:{endpoint.port}/{endpoint.dbname}"


def _configured_database_exists(endpoint: DatabaseEndpoint) -> bool:
    with psycopg.connect(endpoint.maintenance_conninfo, autocommit=True) as connection:
        return database_exists(connection, endpoint.dbname)


def _rebase_target_exists(endpoint: DatabaseEndpoint, target_dbname: str) -> bool:
    """Validate deployment privileges and report whether the fixed target exists."""

    admin = migration_admin_endpoint(endpoint)
    with psycopg.connect(admin.maintenance_conninfo, autocommit=True) as maintenance:
        privilege = maintenance.execute(
            "SELECT rolsuper OR rolcreatedb FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        if not privilege or not privilege[0]:
            raise DatabaseUpgradeError(
                "迁移账号缺少 CREATEDB；请在部署环境临时注入 CMKB_MIGRATION_PG_*"
            )
        return database_exists(maintenance, validate_database_name(target_dbname))


def _plan(args: argparse.Namespace) -> int:
    _, config_dir, manifest_path, _ = _paths(args)
    endpoint = load_default_endpoint(config_dir)
    manifest = load_manifest(manifest_path)
    if not _configured_database_exists(endpoint):
        print(json.dumps({
            "action": "bootstrap",
            "database": _safe_summary(endpoint),
            "schema_version": manifest.schema_version,
        }, ensure_ascii=False))
        return PENDING_EXIT_CODE
    with psycopg.connect(endpoint.conninfo, autocommit=True) as connection:
        if not ledger_exists(connection):
            target = args.target_db or _default_target(endpoint.dbname)
            validate_supported_rebase_source(connection)
            preflight_report = preflight_51_bridge(
                connection, load_bridge_51_policy(config_dir)
            )
            target_exists = _rebase_target_exists(endpoint, target)
            print(json.dumps({
                "action": "rebase",
                "source": _safe_summary(endpoint),
                "target_db": target,
                "target_exists": target_exists,
                "replace_target_required": target_exists,
                "schema_version": manifest.schema_version,
                "bridge_preflight": preflight_report.to_dict(),
            }, ensure_ascii=False))
            return PENDING_EXIT_CODE
        pending = pending_migrations(manifest, load_applied(connection))
        complete = schema_complete(connection, checksum=manifest_checksum(manifest))
        rebase_required = requires_rebase(pending)
        target_exists = False
        if rebase_required:
            target = args.target_db or _default_target(endpoint.dbname)
            validate_supported_rebase_source(connection)
            target_exists = _rebase_target_exists(endpoint, target)
    if pending or not complete:
        payload = {
            "action": (
                "rebase"
                if rebase_required
                else "online_migrate"
                if pending
                else "complete_marker_recovery"
            ),
            "database": _safe_summary(endpoint),
            "migrations": [item.migration_id for item in pending],
            "migration_modes": [item.mode.value for item in pending],
            "completion_marker_missing": not complete,
        }
        if rebase_required:
            payload.update({
                "target_db": target,
                "target_exists": target_exists,
                "replace_target_required": target_exists,
            })
        print(json.dumps(payload, ensure_ascii=False))
        return PENDING_EXIT_CODE
    print(json.dumps({
        "action": "none",
        "database": _safe_summary(endpoint),
        "schema_version": manifest.schema_version,
    }, ensure_ascii=False))
    return 0


def _target_endpoint(
    source: DatabaseEndpoint,
    target_dbname: str,
    *,
    replace_target: bool,
) -> DatabaseEndpoint:
    admin = migration_admin_endpoint(source)
    target = validate_database_name(target_dbname)
    if replace_target and target != _default_target(source.dbname):
        raise DatabaseUpgradeError(
            "--replace-target 只能删除固定轮换目标数据库，不能与任意 --target-db 组合"
        )
    return clone_database(
        source,
        target,
        maintenance_endpoint=admin,
        replace_existing=replace_target,
    )


def _apply(args: argparse.Namespace) -> int:
    repo_root, config_dir, manifest_path, backup_root = _paths(args)
    source = load_default_endpoint(config_dir)
    manifest = load_manifest(manifest_path)
    app_version = _release_version(repo_root)
    bridge_policy = load_bridge_51_policy(config_dir)

    if not _configured_database_exists(source):
        result = bootstrap_empty_database(
            source,
            repo_root=repo_root,
            manifest=manifest,
            app_version=app_version,
        )
        with psycopg.connect(source.conninfo, autocommit=True) as connection:
            report = validate_schema(connection, manifest)
        print(json.dumps({
            "ok": True,
            "mode": "bootstrap",
            "database": _safe_summary(source),
            "applied": result.applied_ids,
            "schema_version": report.schema_version,
        }, ensure_ascii=False))
        return 0

    with psycopg.connect(source.conninfo, autocommit=True) as source_connection:
        legacy_bridge_required = not ledger_exists(source_connection)
        if not legacy_bridge_required:
            pending = pending_migrations(manifest, load_applied(source_connection))
            if not pending:
                # Recovery window: all SQL committed but the terminal marker was not.
                result = apply_manifest(
                    source_connection,
                    manifest,
                    app_version=app_version,
                    details={"mode": "complete_marker_recovery"},
                    validate_before_complete=lambda: validate_schema(
                        source_connection,
                        manifest,
                        require_completion_marker=False,
                    ),
                )
                validate_schema(source_connection, manifest)
                print(json.dumps({
                    "ok": True,
                    "mode": "complete_marker_recovery",
                    "applied": result.applied_ids,
                    "schema_version": result.schema_version,
                }, ensure_ascii=False))
                return 0
            if not requires_rebase(pending):
                deployment_id = (
                    str(getattr(args, "deployment_id", "") or "").strip()
                    or uuid.uuid4().hex
                )
                result = apply_manifest(
                    source_connection,
                    manifest,
                    app_version=app_version,
                    details={
                        "mode": "online_migrate",
                        "deployment_id": deployment_id,
                    },
                    validate_before_complete=lambda: validate_schema(
                        source_connection,
                        manifest,
                        require_completion_marker=False,
                    ),
                )
                report = validate_schema(source_connection, manifest)
                print(json.dumps({
                    "ok": True,
                    "mode": "online_migrate",
                    "database": _safe_summary(source),
                    "applied": result.applied_ids,
                    "schema_version": report.schema_version,
                    "deployment_id": deployment_id,
                    "config_switched": False,
                }, ensure_ascii=False))
                return 0
        validate_supported_rebase_source(source_connection)
        if legacy_bridge_required:
            preflight_51_bridge(source_connection, bridge_policy)

    target_dbname = args.target_db or _default_target(source.dbname)
    admin = migration_admin_endpoint(source)
    with psycopg.connect(admin.maintenance_conninfo, autocommit=True) as deployment_lock:
        acquire_lock(deployment_lock)
        try:
            refreshed_source = load_default_endpoint(config_dir)
            if refreshed_source.dbname != source.dbname:
                raise DatabaseUpgradeError(
                    "等待迁移锁期间数据库配置已变化，拒绝基于陈旧源库继续"
                )
            target = _target_endpoint(
                source, target_dbname, replace_target=args.replace_target
            )
            with (
                psycopg.connect(source.conninfo, autocommit=True) as source_connection,
                psycopg.connect(target.conninfo, autocommit=True) as target_connection,
            ):
                verified_counts: dict[str, int] = {}
                bridge_report = None
                if legacy_bridge_required:
                    bridge_report = apply_51_bridge(
                        target_connection,
                        repo_root=repo_root,
                        default_domain=bridge_policy.default_domain,
                        allowed_domains=bridge_policy.allowed_domains,
                    )

                def validate_rebase_before_complete() -> None:
                    validate_schema(
                        target_connection,
                        manifest,
                        require_completion_marker=False,
                    )
                    verified_counts.update(
                        compare_retained_table_counts(source_connection, target_connection)
                    )

                result = apply_manifest(
                    target_connection,
                    manifest,
                    app_version=app_version,
                    details={"mode": "rebase", "source_dbname": source.dbname},
                    validate_before_complete=validate_rebase_before_complete,
                    use_lock=False,
                )
                report = validate_schema(target_connection, manifest)

            if not args.no_config_switch:
                write_result = write_converged_config(
                    config_dir=config_dir,
                    backup_root=backup_root,
                    migration_id=manifest.schema_version.replace("/", "_").replace(" ", "_"),
                    source_dbname=source.dbname,
                    target_dbname=target.dbname,
                )
                backup = str(write_result.backup_dir)
            else:
                backup = None
        finally:
            release_lock(deployment_lock)
    print(json.dumps({
        "ok": True,
        "mode": "rebase",
        "source": _safe_summary(source),
        "target": _safe_summary(target),
        "applied": result.applied_ids,
        "schema_version": report.schema_version,
        "verified_tables": len(verified_counts),
        "bridge_51": None if bridge_report is None else {
            "user_domain_rows": bridge_report.user_domain_rows,
            "migrated_keys": bridge_report.migrated_keys,
            "migrated_grants": bridge_report.migrated_grants,
            "fallback_domain_binds": bridge_report.fallback_domain_binds,
            "dropped_grants": list(bridge_report.dropped_grants[:50]),
            "dropped_grants_total": len(bridge_report.dropped_grants),
        },
        "config_backup": backup,
        "config_switched": not args.no_config_switch,
    }, ensure_ascii=False))
    return 0


def _verify(args: argparse.Namespace) -> int:
    _, config_dir, manifest_path, _ = _paths(args)
    endpoint = load_default_endpoint(config_dir)
    manifest = load_manifest(manifest_path)
    with psycopg.connect(endpoint.conninfo, autocommit=True) as connection:
        report = validate_schema(connection, manifest)
    print(json.dumps({
        "ok": True,
        "database": _safe_summary(endpoint),
        "schema_version": report.schema_version,
        "tables": len(report.public_tables),
    }, ensure_ascii=False))
    return 0


def _rollback_config(args: argparse.Namespace) -> int:
    _, config_dir, manifest_path, backup_root = _paths(args)
    manifest = load_manifest(manifest_path)
    migration_id = manifest.schema_version.replace("/", "_").replace(" ", "_")
    backup_dir = backup_root / migration_id
    backups = (
        backup_dir / "database.yaml",
        backup_dir / "domain_registry.yaml",
    )
    if not any(path.exists() for path in backups):
        print(json.dumps({
            "ok": True,
            "mode": "rollback_config_noop",
            "reason": "apply 未创建配置备份，配置尚未切换",
        }, ensure_ascii=False))
        return 0
    result = restore_config_backup(
        config_dir=config_dir,
        backup_root=backup_root,
        migration_id=migration_id,
    )
    print(json.dumps({
        "ok": True,
        "mode": "rollback_config",
        "backup": str(result.backup_dir),
    }, ensure_ascii=False))
    return 0


def _rollback_online_ledger(args: argparse.Namespace) -> int:
    repo_root, config_dir, manifest_path, _ = _paths(args)
    endpoint = load_default_endpoint(config_dir)
    manifest = load_manifest(manifest_path)
    app_version = _release_version(repo_root)
    deployment_id = str(getattr(args, "deployment_id", "") or "").strip()
    if not deployment_id:
        raise DatabaseUpgradeError("rollback-online-ledger 必须提供 --deployment-id")
    with psycopg.connect(endpoint.conninfo, autocommit=True) as connection:
        acquire_lock(connection)
        try:
            deleted = rollback_online_migration_records(
                connection,
                manifest,
                app_version=app_version,
                deployment_id=deployment_id,
            )
        finally:
            release_lock(connection)
    print(json.dumps({
        "ok": True,
        "mode": "rollback_online_ledger",
        "database": _safe_summary(endpoint),
        "deployment_id": deployment_id,
        "deleted": deleted,
        "ddl_reverted": False,
    }, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    repo_root = _repo_root()
    parser = argparse.ArgumentParser(description="AgenticKB versioned database convergence")
    parser.add_argument(
        "command",
        choices=(
            "plan",
            "apply",
            "verify",
            "rollback-config",
            "rollback-online-ledger",
        ),
    )
    parser.add_argument("--repo-root", default=str(repo_root))
    parser.add_argument(
        "--config-dir",
        default=os.getenv(
            "MAIN_CONTROL_CONFIG_DIR", str(repo_root / "main_control_service" / "config")
        ),
    )
    parser.add_argument(
        "--manifest", default=str(repo_root / "databases" / "migrations" / "manifest.yaml")
    )
    parser.add_argument(
        "--backup-root", default="/app/data/database-migration-backups"
    )
    parser.add_argument("--target-db")
    parser.add_argument("--deployment-id")
    parser.add_argument(
        "--replace-target",
        action="store_true",
        help="显式确认删除已存在的轮换目标库后重新克隆",
    )
    parser.add_argument("--no-config-switch", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            return _plan(args)
        if args.command == "apply":
            return _apply(args)
        if args.command == "verify":
            return _verify(args)
        if args.command == "rollback-config":
            return _rollback_config(args)
        return _rollback_online_ledger(args)
    except Exception as exc:  # noqa: BLE001 - operator CLI must fail closed without DSN traceback
        print(f"[ERROR] database upgrade failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
