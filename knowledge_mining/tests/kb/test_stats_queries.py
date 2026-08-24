"""概览页统计的 SQL 语义（KbDB.stats_*）。

路由装配与边界在 test_stats_route.py（用假 KbDB，不需要库）；这里跑真 SQL。
写法与 test_overview_queries.py 一致。

⚠️ 需要 PostgreSQL（`_test` 结尾的可丢弃库），与 tests/kb 下其余用例一致。

这里最要紧的一组断言是**「当前知识」口径**：资产计数必须按「每个文档最近一次进入
validated/published build 的快照」算，而不是对 asset_* 表直接 COUNT。重挖会产生新快照，
累计计数会把挖了 3 遍的 10 篇文档报成 30 篇的知识量——图表上看不出来，但数字是错的。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from knowledge_mining.mining.kb.db import KbDB

pytestmark = pytest.mark.asyncio

DOMAIN = "cloud_core_network"
T0 = "2026-01-01T00:00:00+00:00"


def _uid() -> str:
    return uuid.uuid4().hex


async def _add_doc(db, kb, name):
    return await db.insert_document_identity(
        domain=DOMAIN, kb_id=kb["id"], document_key=f"doc:/{name}", document_name=name,
        storage_path=f"/tmp/{kb['id']}/{name}", directory_path="",
    )


async def _insert_run(
    pool, *, run_id, kb_id, status="completed", started_at=T0, committed=1,
):
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO mining_runs
               (id, kb_id, input_path, domain, channel, status, current_stage, started_at,
                finished_at, execution_engine, workflow_manifest_json, metadata_json,
                total_documents, new_count, updated_count, skipped_count,
                failed_count, committed_count)
               VALUES (%s, %s, %s, %s, 'prod', %s, 'done', %s, %s,
                       'workflow', '{}'::jsonb, '{}'::jsonb, 1, 1, 0, 0, 0, %s)""",
            (run_id, kb_id, f"/tmp/{kb_id}", DOMAIN, status, started_at, started_at,
             committed),
        )


async def _insert_run_document(pool, *, rd_id, run_id, document_key, status):
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO mining_run_documents
               (id, run_id, document_key, raw_content_hash, normalized_content_hash,
                action, status, started_at, finished_at)
               VALUES (%s, %s, %s, 'h', 'h', 'NEW', %s, %s, %s)""",
            (rd_id, run_id, document_key, status, T0, T0),
        )


async def _build_snapshot(
    pool, *, kb_id, document_id, build_status="validated", created_at=T0,
    selection_status="active", segments=0, units=(), mentions=0, relations=0,
):
    """造一份 build + 文档快照，并按需填充 segments / units / mentions / relations。

    返回 (build_id, snapshot_id)。units 传 unit_type 列表，好让类型分布也能断言。
    """
    # mentions/relations 都要挂在具体切片上（外键）。少了这句，忘记给 segments 的调用方
    # 会撞进 seg_ids 的 ZeroDivisionError，看半天才发现是自己漏传了参数。
    assert not (mentions or relations) or segments > 0, "mentions/relations 需要 segments > 0"
    build_id, snap_id = _uid(), _uid()
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO asset_document_snapshots
               (id, domain, normalized_content_hash, raw_content_hash, mime_type,
                created_at)
               VALUES (%s, %s, %s, %s, 'text/markdown', %s)""",
            (snap_id, DOMAIN, _uid(), _uid(), created_at),
        )
        await conn.execute(
            """INSERT INTO asset_builds
               (id, build_code, status, build_mode, domain, kb_id, created_at)
               VALUES (%s, %s, %s, 'full', %s, %s, %s)""",
            (build_id, f"build-{build_id[:8]}", build_status, DOMAIN, kb_id, created_at),
        )
        await conn.execute(
            """INSERT INTO asset_build_document_snapshots
               (build_id, document_id, document_snapshot_id, selection_status, reason)
               VALUES (%s, %s, %s, %s, 'add')""",
            (build_id, document_id, snap_id, selection_status),
        )

        seg_ids = []
        for i in range(segments):
            seg_id = _uid()
            seg_ids.append(seg_id)
            await conn.execute(
                """INSERT INTO asset_raw_segments
                   (id, document_snapshot_id, segment_key, segment_index, raw_text,
                    normalized_text, content_hash, normalized_hash)
                   VALUES (%s, %s, %s, %s, 't', 't', %s, %s)""",
                (seg_id, snap_id, f"seg-{i}", i, _uid(), _uid()),
            )
        for i, unit_type in enumerate(units):
            await conn.execute(
                """INSERT INTO asset_retrieval_units
                   (id, document_snapshot_id, unit_key, unit_type, target_type,
                    text, search_text, created_at)
                   VALUES (%s, %s, %s, %s, 'raw_segment', 't', 't', %s)""",
                (_uid(), snap_id, f"unit-{i}", unit_type, created_at),
            )
        for i in range(mentions):
            await conn.execute(
                """INSERT INTO asset_segment_entity_mentions
                   (id, document_snapshot_id, segment_id, node_type, mention_text)
                   VALUES (%s, %s, %s, 'device', 'x')""",
                (_uid(), snap_id, seg_ids[i % len(seg_ids)]),
            )
        for i in range(relations):
            await conn.execute(
                """INSERT INTO asset_raw_segment_relations
                   (id, document_snapshot_id, source_segment_id, target_segment_id,
                    relation_type)
                   VALUES (%s, %s, %s, %s, 'next')""",
                (_uid(), snap_id, seg_ids[0], seg_ids[(i + 1) % len(seg_ids)]),
            )
    return build_id, snap_id


# ── stats_document_status ───────────────────────────────────────────────────

async def test_document_status_six_keys_always_present(async_pool):
    """六个键恒存在——调用方（路由与前端）不必判 undefined。"""
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="K", owner_id=owner["id"])

    counts = await db.stats_document_status(kb_ids=[kb["id"]])
    assert set(counts) == {
        "uploaded", "mining", "mined", "published", "withdrawn", "failed"
    }
    assert all(v == 0 for v in counts.values())


async def test_document_status_buckets_by_run_state(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="K", owner_id=owner["id"])
    for n in ("a.md", "b.md", "c.md", "d.md"):
        await _add_doc(db, kb, n)

    await _insert_run(async_pool, run_id="run-1", kb_id=kb["id"])
    await _insert_run_document(
        async_pool, rd_id="rd-a", run_id="run-1", document_key="doc:/a.md",
        status="failed")
    await _insert_run_document(
        async_pool, rd_id="rd-b", run_id="run-1", document_key="doc:/b.md",
        status="processing")
    await _insert_run_document(
        async_pool, rd_id="rd-c", run_id="run-1", document_key="doc:/c.md",
        status="committed")
    # d.md 没有 run 记录 → uploaded

    counts = await db.stats_document_status(kb_ids=[kb["id"]])
    assert counts["failed"] == 1
    assert counts["mining"] == 1
    assert counts["mined"] == 1
    assert counts["uploaded"] == 1


async def test_document_status_without_release_never_reports_published(async_pool):
    """with_release=False 时那两档恒 0 —— 这是「口径不适用」，不是数值为零。"""
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="K", owner_id=owner["id"])
    await _add_doc(db, kb, "a.md")

    counts = await db.stats_document_status(kb_ids=[kb["id"]], with_release=False)
    assert counts["published"] == 0
    assert counts["withdrawn"] == 0


async def test_document_status_is_bounded_by_kb_ids(async_pool):
    """别的库的文档不得计进来——这是端点的越权边界。"""
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    mine = await db.create_kb(domain=DOMAIN, name="mine", owner_id=owner["id"])
    theirs = await db.create_kb(domain=DOMAIN, name="theirs", owner_id=owner["id"])
    await _add_doc(db, mine, "a.md")
    await _add_doc(db, theirs, "b.md")
    await _add_doc(db, theirs, "c.md")

    counts = await db.stats_document_status(kb_ids=[mine["id"]])
    assert sum(counts.values()) == 1


async def test_document_status_empty_input_short_circuits(async_pool):
    """空可见集不查库——kb_id = ANY('{}') 在 psycopg 里推断不出元素类型会直接报错。"""
    db = KbDB(async_pool)
    counts = await db.stats_document_status(kb_ids=[])
    assert sum(counts.values()) == 0
    assert len(counts) == 6


# ── stats_assets ────────────────────────────────────────────────────────────

async def test_assets_count_current_snapshot_only(async_pool):
    """**本文件最要紧的一条**：重挖过的文档只算最新那份快照，不是累加。

    同一篇文档挖两遍会有两份快照，各 3 个切片。累计 COUNT 会报 6；正确答案是 3。
    """
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="K", owner_id=owner["id"])
    doc = await _add_doc(db, kb, "a.md")

    await _build_snapshot(
        async_pool, kb_id=kb["id"], document_id=doc["id"],
        created_at="2026-01-01T00:00:00+00:00", segments=3, units=("raw_text",))
    await _build_snapshot(
        async_pool, kb_id=kb["id"], document_id=doc["id"],
        created_at="2026-06-01T00:00:00+00:00", segments=3, units=("raw_text",))

    assets = await db.stats_assets(kb_ids=[kb["id"]])
    assert assets["snapshots"] == 1
    assert assets["segments"] == 3
    assert assets["retrieval_units"] == 1


async def test_assets_count_all_four_kinds(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="K", owner_id=owner["id"])
    doc = await _add_doc(db, kb, "a.md")

    await _build_snapshot(
        async_pool, kb_id=kb["id"], document_id=doc["id"],
        segments=4, units=("raw_text", "summary"), mentions=3, relations=2)

    assets = await db.stats_assets(kb_ids=[kb["id"]])
    assert assets == {
        "snapshots": 1, "segments": 4, "retrieval_units": 2,
        "entity_mentions": 3, "relations": 2,
    }


async def test_assets_exclude_withdrawn_and_unvalidated_builds(async_pool):
    """selection_status='removed'（撤回）与 status='building'（没验完）都不算当前知识。"""
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="K", owner_id=owner["id"])
    removed_doc = await _add_doc(db, kb, "removed.md")
    building_doc = await _add_doc(db, kb, "building.md")

    await _build_snapshot(
        async_pool, kb_id=kb["id"], document_id=removed_doc["id"],
        selection_status="removed", segments=5)
    await _build_snapshot(
        async_pool, kb_id=kb["id"], document_id=building_doc["id"],
        build_status="building", segments=5)

    assets = await db.stats_assets(kb_ids=[kb["id"]])
    assert assets["snapshots"] == 0
    assert assets["segments"] == 0


async def test_assets_are_bounded_by_kb_ids(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    mine = await db.create_kb(domain=DOMAIN, name="mine", owner_id=owner["id"])
    theirs = await db.create_kb(domain=DOMAIN, name="theirs", owner_id=owner["id"])
    my_doc = await _add_doc(db, mine, "a.md")
    their_doc = await _add_doc(db, theirs, "b.md")

    await _build_snapshot(async_pool, kb_id=mine["id"], document_id=my_doc["id"], segments=2)
    await _build_snapshot(
        async_pool, kb_id=theirs["id"], document_id=their_doc["id"], segments=9)

    assert (await db.stats_assets(kb_ids=[mine["id"]]))["segments"] == 2


async def test_assets_empty_input_short_circuits(async_pool):
    db = KbDB(async_pool)
    assert await db.stats_assets(kb_ids=[]) == {
        "snapshots": 0, "segments": 0, "retrieval_units": 0,
        "entity_mentions": 0, "relations": 0,
    }


# ── stats_retrieval_unit_types ──────────────────────────────────────────────

async def test_unit_types_grouped_and_scoped_to_current_snapshot(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="K", owner_id=owner["id"])
    doc = await _add_doc(db, kb, "a.md")

    await _build_snapshot(
        async_pool, kb_id=kb["id"], document_id=doc["id"], segments=1,
        units=("raw_text", "raw_text", "summary"))

    assert await db.stats_retrieval_unit_types(kb_ids=[kb["id"]]) == {
        "raw_text": 2, "summary": 1,
    }


async def test_unit_types_empty_input_short_circuits(async_pool):
    db = KbDB(async_pool)
    assert await db.stats_retrieval_unit_types(kb_ids=[]) == {}


# ── stats_mining_trend ──────────────────────────────────────────────────────

async def test_trend_fills_empty_days_with_zeros(async_pool):
    """空天必须补零。折线图跳过没有数据的日期，会把「三周没挖」画成持续产出。"""
    db = KbDB(async_pool)
    trend = await db.stats_mining_trend(kb_ids=[], days=7)

    assert len(trend) == 7
    assert all(p["runs"] == 0 and p["documents"] == 0 for p in trend)
    # 升序，且末位是今天
    assert [p["date"] for p in trend] == sorted(p["date"] for p in trend)
    assert trend[-1]["date"] == datetime.now(timezone.utc).date().isoformat()


async def test_trend_counts_runs_and_committed_documents(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="K", owner_id=owner["id"])

    today = datetime.now(timezone.utc).date()
    stamp = f"{today.isoformat()}T03:00:00+00:00"
    await _insert_run(
        async_pool, run_id="r1", kb_id=kb["id"], started_at=stamp, committed=4)
    await _insert_run(
        async_pool, run_id="r2", kb_id=kb["id"], started_at=stamp,
        status="failed", committed=1)

    trend = await db.stats_mining_trend(kb_ids=[kb["id"]], days=7)
    point = next(p for p in trend if p["date"] == today.isoformat())

    assert point["runs"] == 2
    assert point["completed"] == 1      # 只有 r1 是 completed
    assert point["documents"] == 5


async def test_trend_ignores_runs_outside_the_window(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="K", owner_id=owner["id"])

    old = (datetime.now(timezone.utc).date() - timedelta(days=90)).isoformat()
    await _insert_run(
        async_pool, run_id="ancient", kb_id=kb["id"],
        started_at=f"{old}T00:00:00+00:00", committed=99)

    trend = await db.stats_mining_trend(kb_ids=[kb["id"]], days=7)
    assert sum(p["documents"] for p in trend) == 0


async def test_trend_is_bounded_by_kb_ids(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    mine = await db.create_kb(domain=DOMAIN, name="mine", owner_id=owner["id"])
    theirs = await db.create_kb(domain=DOMAIN, name="theirs", owner_id=owner["id"])

    stamp = f"{datetime.now(timezone.utc).date().isoformat()}T03:00:00+00:00"
    await _insert_run(
        async_pool, run_id="r-theirs", kb_id=theirs["id"], started_at=stamp, committed=7)

    trend = await db.stats_mining_trend(kb_ids=[mine["id"]], days=7)
    assert sum(p["documents"] for p in trend) == 0
