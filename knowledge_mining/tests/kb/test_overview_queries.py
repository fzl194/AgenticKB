"""概览页聚合的 SQL 语义 + 可见集轻量查询（/api/runs 的身份护栏也用它）。

路由装配与组装逻辑在 test_overview_route.py（用假 KbDB，不需要库）；这里跑真 SQL。

⚠️ 需要 PostgreSQL（`_test` 结尾的可丢弃库），与 tests/kb 下其余用例一致。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.db import KbDB

pytestmark = pytest.mark.asyncio

DOMAIN = "cloud_core_network"
T0 = "2026-01-01T00:00:00+00:00"


async def _insert_run(
    pool, *, run_id, kb_id, status="completed", started_at=T0, finished_at=None,
    domain=DOMAIN, total=1, new=1, updated=0,
):
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO mining_runs
               (id, kb_id, input_path, domain, channel, status, current_stage, started_at,
                finished_at, execution_engine, workflow_manifest_json, metadata_json,
                total_documents, new_count, updated_count, skipped_count,
                failed_count, committed_count)
               VALUES (%s, %s, %s, %s, 'prod', %s, 'done', %s, %s,
                       'workflow', '{}'::jsonb, '{}'::jsonb, %s, %s, %s, 0, 0, 1)""",
            (run_id, kb_id, f"/tmp/{kb_id}", domain, status, started_at, finished_at,
             total, new, updated),
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


async def _add_doc(db, kb, name):
    return await db.insert_document_identity(
        domain=DOMAIN, kb_id=kb["id"], document_key=f"doc:/{name}", document_name=name,
        storage_path=f"/tmp/{kb['id']}/{name}", directory_path="",
    )


# ── list_visible_kb_ids（/api/runs 护栏与概览共用的可见集） ──────────────────

async def test_visible_kb_ids_match_list_visible(async_pool):
    """轻量版必须与 list_visible 给出同一个集合——两者一旦分叉，护栏就会与页面不一致。"""
    db = KbDB(async_pool)
    alice = await db.upsert_user_by_username("alice")
    bob = await db.upsert_user_by_username("bob")
    own = await db.create_kb(domain=DOMAIN, name="own", owner_id=alice["id"])
    pub = await db.create_kb(domain=DOMAIN, name="pub", owner_id=bob["id"], visibility="public")
    member_kb = await db.create_kb(domain=DOMAIN, name="mem", owner_id=bob["id"])
    await db.add_member(kb_id=member_kb["id"], user_id=alice["id"], role="viewer")
    await db.create_kb(domain=DOMAIN, name="hidden", owner_id=bob["id"])  # 不可见

    ids = set(await db.list_visible_kb_ids(user_id=alice["id"], domain=DOMAIN))
    from_full = {k["id"] for k in await db.list_visible(user_id=alice["id"], domain=DOMAIN)}

    assert ids == from_full == {own["id"], pub["id"], member_kb["id"]}


async def test_visible_kb_ids_admin_sees_all_in_domain(async_pool):
    db = KbDB(async_pool)
    admin = await db.upsert_user_by_username("root")
    bob = await db.upsert_user_by_username("bob")
    async with async_pool.connection() as conn:
        await conn.execute(
            "UPDATE kb_users SET site_role = 'admin' WHERE id = %s", [admin["id"]]
        )
    a = await db.create_kb(domain=DOMAIN, name="a", owner_id=bob["id"])
    b = await db.create_kb(domain=DOMAIN, name="b", owner_id=bob["id"])
    # 别的域不算
    await db.create_kb(domain="generic", name="other", owner_id=bob["id"])

    ids = set(await db.list_visible_kb_ids(user_id=admin["id"], domain=DOMAIN))
    assert ids == {a["id"], b["id"]}


# ── overview_status_counts ──────────────────────────────────────────────────

async def test_status_counts_shape_and_zero_fill(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="K", owner_id=owner["id"])
    for n in ("a.md", "b.md", "c.md"):
        await _add_doc(db, kb, n)

    await _insert_run(async_pool, run_id="run-1", kb_id=kb["id"])
    await _insert_run_document(
        async_pool, rd_id="rd-a", run_id="run-1", document_key="doc:/a.md", status="failed")
    await _insert_run_document(
        async_pool, rd_id="rd-b", run_id="run-1", document_key="doc:/b.md",
        status="processing")

    counts = await db.overview_status_counts(kb_ids=[kb["id"]])
    assert counts[kb["id"]] == {"total": 3, "mining": 1, "failed": 1}


async def test_status_counts_empty_input_short_circuits(async_pool):
    """空可见集不查库——kb_id = ANY('{}') 在 psycopg 里推断不出元素类型会直接报错。"""
    db = KbDB(async_pool)
    assert await db.overview_status_counts(kb_ids=[]) == {}
    assert await db.overview_run_rollup(kb_ids=[]) == {}
    assert await db.overview_recent_runs(kb_ids=[]) == []


async def test_status_counts_do_not_bleed_across_kbs(async_pool):
    """状态串味的聚合版：同名文档在另一个库失败，不得计进本库的 failed。

    document_key 不含 kb_id，只按它关联 mining_run_documents 会让两个库的同名文件
    互相串状态——聚合计数这条路径同样要按 kb 维度收敛。
    """
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb_a = await db.create_kb(domain=DOMAIN, name="A", owner_id=owner["id"])
    kb_b = await db.create_kb(domain=DOMAIN, name="B", owner_id=owner["id"])
    await _add_doc(db, kb_a, "spec.pdf")
    await _add_doc(db, kb_b, "spec.pdf")

    await _insert_run(async_pool, run_id="run-a", kb_id=kb_a["id"])
    await _insert_run_document(
        async_pool, rd_id="rd-a", run_id="run-a", document_key="doc:/spec.pdf",
        status="failed")

    counts = await db.overview_status_counts(kb_ids=[kb_a["id"], kb_b["id"]])
    assert counts[kb_a["id"]] == {"total": 1, "mining": 0, "failed": 1}
    assert counts[kb_b["id"]] == {"total": 1, "mining": 0, "failed": 0}


# ── overview_run_rollup ─────────────────────────────────────────────────────

async def test_last_mined_at_only_counts_completed_runs(async_pool):
    """卡片上的「最近挖掘」是最近一次**成功产出**；把 failed 算进去会让反复失败的库看着很新鲜。"""
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="K", owner_id=owner["id"])

    await _insert_run(
        async_pool, run_id="ok", kb_id=kb["id"], status="completed",
        started_at="2026-05-01T00:00:00+00:00", finished_at="2026-05-01T00:10:00+00:00")
    await _insert_run(
        async_pool, run_id="bad", kb_id=kb["id"], status="failed",
        started_at="2026-08-01T00:00:00+00:00", finished_at="2026-08-01T00:01:00+00:00")

    rollup = await db.overview_run_rollup(kb_ids=[kb["id"]])
    assert rollup[kb["id"]]["last_mined_at"] == "2026-05-01T00:10:00+00:00"


async def test_awaiting_review_picks_the_latest_run(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="K", owner_id=owner["id"])

    await _insert_run(
        async_pool, run_id="old-review", kb_id=kb["id"], status="awaiting_review",
        started_at="2026-05-01T00:00:00+00:00")
    await _insert_run(
        async_pool, run_id="new-review", kb_id=kb["id"], status="awaiting_review",
        started_at="2026-08-01T00:00:00+00:00")

    rollup = await db.overview_run_rollup(kb_ids=[kb["id"]])
    assert rollup[kb["id"]]["awaiting_review_run_id"] == "new-review"


async def test_rollup_null_when_nothing_matches(async_pool):
    """只有失败 run 时两项都是 NULL —— FILTER 无匹配行时 array_agg 为 NULL，下标取 NULL。"""
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="K", owner_id=owner["id"])
    await _insert_run(async_pool, run_id="bad", kb_id=kb["id"], status="failed")

    rollup = await db.overview_run_rollup(kb_ids=[kb["id"]])
    assert rollup[kb["id"]]["last_mined_at"] is None
    assert rollup[kb["id"]]["awaiting_review_run_id"] is None


# ── overview_recent_runs ────────────────────────────────────────────────────

async def test_recent_runs_are_cross_kb_named_and_capped(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb_a = await db.create_kb(domain=DOMAIN, name="库A", owner_id=owner["id"])
    kb_b = await db.create_kb(domain=DOMAIN, name="库B", owner_id=owner["id"])
    for i in range(4):
        await _insert_run(
            async_pool, run_id=f"a{i}", kb_id=kb_a["id"],
            started_at=f"2026-01-0{i + 1}T00:00:00+00:00")
    await _insert_run(
        async_pool, run_id="b0", kb_id=kb_b["id"],
        started_at="2026-02-01T00:00:00+00:00")

    runs = await db.overview_recent_runs(kb_ids=[kb_a["id"], kb_b["id"]], limit=3)

    assert [r["id"] for r in runs] == ["b0", "a3", "a2"]  # started_at DESC
    assert runs[0]["kb_id"] == kb_b["id"] and runs[0]["kb_name"] == "库B"


async def test_recent_runs_exclude_kbs_outside_the_scope(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    mine = await db.create_kb(domain=DOMAIN, name="mine", owner_id=owner["id"])
    theirs = await db.create_kb(domain=DOMAIN, name="theirs", owner_id=owner["id"])
    await _insert_run(async_pool, run_id="r-mine", kb_id=mine["id"])
    await _insert_run(async_pool, run_id="r-theirs", kb_id=theirs["id"])

    runs = await db.overview_recent_runs(kb_ids=[mine["id"]])
    assert [r["id"] for r in runs] == ["r-mine"]


# ── has_active_release ──────────────────────────────────────────────────────

async def test_has_active_release_false_on_kb_only_deployment(async_pool):
    """KB 挖掘 publish=False，永不产 release —— 检索范围里那个「不存在的选项」。"""
    db = KbDB(async_pool)
    assert await db.has_active_release(domain=DOMAIN) is False
