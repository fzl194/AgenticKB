"""PG-gated smoke tests for the PostgreSQL repositories (M1.2).

These only run when a real disposable PostgreSQL test DB is available
(``KB_RUN_POSTGRES_ACCEPTANCE=1`` + ``*_test`` dbname). Without PG they skip,
so the full file-management suite is green in any environment.

The smoke covers just the StorageObject round-trip + quota optimistic-
concurrency — enough to prove the SQL/DDL alignment is correct. Deep
behavioural coverage lives in the service / memory tests, which share the same
Protocol contract.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

# Reuse the project's PG gate + pool fixture pattern.
pytestmark = pytest.mark.skipif(
    os.environ.get("KB_RUN_POSTGRES_ACCEPTANCE") != "1",
    reason="set KB_RUN_POSTGRES_ACCEPTANCE=1 to run PostgreSQL acceptance",
)


@pytest_asyncio.fixture
async def pg_pool(db_config, _ensure_schema):
    """Async pool over the disposable test DB (mirrors kb/conftest)."""
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        db_config.conninfo,
        min_size=1,
        max_size=2,
        open=False,
        kwargs={"row_factory": dict_row},
    )
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# StorageObjectRepository round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_object_register_get_round_trip(pg_pool):
    from knowledge_mining.mining.file_management.repositories_pg import (
        PgStorageObjectRepository,
    )
    from knowledge_mining.mining.contracts.file_management import StorageObjectRecord

    repo = PgStorageObjectRepository(pg_pool)
    rec = StorageObjectRecord(
        id="pg_smoke_o1", provider="fake", bucket="b", object_key="k",
        object_version_id=None, sha256="h", size=42, mime="text/plain",
        artifact_class="source", state="STAGING",
    )
    await repo.register(rec)
    fetched = await repo.get("pg_smoke_o1")
    assert fetched is not None
    assert fetched.sha256 == "h"
    assert fetched.size == 42
    assert fetched.state == "STAGING"

    # Dedup probe.
    by_loc = await repo.find_by_location("b", "k", None)
    assert by_loc is not None and by_loc.id == "pg_smoke_o1"


# ---------------------------------------------------------------------------
# QuotaRepository optimistic concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quota_reserve_then_commit_round_trip(pg_pool):
    from knowledge_mining.mining.file_management.repositories_pg import (
        PgQuotaRepository,
    )

    repo = PgQuotaRepository(pg_pool)
    # Seed a real limit first (get() creates a zero-limit row by default).
    async with pg_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO asset_storage_quotas (kb_id, limit_bytes, version, updated_at)
               VALUES ('pg_smoke_kb', 1000, 1, now())
               ON CONFLICT (kb_id) DO UPDATE SET limit_bytes = 1000, version = 1""",
        )
    q0 = await repo.get("pg_smoke_kb")
    assert q0.limit_bytes == 1000

    q1 = await repo.reserve("pg_smoke_kb", 300, expected_version=q0.version)
    assert q1.reserved_bytes == 300
    q2 = await repo.commit("pg_smoke_kb", 300, 300, expected_version=q1.version)
    assert q2.reserved_bytes == 0
    assert q2.used_bytes == 300


@pytest.mark.asyncio
async def test_quota_reserve_over_limit_raises(pg_pool):
    from knowledge_mining.mining.contracts.file_management import QuotaExceeded
    from knowledge_mining.mining.file_management.repositories_pg import (
        PgQuotaRepository,
    )

    repo = PgQuotaRepository(pg_pool)
    async with pg_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO asset_storage_quotas (kb_id, limit_bytes, version, updated_at)
               VALUES ('pg_smoke_tiny', 10, 1, now())
               ON CONFLICT (kb_id) DO UPDATE SET limit_bytes = 10, version = 1""",
        )
    q = await repo.get("pg_smoke_tiny")
    with pytest.raises(QuotaExceeded):
        await repo.reserve("pg_smoke_tiny", 100, expected_version=q.version)
