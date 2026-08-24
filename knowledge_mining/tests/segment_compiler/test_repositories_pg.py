"""PG-gated smoke tests for the Segment Compiler PG repository (M5/v2).

仅在真实可丢弃 PG 测试库可用时运行（``KB_RUN_POSTGRES_ACCEPTANCE=1`` +
``*_test`` 库名），与 ``tests/shadow_parse/test_repositories_pg.py`` 同惯例。

背景（2026-08-24 生产事故回归）：切片编译器 v2 的章节角色
（definition/enumeration/...）曾违反 002 DDL 的 semantic_role CHECK，
因本层此前**没有任何真库用例**而漏网——编译产物只有经过真约束才算
被验证过。本文件就是那条缺失的防线。
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

pytestmark = pytest.mark.skipif(
    os.environ.get("KB_RUN_POSTGRES_ACCEPTANCE") != "1",
    reason="set KB_RUN_POSTGRES_ACCEPTANCE=1 to run PostgreSQL acceptance",
)

#: v2 编译器会产出的全部语义角色（含旧词表交集值）——逐一过真约束。
_V2_ROLES = (
    "definition", "enumeration", "example", "conclusion", "constraint",
    "navigation", "overview", "unknown",
)


@pytest_asyncio.fixture
async def pg_pool(db_config, _ensure_schema):
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


async def _seed_snapshot(pool, snapshot_id: str) -> None:
    """最小快照行（满足 asset_raw_segments 的 FK）。"""
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO asset_document_snapshots (
                   id, domain, normalized_content_hash, raw_content_hash,
                   mime_type, title, scope_json, tags_json,
                   parser_profile_json, metadata_json, created_at,
                   lifecycle_status
               ) VALUES (%s,'pg_smoke','h','h','text/plain','smoke',
                         '{}'::jsonb,'[]'::jsonb,'{}'::jsonb,'{}'::jsonb,
                         '2026-08-24T00:00:00+00:00','READY')
               ON CONFLICT (id) DO NOTHING""",
            (snapshot_id,),
        )


@pytest.mark.asyncio
async def test_v2_semantic_roles_pass_real_check_constraint(pg_pool):
    """v2 全部角色值可落 asset_raw_segments（012 加宽后的真约束）。"""
    from knowledge_mining.mining.contracts.segment_compiler import (
        CompiledSegment,
    )
    from knowledge_mining.mining.segment_compiler.repositories_pg import (
        PgSegmentStore,
    )

    await _seed_snapshot(pg_pool, "snap_pg_smoke_roles")
    store = PgSegmentStore(pg_pool)
    segments = tuple(
        CompiledSegment(
            segment_index=i,
            block_type="paragraph",
            raw_text=f"{role} 角色切片正文。",
            heading_chain=((1, f"章节-{i}"),),
            element_ids=(f"el-{i}",),
            metadata={},
            token_count=12,
            semantic_role=role,
        )
        for i, role in enumerate(_V2_ROLES)
    )
    count = await store.replace_for_snapshot(
        "snap_pg_smoke_roles", segments, "segc-pg-smoke",
        document_key="pg-smoke.md",
    )
    assert count == len(_V2_ROLES)

    back = await store.list_for_snapshot("snap_pg_smoke_roles")
    assert [s.semantic_role for s in back] == list(_V2_ROLES)


@pytest.mark.asyncio
async def test_replace_for_snapshot_round_trip_and_fingerprint(pg_pool):
    from knowledge_mining.mining.contracts.segment_compiler import (
        CompiledSegment,
    )
    from knowledge_mining.mining.segment_compiler.repositories_pg import (
        PgSegmentStore,
    )

    await _seed_snapshot(pg_pool, "snap_pg_smoke_replace")
    store = PgSegmentStore(pg_pool)
    first = (
        CompiledSegment(
            segment_index=0, block_type="table", raw_text="a\tb\n1\t2",
            heading_chain=((1, "表格节"),),
            element_ids=("t1",),
            metadata={"view": "whole", "table_kind": "relation_table",
                      "rows": 2, "columns": 2, "table_caption": ""},
            token_count=7, semantic_role="definition",
        ),
    )
    await store.replace_for_snapshot(
        "snap_pg_smoke_replace", first, "segc-a",
        document_key="k.md",
    )
    assert await store.compiler_fingerprint("snap_pg_smoke_replace") == "segc-a"

    # 重切替换：整表替换语义（旧行清干净，结构元数据保真）。
    second = (
        CompiledSegment(
            segment_index=0, block_type="paragraph", raw_text="重切后唯一片",
            heading_chain=((1, "表格节"), (2, "小节")),
            element_ids=("p1", "p2"),
            metadata={"split": True},
            token_count=6, semantic_role="conclusion",
        ),
    )
    count = await store.replace_for_snapshot(
        "snap_pg_smoke_replace", second, "segc-b",
        document_key="k.md",
    )
    assert count == 1
    back = await store.list_for_snapshot("snap_pg_smoke_replace")
    assert len(back) == 1
    assert back[0].semantic_role == "conclusion"
    assert back[0].metadata["split"] is True
    assert await store.compiler_fingerprint("snap_pg_smoke_replace") == "segc-b"
