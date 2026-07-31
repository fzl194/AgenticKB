"""Demo runner: incremental mining pipeline.

Usage:
    python knowledge_mining/demo_run.py

Incremental mode: only processes new/changed documents, carries forward
existing snapshots via assemble_build's incremental merge.

To do a full reset (drop & recreate tables), uncomment the
recreate_all_tables() call in main().

All config comes from .env via MiningConfig / MiningDbConfig.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("demo_run")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "knowledge_base" / "SMF会话管理功能"  # iteration-1: multi-doc test

# ── Full-reset helpers (commented out in main, kept for manual use) ──────
#
# Tables to drop (reverse order — children first for FK constraints)
_ASSET_TABLES = [
    "asset_retrieval_embeddings",
    "asset_retrieval_units",
    "asset_raw_segment_relations",
    "asset_raw_segments",
    "asset_build_document_snapshots",
    "asset_publish_releases",
    "asset_builds",
    "asset_document_snapshot_links",
    "asset_document_snapshots",
    "asset_documents",
    "asset_source_batches",
]
_RUNTIME_TABLES = [
    "mining_run_stage_events",
    "mining_run_documents",
    "mining_runs",
]

# Functions & triggers that must be dropped before their tables
_ASSET_FUNCTIONS = [
    ("trg_populate_embedding_vector", "populate_embedding_vector_vec"),
    ("trg_asset_retrieval_units_search_vector", "asset_retrieval_units_search_vector_update"),
]

_SCHEMA_DIR = REPO_ROOT / "databases"
_ASSET_PG_SCHEMA = _SCHEMA_DIR / "asset_core" / "schemas" / "002_asset_core_postgresql.sql"
_RUNTIME_PG_SCHEMA = _SCHEMA_DIR / "mining_runtime" / "schemas" / "002_mining_runtime_postgresql.sql"


def _drop_all(conn) -> None:
    """Drop all mining-related tables, triggers, and functions."""
    for trig_name, func_name in _ASSET_FUNCTIONS:
        conn.execute(f"DROP TRIGGER IF EXISTS {trig_name} ON asset_retrieval_embeddings")
        conn.execute(f"DROP TRIGGER IF EXISTS {trig_name} ON asset_retrieval_units")
        conn.execute(f"DROP FUNCTION IF EXISTS {func_name} CASCADE")
    for table in _ASSET_TABLES + _RUNTIME_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    logger.info("  Dropped all existing tables, triggers, functions.")


def _apply_schema(conn, sql_path: Path, label: str) -> None:
    """Execute a SQL schema file against the current connection."""
    sql = sql_path.read_text(encoding="utf-8")
    conn.execute(sql)
    logger.info("  Applied schema: %s (%s)", label, sql_path.name)


def recreate_all_tables(cfg) -> None:
    """Drop and recreate all PG tables from schema files.

    Call this for a full reset before the first run, or to start fresh.
    """
    import psycopg

    conninfo = cfg.conninfo
    logger.info("Recreating all PG tables from schema files...")

    with psycopg.connect(conninfo, autocommit=True) as conn:
        _drop_all(conn)
        _apply_schema(conn, _ASSET_PG_SCHEMA, "asset_core")
        _apply_schema(conn, _RUNTIME_PG_SCHEMA, "mining_runtime")

    logger.info("All tables recreated.")


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig
    from knowledge_mining.mining.infra.mining_config import MiningConfig
    from knowledge_mining.mining.jobs.run import run

    db_cfg = MiningDbConfig()
    mining_cfg = MiningConfig()

    # Full reset: uncomment the line below to drop & recreate all tables
    # recreate_all_tables(db_cfg)

    from knowledge_mining.mining.infra.domain_pack import get_default_domain
    domain = get_default_domain()  # 来自 domain_registry.yaml
    llm_url = mining_cfg.llm_service_url

    # Run mining pipeline (incremental)
    logger.info("Starting mining run...")
    logger.info("  domain:           %s", domain)
    logger.info("  input_path:       %s", DATA_DIR)
    logger.info("  llm_base_url:     %s", llm_url)

    t0 = time.perf_counter()
    result = run(
        input_path=DATA_DIR,
        domain=domain,
        publish_on_partial_failure=True,
    )
    elapsed = time.perf_counter() - t0

    # Print summary
    print("\n" + "=" * 60)
    print("MINING RUN COMPLETE")
    print("=" * 60)
    print(f"  Run ID:            {result['run_id']}")
    print(f"  Status:            {result['status']}")
    print(f"  Total documents:   {result['total_documents']}")
    print(f"  Committed:         {result['committed_count']}")
    print(f"  New:               {result['new_count']}")
    print(f"  Updated:           {result['updated_count']}")
    print(f"  Failed:            {result['failed_count']}")
    print(f"  Skipped:           {result['skipped_count']}")
    print(f"  Build ID:          {result['build_id']}")
    print(f"  Release ID:        {result['release_id']}")
    print(f"  Elapsed:           {elapsed:.1f}s")
    print("=" * 60)

    if result["status"] != "completed" or result["failed_count"] > 0:
        logger.warning("Run completed with failures. Check mining_run_stage_events for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
