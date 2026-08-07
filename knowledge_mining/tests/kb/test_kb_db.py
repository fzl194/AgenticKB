"""P2.1 — KbDB async repository (kb_users / knowledge_bases / kb_members)."""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.db import KbDB

pytestmark = pytest.mark.asyncio


async def test_upsert_user_idempotent(async_pool):
    db = KbDB(async_pool)
    u1 = await db.upsert_user_by_username("alice", display_name="Alice")
    u2 = await db.upsert_user_by_username("alice")  # 幂等
    assert u1["username"] == "alice"
    assert u1["id"] == u2["id"]
    assert u2["display_name"] == "Alice"  # COALESCE 保留


async def test_create_kb_and_get(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain="cloud_core_network", name="KB-A", owner_id=owner["id"])
    assert kb["domain"] == "cloud_core_network"
    assert kb["status"] == "active"
    fetched = await db.get_kb(kb["id"])
    assert fetched is not None and fetched["name"] == "KB-A"


async def test_list_visible_owner_member_public(async_pool):
    db = KbDB(async_pool)
    alice = await db.upsert_user_by_username("alice")
    bob = await db.upsert_user_by_username("bob")
    carol = await db.upsert_user_by_username("carol")
    # alice 的 private，bob 的 public，carol 的 private（加 alice 为 member；visibility 已收口为 private/public）
    priv = await db.create_kb(domain="cloud_core_network", name="priv", owner_id=alice["id"], visibility="private")
    pub = await db.create_kb(domain="cloud_core_network", name="pub", owner_id=bob["id"], visibility="public")
    shared = await db.create_kb(domain="cloud_core_network", name="shared", owner_id=carol["id"], visibility="private")
    await db.add_member(kb_id=shared["id"], user_id=alice["id"], role="viewer")

    visible_to_alice = {k["name"] for k in await db.list_visible(user_id=alice["id"], domain="cloud_core_network")}
    # alice 看得到：自己的 private + bob 的 public + carol 的 private（被加为 member）
    assert visible_to_alice == {"priv", "pub", "shared"}

    visible_to_bob = {k["name"] for k in await db.list_visible(user_id=bob["id"], domain="cloud_core_network")}
    # bob 看得到：自己的 public + 别人的 public（看不到 alice/carol 的 private 未入成员）
    assert visible_to_bob == {"pub"}


async def test_list_visible_my_role_and_document_count(async_pool):
    """list_visible 附带 my_role（owner/editor/viewer）与 document_count。"""
    db = KbDB(async_pool)
    alice = await db.upsert_user_by_username("alice")
    bob = await db.upsert_user_by_username("bob")
    # alice 自有库（放 2 个文档）；bob 的 public 库（放 1 个文档，加 alice 为 editor）
    owned = await db.create_kb(domain="cloud_core_network", name="owned", owner_id=alice["id"])
    pub = await db.create_kb(domain="cloud_core_network", name="pub", owner_id=bob["id"], visibility="public")
    await db.add_member(kb_id=pub["id"], user_id=alice["id"], role="editor")
    for fn in ("a.md", "b.md"):
        await db.insert_document_identity(
            domain="cloud_core_network", kb_id=owned["id"], document_key=f"doc:/{fn}",
            document_name=fn, storage_path=f"/tmp/{owned['id']}/{fn}", directory_path="",
        )
    await db.insert_document_identity(
        domain="cloud_core_network", kb_id=pub["id"], document_key="doc:/c.md",
        document_name="c.md", storage_path=f"/tmp/{pub['id']}/c.md", directory_path="",
    )

    rows = {k["name"]: k for k in await db.list_visible(user_id=alice["id"], domain="cloud_core_network")}
    assert rows["owned"]["my_role"] == "owner"
    assert rows["owned"]["document_count"] == 2
    assert rows["pub"]["my_role"] == "editor"  # 被加为 editor 成员
    assert rows["pub"]["document_count"] == 1

    # bob 看自己的 public 库：owner 角色；看不到 alice 的 private（不在列表里）
    bob_rows = {k["name"]: k for k in await db.list_visible(user_id=bob["id"], domain="cloud_core_network")}
    assert bob_rows["pub"]["my_role"] == "owner"
    assert "owned" not in bob_rows


async def test_private_invisible_via_is_visible(async_pool):
    db = KbDB(async_pool)
    alice = await db.upsert_user_by_username("alice")
    bob = await db.upsert_user_by_username("bob")
    kb = await db.create_kb(domain="cloud_core_network", name="priv", owner_id=alice["id"], visibility="private")
    assert await db.is_visible(kb_id=kb["id"], user_id=alice["id"]) is True
    assert await db.is_visible(kb_id=kb["id"], user_id=bob["id"]) is False
    assert await db.can_write(kb_id=kb["id"], user_id=bob["id"]) is False


async def test_private_editor_can_write(async_pool):
    db = KbDB(async_pool)
    alice = await db.upsert_user_by_username("alice")
    bob = await db.upsert_user_by_username("bob")
    kb = await db.create_kb(domain="cloud_core_network", name="sh", owner_id=alice["id"], visibility="private")
    await db.add_member(kb_id=kb["id"], user_id=bob["id"], role="editor")
    assert await db.can_write(kb_id=kb["id"], user_id=bob["id"]) is True
    # 降级为 viewer → 不能写
    await db.add_member(kb_id=kb["id"], user_id=bob["id"], role="viewer")
    assert await db.can_write(kb_id=kb["id"], user_id=bob["id"]) is False
    assert await db.is_visible(kb_id=kb["id"], user_id=bob["id"]) is True  # 仍可读


async def test_soft_delete_hides_keeps_row(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain="cloud_core_network", name="K", owner_id=owner["id"])
    deleted = await db.soft_delete(kb["id"])
    assert deleted["status"] == "deleted"
    assert deleted["deleted_at"] is not None
    # 默认查不到（status='active' 过滤）
    assert await db.get_kb(kb["id"]) is None
    # 行还在
    assert (await db.get_kb(kb["id"], include_deleted=True))["status"] == "deleted"
    # list_visible 也过滤掉
    assert all(k["id"] != kb["id"] for k in await db.list_visible(user_id=owner["id"], domain="cloud_core_network"))


async def test_update_kb(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain="cloud_core_network", name="K", owner_id=owner["id"])
    # dict 驱动：name/visibility 更新(visibility 已收口为 private/public)
    updated = await db.update_kb(kb["id"], fields={"name": "K2", "visibility": "public"})
    assert updated["name"] == "K2" and updated["visibility"] == "public"

    # mining_workflow_id 设值
    updated = await db.update_kb(kb["id"], fields={"mining_workflow_id": "wf-x"})
    assert updated["mining_workflow_id"] == "wf-x"
    # 显式 None → 清空（SET NULL），这是 PATCH null-clearing 契约（解 BLOCKER）
    updated = await db.update_kb(kb["id"], fields={"mining_workflow_id": None})
    assert updated["mining_workflow_id"] is None
    # 未提供的列不动：设回 wf-x 后只改 name，mining_workflow_id 应保持
    await db.update_kb(kb["id"], fields={"mining_workflow_id": "wf-x"})
    updated = await db.update_kb(kb["id"], fields={"name": "K3"})
    assert updated["name"] == "K3"
    assert updated["mining_workflow_id"] == "wf-x"


async def test_unique_domain_name(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    await db.create_kb(domain="cloud_core_network", name="dup", owner_id=owner["id"])
    with pytest.raises(Exception):
        await db.create_kb(domain="cloud_core_network", name="dup", owner_id=owner["id"])
    # 不同 domain 同名允许
    kb2 = await db.create_kb(domain="generic", name="dup", owner_id=owner["id"])
    assert kb2["domain"] == "generic"


async def test_derived_status_mined_for_committed_run_document(async_pool):
    """P1：KB 挖掘 publish=False（无 active release）、run_document 到 committed
    时，派生 status 应为 'mined'。旧 _STATUS_CASE_SQL 没有 committed 档，
    会 ELSE 兜底回 'uploaded'——与「没挖过」无法区分，正是「挖掘后状态没打通」根因。
    """
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain="cloud_core_network", name="K", owner_id=owner["id"])
    doc = await db.insert_document_identity(
        domain="cloud_core_network", kb_id=kb["id"], document_key="doc:/a.md",
        document_name="a.md", storage_path=f"/tmp/{kb['id']}/a.md", directory_path="",
    )
    # 无 run_document → uploaded
    assert (await db.list_documents_in_kb(kb_id=kb["id"]))[0]["status"] == "uploaded"

    started = "2026-01-01T00:00:00+00:00"
    async with async_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO mining_runs
               (id, input_path, domain, channel, status, current_stage, started_at,
                execution_engine, workflow_manifest_json, metadata_json,
                total_documents, new_count, updated_count, skipped_count,
                failed_count, committed_count)
               VALUES (%s, %s, 'cloud_core_network', 'prod', 'completed', 'done', %s,
                       'workflow', '{}'::jsonb, '{}'::jsonb, 1, 1, 0, 0, 0, 1)""",
            ("run-mine-1", f"/tmp/{kb['id']}", started),
        )
        await conn.execute(
            """INSERT INTO mining_run_documents
               (id, run_id, document_key, raw_content_hash, normalized_content_hash,
                action, status, started_at, finished_at)
               VALUES (%s, %s, 'doc:/a.md', 'h', 'h', 'NEW', 'committed', %s, %s)""",
            ("rd-1", "run-mine-1", started, "2026-01-01T00:00:01+00:00"),
        )

    # committed + 无 active release → mined（P1 修复点）
    rows = await db.list_documents_in_kb(kb_id=kb["id"])
    assert rows[0]["status"] == "mined"
    assert await db.derive_document_status(doc["id"]) == "mined"

    # processing → mining（仍能区分「挖掘中」）
    async with async_pool.connection() as conn:
        await conn.execute(
            "UPDATE mining_run_documents SET status='processing', finished_at=NULL "
            "WHERE id='rd-1'"
        )
    assert (await db.list_documents_in_kb(kb_id=kb["id"]))[0]["status"] == "mining"


async def test_list_kb_runs_includes_committed_count(async_pool):
    """任务列表双色进度条需要 committed_count，内联到 list_kb_runs 避免前端 N+1 轮询。"""
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain="cloud_core_network", name="K", owner_id=owner["id"])
    started = "2026-01-01T00:00:00+00:00"
    run_id = f"run-{kb['id']}"
    async with async_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO mining_runs
               (id, kb_id, input_path, domain, channel, status, current_stage, started_at,
                execution_engine, workflow_manifest_json, metadata_json,
                total_documents, new_count, updated_count, skipped_count,
                failed_count, committed_count)
               VALUES (%s, %s, %s, 'cloud_core_network', 'prod', 'completed', 'done', %s,
                       'workflow', '{}'::jsonb, '{}'::jsonb, 5, 3, 0, 1, 1, 3)""",
            (run_id, kb["id"], f"/tmp/{kb['id']}", started),
        )
    runs = await db.list_kb_runs(kb["id"])
    assert len(runs) == 1
    assert runs[0]["id"] == run_id
    assert runs[0]["committed_count"] == 3


async def test_get_document_knowledge_returns_relations(async_pool):
    """get_document_knowledge 经 build→snapshot 返回切片/检索单元/实体提及/**关系**。
    关系(asset_raw_segment_relations)是文档预览页「关系」Tab 的数据源。"""
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain="cloud_core_network", name="K", owner_id=owner["id"])
    doc = await db.insert_document_identity(
        domain="cloud_core_network", kb_id=kb["id"], document_key="doc:/a.md",
        document_name="a.md", storage_path=f"/tmp/{kb['id']}/a.md", directory_path="",
    )
    doc_id = doc["id"]
    started = "2026-01-01T00:00:00+00:00"
    snap_id = f"snap-{doc_id}"
    build_id = f"build-{doc_id}"
    seg0, seg1 = f"seg0-{doc_id}", f"seg1-{doc_id}"
    async with async_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO asset_document_snapshots
               (id, domain, normalized_content_hash, raw_content_hash, mime_type, created_at)
               VALUES (%s, 'cloud_core_network', 'nh', 'rh', 'text/markdown', %s)""",
            (snap_id, started),
        )
        await conn.execute(
            """INSERT INTO asset_raw_segments
               (id, document_snapshot_id, segment_key, segment_index,
                raw_text, normalized_text, content_hash, normalized_hash)
               VALUES (%s, %s, 'seg-0', 0, '源段文本', '源段文本', 'ch0', 'nh0')""",
            (seg0, snap_id),
        )
        await conn.execute(
            """INSERT INTO asset_raw_segments
               (id, document_snapshot_id, segment_key, segment_index,
                raw_text, normalized_text, content_hash, normalized_hash)
               VALUES (%s, %s, 'seg-1', 1, '目标段文本', '目标段文本', 'ch1', 'nh1')""",
            (seg1, snap_id),
        )
        await conn.execute(
            """INSERT INTO asset_builds
               (id, build_code, status, build_mode, domain, kb_id, created_at)
               VALUES (%s, %s, 'validated', 'full', 'cloud_core_network', %s, %s)""",
            (build_id, f"BUILD-{doc_id}", kb["id"], started),
        )
        await conn.execute(
            """INSERT INTO asset_build_document_snapshots
               (build_id, document_id, document_snapshot_id, selection_status, reason)
               VALUES (%s, %s, %s, 'active', 'add')""",
            (build_id, doc_id, snap_id),
        )
        await conn.execute(
            """INSERT INTO asset_raw_segment_relations
               (id, document_snapshot_id, source_segment_id, target_segment_id,
                relation_type, weight, confidence)
               VALUES (%s, %s, %s, %s, 'elaborates', 0.8, 0.9)""",
            (f"rel-{doc_id}", snap_id, seg0, seg1),
        )

    knowledge = await db.get_document_knowledge(kb["id"], doc_id)
    assert knowledge["mined"] is True
    assert knowledge["build_id"] == build_id
    assert len(knowledge["segments"]) == 2
    assert knowledge["retrieval_units"] == []  # 空数组兜底
    assert knowledge["entity_mentions"] == []
    rels = knowledge["relations"]
    assert len(rels) == 1
    r = rels[0]
    assert r["relation_type"] == "elaborates"
    assert r["source_segment_text"] == "源段文本"
    assert r["target_segment_text"] == "目标段文本"
    assert r["weight"] == 0.8
    assert r["confidence"] == 0.9
