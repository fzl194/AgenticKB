"""清空 CoreMasterKB PostgreSQL 业务数据，保留当前数据库结构。

默认只预览目标表：
    python reset_db.py

确认无误后执行：
    python reset_db.py --execute

连接配置读取 ``main_control_service/config/system/database.yaml``。脚本不操作
MinIO、Docker volumes 或主控 SQLite；服务重启后由各服务的启动自愈逻辑补齐
schema，并重新播种 admin、系统工作流等基础数据。
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = (
    REPO_ROOT / "main_control_service" / "config" / "system" / "database.yaml"
)
DEFAULT_DOMAIN_REGISTRY_PATH = (
    REPO_ROOT / "main_control_service" / "config" / "domain_registry.yaml"
)
DEFAULT_AUTH_CONFIG_PATH = (
    REPO_ROOT / "main_control_service" / "config" / "system" / "auth.yaml"
)

_MANAGED_TABLE_NAMES = frozenset(
    {
        "agent_llm_attempts",
        "agent_llm_events",
        "agent_llm_model_calls",
        "agent_llm_prompt_templates",
        "agent_llm_requests",
        "agent_llm_results",
        "agent_llm_tasks",
        "asset_build_document_snapshots",
        "asset_builds",
        "asset_document_snapshot_links",
        "asset_document_snapshots",
        "asset_documents",
        "asset_file_audit_events",
        "asset_parse_run_attempts",
        "asset_parse_runs",
        "asset_publish_releases",
        "asset_raw_segment_relations",
        "asset_raw_segments",
        # 历史影子迁移残表：现役代码零引用，但老库里有，清业务数据应一并清空。
        "asset_raw_segments_staging",
        "asset_retrieval_embeddings",
        "asset_retrieval_embeddings_v2",
        "asset_retrieval_embeddings_v2_staging",
        "asset_retrieval_units",
        "asset_retrieval_units_v2",
        "asset_retrieval_units_v2_staging",
        "asset_segment_element_links",
        "asset_segment_entity_mentions",
        "asset_snapshot_readiness",
        "asset_snapshot_readiness_staging",
        "asset_source_batches",
        "asset_storage_object_refs",
        "asset_storage_objects",
        "asset_storage_operations",
        "asset_storage_quotas",
        "asset_structure_edges",
        "asset_structure_edges_staging",
        "asset_structure_nodes",
        "asset_structure_nodes_staging",
        "asset_structured_assets",
        "asset_structured_assets_staging",
        "asset_table_cells",
        "asset_table_cells_staging",
        "asset_upload_sessions",
        "kb_folders",
        "kb_members",
        "kb_users",
        "knowledge_bases",
        "mcp_access",
        "mcp_open_kbs",
        "mining_run_documents",
        "mining_run_stage_events",
        "mining_runs",
        "mining_workflow_node_events",
        "mining_workflow_versions",
        "mining_workflows",
        "ontology_alias_dictionary",
        "ontology_candidates",
        "ontology_entities",
        "ontology_entity_relations",
        "ontology_evidence_nodes",
        "ontology_node_types",
        "ontology_relation_types",
        "ontology_versions",
        "operator_paradigm",
        "operator_paradigm_version",
        "serving_query_cache",
        "serving_query_logs",
    }
)

_DISCOVER_TABLES_SQL = """
SELECT n.nspname, c.relname
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p')
  AND NOT c.relispartition
ORDER BY n.nspname, c.relname
"""

_OTHER_SESSIONS_SQL = """
SELECT pid,
       COALESCE(usename, '<unknown>'),
       COALESCE(application_name, '<unnamed>'),
       COALESCE(client_addr::text, 'local'),
       COALESCE(state, '<unknown>')
FROM pg_catalog.pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND backend_type = 'client backend'
ORDER BY pid
"""


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str = field(default="", repr=False)
    sslmode: str = "disable"
    gssencmode: str = "disable"

    @property
    def target_id(self) -> str:
        return f"{self.host}:{self.port}/{self.dbname}"

    def connection_kwargs(self) -> dict[str, Any]:
        """Return a new psycopg kwargs mapping without logging credentials."""
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
            "sslmode": self.sslmode,
            "gssencmode": self.gssencmode,
        }


def _database_config_from_mapping(data: dict[str, Any]) -> DatabaseConfig:
    required = ("host", "dbname", "user")
    missing = [name for name in required if not str(data.get(name, "")).strip()]
    if missing:
        raise ValueError(f"database config missing required fields: {', '.join(missing)}")

    try:
        port = int(data.get("port", 5432))
    except (TypeError, ValueError) as exc:
        raise ValueError("database config port must be an integer") from exc

    return DatabaseConfig(
        host=str(data["host"]).strip(),
        port=port,
        dbname=str(data["dbname"]).strip(),
        user=str(data["user"]).strip(),
        password=str(data.get("password", "")),
        sslmode=str(data.get("sslmode", "disable")),
        gssencmode=str(data.get("gssencmode", "disable")),
    )


def load_database_config(path: str | Path = DEFAULT_CONFIG_PATH) -> DatabaseConfig:
    """Load the default PostgreSQL target from the control-plane YAML."""
    config_path = Path(path)
    if not config_path.is_file():
        raise ValueError(f"database config not found: {config_path}")

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - production dependency guard
        raise RuntimeError("PyYAML is required to read database.yaml") from exc

    parsed = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"invalid database config: {config_path}")
    if str(parsed.get("driver", "postgresql")).lower() != "postgresql":
        raise ValueError("reset_db only supports driver=postgresql")

    default = parsed.get("default")
    if not isinstance(default, dict):
        raise ValueError("database config missing mapping: default")
    return _database_config_from_mapping(default)


def load_database_targets(
    database_path: str | Path = DEFAULT_CONFIG_PATH,
    domain_registry_path: str | Path = DEFAULT_DOMAIN_REGISTRY_PATH,
    *,
    default_only: bool = False,
) -> list[DatabaseConfig]:
    """Load and physically deduplicate default plus inline domain databases."""
    default = load_database_config(database_path)
    targets: dict[tuple[str, int, str], DatabaseConfig] = {
        (default.host.lower(), default.port, default.dbname): default
    }

    if default_only:
        return list(targets.values())

    registry_path = Path(domain_registry_path)
    if not registry_path.is_file():
        raise ValueError(f"domain registry not found: {registry_path}")

    import yaml

    parsed = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    domains = parsed.get("domains", {}) if isinstance(parsed, dict) else {}
    if not isinstance(domains, dict):
        raise ValueError("domain registry field 'domains' must be a mapping")

    for domain_name, entry in domains.items():
        if not isinstance(entry, dict):
            raise ValueError(f"domain {domain_name!r} must be a mapping")
        inline = entry.get("database")
        if inline is None:
            continue
        if not isinstance(inline, dict):
            raise ValueError(f"domain {domain_name!r} database must be a mapping")
        config = _database_config_from_mapping(inline)
        identity = (config.host.lower(), config.port, config.dbname)
        targets.setdefault(identity, config)

    return list(targets.values())


def validate_admin_bootstrap(path: str | Path = DEFAULT_AUTH_CONFIG_PATH) -> None:
    """Ensure one restart can recreate a login-capable admin after truncation."""
    auth_path = Path(path)
    if not auth_path.is_file():
        raise ValueError(f"auth config not found: {auth_path}")

    import yaml

    parsed = yaml.safe_load(auth_path.read_text(encoding="utf-8")) or {}
    bootstrap = parsed.get("bootstrap", {}) if isinstance(parsed, dict) else {}
    password = bootstrap.get("admin_password", "") if isinstance(bootstrap, dict) else ""
    if not isinstance(password, str):
        raise ValueError("auth bootstrap.admin_password must be a string")
    if password in {"", "change-me-on-first-login"}:
        raise ValueError(
            "auth bootstrap.admin_password is empty or a placeholder; "
            "configure it before clearing kb_users"
        )


def quote_identifier(value: str) -> str:
    """Quote one PostgreSQL identifier sourced from the system catalog."""
    return '"' + value.replace('"', '""') + '"'


def build_truncate_statement(tables: Sequence[tuple[str, str]]) -> str | None:
    """Build one atomic TRUNCATE for all managed tables."""
    if not tables:
        return None
    qualified = [
        f"{quote_identifier(schema)}.{quote_identifier(table)}"
        for schema, table in tables
    ]
    return f"TRUNCATE TABLE {', '.join(qualified)} RESTART IDENTITY"


def _is_managed_table(table: str) -> bool:
    return table in _MANAGED_TABLE_NAMES


def discover_public_tables(cursor: Any) -> list[tuple[str, str]]:
    """Return non-partition-child tables from the public schema."""
    cursor.execute(_DISCOVER_TABLES_SQL)
    return [(str(schema), str(table)) for schema, table in cursor.fetchall()]


def assert_no_other_sessions(cursor: Any) -> None:
    """Refuse reset while application pools or other client sessions remain."""
    cursor.execute(_OTHER_SESSIONS_SQL)
    sessions = cursor.fetchall()
    if not sessions:
        return
    details = ", ".join(
        f"pid={pid} user={user} app={app} client={client} state={state}"
        for pid, user, app, client, state in sessions
    )
    raise RuntimeError(
        "other PostgreSQL client sessions are still connected; "
        f"stop CoreMasterKB before reset ({details})"
    )


def validate_managed_tables(tables: Sequence[tuple[str, str]]) -> None:
    """Refuse to clear a database that appears shared with another product."""
    unknown = [table for _schema, table in tables if not _is_managed_table(table)]
    if unknown:
        raise ValueError(
            "refusing to clear unknown public tables: " + ", ".join(sorted(unknown))
        )


def truncate_public_tables(cursor: Any) -> list[str]:
    """Discover and truncate every current CoreMasterKB table atomically."""
    tables = discover_public_tables(cursor)
    validate_managed_tables(tables)
    statement = build_truncate_statement(tables)
    if statement is not None:
        cursor.execute(statement)
    return [f"{schema}.{table}" for schema, table in tables]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="清空 CoreMasterKB PostgreSQL 数据，保留最新表结构。"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="database.yaml 路径",
    )
    parser.add_argument(
        "--domain-registry",
        type=Path,
        default=DEFAULT_DOMAIN_REGISTRY_PATH,
        help="domain_registry.yaml 路径；用于发现并去重域数据库",
    )
    parser.add_argument(
        "--auth-config",
        type=Path,
        default=DEFAULT_AUTH_CONFIG_PATH,
        help="auth.yaml 路径；执行前验证 admin 可在重启时重新播种",
    )
    parser.add_argument(
        "--default-only",
        action="store_true",
        help="只清 database.yaml 的 default；必须显式指定才忽略域数据库",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际执行；省略时只预览目标库和表",
    )
    return parser


def _print_target(config: DatabaseConfig, tables: Sequence[tuple[str, str]]) -> None:
    print(
        f"Target: {config.host}:{config.port}/{config.dbname} "
        f"(user={config.user}, schema=public)"
    )
    print(f"Managed tables: {len(tables)}")
    for schema, table in tables:
        print(f"  - {schema}.{table}")


def _inspect_target(
    psycopg: Any,
    config: DatabaseConfig,
    *,
    require_quiet: bool,
) -> list[tuple[str, str]]:
    with psycopg.connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cursor:
            tables = discover_public_tables(cursor)
            validate_managed_tables(tables)
            if require_quiet:
                assert_no_other_sessions(cursor)
            return tables


def _clear_target(psycopg: Any, config: DatabaseConfig) -> list[str]:
    with psycopg.connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '10s'")
            cursor.execute("SET LOCAL statement_timeout = '120s'")
            assert_no_other_sessions(cursor)
            return truncate_public_tables(cursor)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        targets = load_database_targets(
            args.config,
            args.domain_registry,
            default_only=args.default_only,
        )
        import psycopg

        inspected = [
            (
                config,
                _inspect_target(psycopg, config, require_quiet=args.execute),
            )
            for config in targets
        ]
        for config, tables in inspected:
            _print_target(config, tables)

        if not args.execute:
            print("Dry run only. Re-run with --execute to clear these tables.")
            return 0

        validate_admin_bootstrap(args.auth_config)
        print("WARNING: all rows in the tables above will be permanently deleted.")
        for config, _tables in inspected:
            phrase = f"RESET {config.target_id}"
            confirmation = input(f"Type '{phrase}' to proceed: ")
            if confirmation != phrase:
                print("Aborted before clearing any database.")
                return 0

        total = 0
        completed_targets: list[str] = []
        try:
            for config, _tables in inspected:
                cleared = _clear_target(psycopg, config)
                total += len(cleared)
                completed_targets.append(config.target_id)
                print(f"Cleared {len(cleared)} tables in {config.target_id}.")
        except Exception:
            if completed_targets:
                print(
                    "[ERROR] partial multi-database reset; already cleared: "
                    + ", ".join(completed_targets),
                    file=sys.stderr,
                )
            raise

        print(f"Cleared {total} PostgreSQL tables; schema was preserved.")
        print("MinIO object bytes and Docker volumes were not modified.")
        print("PostgreSQL upload/object metadata was cleared.")
        print("Restart services with: bash deploy-server.sh --apply-config")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except ImportError:
        print("[ERROR] psycopg is required; run this script in the app image.", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary, keep rollback semantics
        print(f"[ERROR] database reset failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
