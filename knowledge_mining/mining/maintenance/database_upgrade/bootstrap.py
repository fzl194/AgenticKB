"""Explicit empty-database bootstrap used by deployment, never service startup."""

from __future__ import annotations

from pathlib import Path

import psycopg

from knowledge_mining.mining.infra.pg_config import MiningDbConfig
from knowledge_mining.mining.infra.pg_schema import _execute_ddl, ensure_primary_schema
from llm_service.pg_config import LlmDbConfig
from llm_service.pg_schema import ensure_schema as ensure_llm_schema

from .database import (
    DatabaseEndpoint,
    create_empty_database,
    migration_admin_endpoint,
)
from .manifest import MigrationManifest
from .runner import MigrationRunResult, apply_manifest
from .validation import validate_schema


JAVA_BOOTSTRAP_SQL = (
    "agent_serving_java/src/main/resources/db/serving/001_serving_query_logs.sql",
    "agent_serving_java/src/main/resources/db/serving/013_user_domains.sql",
    "agent_serving_java/src/main/resources/db/operator/001_operator_paradigm.sql",
    "agent_serving_java/src/main/resources/db/operator/003_paradigm_domain_binding_retirement.sql",
)


def bootstrap_empty_database(
    endpoint: DatabaseEndpoint,
    *,
    repo_root: Path,
    manifest: MigrationManifest,
    app_version: str,
) -> MigrationRunResult:
    """Create the current schema explicitly before any business service starts."""

    create_empty_database(
        endpoint,
        maintenance_endpoint=migration_admin_endpoint(endpoint),
    )
    mining_config = MiningDbConfig(
        pg_host=endpoint.host,
        pg_port=endpoint.port,
        pg_dbname=endpoint.dbname,
        pg_user=endpoint.user,
        pg_password=endpoint.password,
        pg_sslmode=endpoint.sslmode,
        pg_gssencmode=endpoint.gssencmode,
        pg_pool_min=1,
        pg_pool_max=2,
    )
    ensure_primary_schema(mining_config)
    ensure_llm_schema(
        LlmDbConfig(
            host=endpoint.host,
            port=endpoint.port,
            dbname=endpoint.dbname,
            user=endpoint.user,
            password=endpoint.password,
            sslmode=endpoint.sslmode,
            gssencmode=endpoint.gssencmode,
            pool_min=1,
            pool_max=2,
        )
    )
    with psycopg.connect(endpoint.conninfo, autocommit=True) as connection:
        for relative_path in JAVA_BOOTSTRAP_SQL:
            ddl = (repo_root / relative_path).read_text(encoding="utf-8")
            _execute_ddl(connection, ddl)
        return apply_manifest(
            connection,
            manifest,
            app_version=app_version,
            details={"mode": "bootstrap"},
            validate_before_complete=lambda: validate_schema(
                connection, manifest, require_completion_marker=False
            ),
        )


__all__ = ["JAVA_BOOTSTRAP_SQL", "bootstrap_empty_database"]
