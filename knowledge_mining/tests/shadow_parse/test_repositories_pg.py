"""PG-gated smoke tests for the Shadow Parse PG repository (M2).

仅在真实可丢弃 PG 测试库可用时运行（``KB_RUN_POSTGRES_ACCEPTANCE=1`` +
``*_test`` 库名），与 ``tests/file_management/test_repositories_pg.py`` 同惯例。
无 PG 时整文件 skip，影子解析主套件（memory 实现）在任何环境都保持绿。

Smoke 覆盖：upsert 插入 / 幂等键冲突翻转（FAILED -> SUCCEEDED，id 稳定）/
``find_by_document_hash`` 探针——足以证明 SQL 与 009 DDL 对齐。
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

pytestmark = pytest.mark.skipif(
    os.environ.get("KB_RUN_POSTGRES_ACCEPTANCE") != "1",
    reason="set KB_RUN_POSTGRES_ACCEPTANCE=1 to run PostgreSQL acceptance",
)


@pytest_asyncio.fixture
async def pg_pool(db_config, _ensure_schema):
    """Async pool over the disposable test DB（沿用 kb/conftest 模式）。"""
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


@pytest.mark.asyncio
async def test_parse_run_upsert_round_trip_and_idempotent_flip(pg_pool):
    from knowledge_mining.mining.shadow_parse.contracts import ParseRunRecord
    from knowledge_mining.mining.shadow_parse.repositories_pg import (
        PgParseRunRepository,
    )

    repo = PgParseRunRepository(pg_pool)
    failed = ParseRunRecord(
        id="pg_smoke_pr1",
        document_id="pg_smoke_doc1",
        source_storage_object_id="so_1",
        source_raw_hash="h1",
        source_content_revision=3,
        parser_id="stub-txt",
        parser_fingerprint="stub-fp-v1",
        status="FAILED",
        error_message="RuntimeError: boom",
        started_at="2026-08-13T00:00:00+00:00",
        finished_at="2026-08-13T00:00:01+00:00",
    )
    stored = await repo.upsert(failed)
    assert stored.status == "FAILED"
    assert stored.error_message == "RuntimeError: boom"
    assert stored.metadata_json  # JSONB 默认/写入非空

    # 同幂等键 upsert SUCCEEDED：覆盖原行并保留 id（稳定身份）。
    succeeded = ParseRunRecord(
        id="pg_smoke_pr2",  # 不同 id：冲突时以原行 id 为准
        document_id="pg_smoke_doc1",
        source_storage_object_id="so_1",
        source_raw_hash="h1",
        source_content_revision=3,
        parser_id="stub-txt",
        parser_fingerprint="stub-fp-v1",
        status="SUCCEEDED",
        parse_ir_storage_object_id="so_ir_1",
        parse_ir_schema_version="0.1.0",
        element_count=2,
        container_count=1,
        relation_count=1,
        started_at="2026-08-13T00:02:00+00:00",
        finished_at="2026-08-13T00:02:01+00:00",
    )
    flipped = await repo.upsert(succeeded)
    assert flipped.id == "pg_smoke_pr1"
    assert flipped.status == "SUCCEEDED"
    assert flipped.element_count == 2
    assert flipped.parse_ir_storage_object_id == "so_ir_1"

    # 幂等探针。
    probe = await repo.find_by_document_hash(
        "pg_smoke_doc1", "h1", "stub-fp-v1"
    )
    assert probe is not None
    assert probe.id == "pg_smoke_pr1"
    assert probe.status == "SUCCEEDED"

    # get 直接命中。
    fetched = await repo.get("pg_smoke_pr1")
    assert fetched is not None and fetched.status == "SUCCEEDED"
    assert await repo.get("no_such_run") is None

    # 不同 parser 指纹是另一行（幂等键成员）。
    other = await repo.find_by_document_hash("pg_smoke_doc1", "h1", "other-fp")
    assert other is None
