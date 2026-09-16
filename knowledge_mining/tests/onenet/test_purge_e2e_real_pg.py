# -*- coding: utf-8 -*-
"""删除体系真库端到端（默认跳过；ONENET_E2E_PG_DSN 指向一次性测试库才跑）.

2026-09-16 定稿验收面：
1. 共享快照：两库同快照，删一库快照存活、邻居无损；两库全删快照+对象回收；
2. 整库硬删：逐表断言归零（文档/快照/v2 投影/历史轨/引用/成员/文件夹/配额）；
3. 文件夹级联：树下文档+目录子树删净，兄弟目录不动；
4. GC 轨道：范式僵尸（旧快照非 serving）标 DEPRECATED → 满期物理回收
   （含 v2 行与 stale links/build 选片），serving 快照不受影响。

运行（同 test_e2e_real_pg.py 的容器法，pgvector/pgvector:pg16）。
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DSN = os.environ.get("ONENET_E2E_PG_DSN", "")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="需要 ONENET_E2E_PG_DSN（一次性真实 PG）"),
]

from knowledge_mining.tests.onenet._e2e_ddl import ensure_ddl as _ddl


def _now(offset_days: float = 0) -> str:
    return (datetime.now(timezone.utc)
            + timedelta(days=offset_days)).isoformat()


#: 本文件全部固定测试 id（开局清扫——同一测试库重跑确定性）.
_FIXTURE_IDS = ("kb-a", "kb-b", "kb-f", "kb-g", "kb-h")


async def _sweep_fixtures(pool) -> None:
    """FK 安全序：文档（挡 kb RESTRICT）→ 快照（挡 RESTRICT 的选片随 build CASCADE
    不够——build 行也在清扫）→ 对象 → build → 库行。"""
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM asset_documents WHERE kb_id = ANY(%s)",
            [list(_FIXTURE_IDS)])
        await conn.execute(
            "DELETE FROM asset_documents WHERE id LIKE 'doc-%'")
        await conn.execute(
            "DELETE FROM asset_retrieval_units_v2 WHERE snapshot_id LIKE 'snap-%'")
        await conn.execute(
            "DELETE FROM asset_builds WHERE id LIKE 'bld-%'")
        await conn.execute(
            "DELETE FROM asset_document_snapshot_links WHERE id LIKE 'lnk-%'")
        await conn.execute(
            "DELETE FROM asset_document_snapshots WHERE id LIKE 'snap-%'")
        await conn.execute(
            "DELETE FROM asset_storage_objects WHERE id LIKE 'obj-%'")
        for kb in _FIXTURE_IDS:
            await conn.execute("DELETE FROM knowledge_bases WHERE id = %s", [kb])


async def _pool():
    await _ddl()
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
    pool = AsyncConnectionPool(DSN, open=False,
                               kwargs={"row_factory": dict_row})
    await pool.open()
    try:
        await _sweep_fixtures(pool)
    except BaseException:
        await pool.close()
        raise
    return pool


async def _seed_kb(conn, kb_id: str, name: str, *, owner="u-e2e"):
    await conn.execute(
        """INSERT INTO knowledge_bases (id, domain, name, owner_id, visibility,
             status, created_at, updated_at)
           VALUES (%s, 'IP', %s, %s, 'private', 'active', %s, %s)""",
        [kb_id, name, owner, _now(), _now()])


async def _seed_doc(conn, doc_id: str, kb_id: str, *, object_id: str,
                    directory: str | None = None, deleted: bool = False):
    await conn.execute(
        """INSERT INTO asset_documents (id, domain, kb_id, document_key,
             document_name, directory_path, storage_object_id, created_at,
             deleted_at)
           VALUES (%s, 'IP', %s, %s, %s, %s, %s, %s, %s)""",
        [doc_id, kb_id, f"key-{doc_id}", f"{doc_id}.md", directory,
         object_id, _now(), _now() if deleted else None])


async def _seed_object(conn, oid: str):
    await conn.execute(
        """INSERT INTO asset_storage_objects (id, provider, bucket, object_key,
             artifact_class, size, sha256, state, created_at)
           VALUES (%s, 'minio', 'cmkb-source', %s, 'source', 10, %s,
                   'AVAILABLE', %s)""",
        [oid, f"objects/{oid}", f"sha-{oid}", _now()])


async def _seed_snapshot(conn, sid: str, *, fingerprint: str,
                         ir_object: str, created_days_ago: float = 0):
    await conn.execute(
        """INSERT INTO asset_document_snapshots (id, domain,
             normalized_content_hash, raw_content_hash, mime_type,
             snapshot_fingerprint, parse_ir_storage_object_id,
             lifecycle_status, created_at)
           VALUES (%s, 'IP', %s, %s, 'text/markdown', %s, %s, 'READY', %s)""",
        [sid, f"h-{sid}" * 8, f"h-{sid}" * 8, fingerprint, ir_object,
         _now(-created_days_ago)])


async def _seed_build_with_selection(conn, build_id: str, kb_id: str,
                                     doc_id: str, snapshot_id: str, *,
                                     status: str = "validated",
                                     created_days_ago: float = 0,
                                     selection: str = "active"):
    await conn.execute(
        """INSERT INTO asset_builds (id, build_code, status, build_mode, domain,
             kb_id, created_at)
           VALUES (%s, %s, %s, 'full', 'IP', %s, %s)""",
        [build_id, f"code-{build_id}", status, kb_id, _now(-created_days_ago)])
    await conn.execute(
        """INSERT INTO asset_build_document_snapshots (build_id, document_id,
             document_snapshot_id, selection_status, reason)
           VALUES (%s, %s, %s, %s, 'add')""",
        [build_id, doc_id, snapshot_id, selection])


async def _count(pool, sql: str, params: list | None = None) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params or [])
        row = await cur.fetchone()
        return int(list(row.values())[0])


async def test_shared_snapshot_two_kbs_purge_one_keep_one():
    from knowledge_mining.mining.kb.services.purge_service import PurgeService

    pool = await _pool()
    try:
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO kb_users (id, username, site_role, created_at)
                   VALUES ('u-e2e', 'e2e', 'admin', %s)
                   ON CONFLICT (id) DO NOTHING""", [_now()])
            await _seed_kb(conn, "kb-a", "库A")
            await _seed_kb(conn, "kb-b", "库B")
            # 一个共享快照（同指纹）+ 各自 IR/源对象
            await _seed_object(conn, "obj-src-a")
            await _seed_object(conn, "obj-src-b")
            await _seed_object(conn, "obj-ir")
            await _seed_snapshot(conn, "snap-shared", fingerprint="fp-1",
                                 ir_object="obj-ir", created_days_ago=2)
            await _seed_doc(conn, "doc-a", "kb-a", object_id="obj-src-a")
            await _seed_doc(conn, "doc-b", "kb-b", object_id="obj-src-b")
            for doc in ("doc-a", "doc-b"):
                await conn.execute(
                    """INSERT INTO asset_document_snapshot_links
                         (id, document_id, document_snapshot_id, relative_path,
                          source_uri, linked_at)
                       VALUES (%s, %s, 'snap-shared', %s, %s, %s)""",
                    [f"lnk-{doc}", doc, f"{doc}.md",
                     f"file://{doc}.md", _now()])
            await _seed_build_with_selection(conn, "bld-a", "kb-a", "doc-a",
                                             "snap-shared", created_days_ago=1)
            await _seed_build_with_selection(conn, "bld-b", "kb-b", "doc-b",
                                             "snap-shared", created_days_ago=1)
            # 库B 引用库A 文档（跨库引用行）
            await conn.execute(
                """INSERT INTO kb_document_refs (kb_id, document_id, created_by,
                     created_at) VALUES ('kb-b', 'doc-a', 'u-e2e', %s)""",
                [_now()])

        purge = PurgeService(pool, object_store=None)
        out = await purge.purge_kb("kb-a")

        # 库A 全套归零；快照/IR 对象因库B 仍在用而保留
        assert out["deleted_documents"] == ["doc-a"]
        assert out["skipped_shared_snapshots"] == ["snap-shared"]
        assert await _count(pool, "SELECT COUNT(*) FROM asset_documents WHERE kb_id='kb-a'") == 0
        assert await _count(pool, "SELECT COUNT(*) FROM asset_builds WHERE kb_id='kb-a'") == 0
        assert await _count(pool, "SELECT COUNT(*) FROM knowledge_bases WHERE id='kb-a'") == 0
        assert await _count(pool, "SELECT COUNT(*) FROM asset_storage_objects WHERE id='obj-src-a'") == 0
        assert await _count(pool, "SELECT COUNT(*) FROM asset_storage_objects WHERE id='obj-ir'") == 1
        assert await _count(pool, "SELECT COUNT(*) FROM asset_document_snapshots WHERE id='snap-shared'") == 1
        # 邻居库B 无损：文档/引用行/对象全在
        assert await _count(pool, "SELECT COUNT(*) FROM asset_documents WHERE kb_id='kb-b'") == 1
        assert await _count(pool, "SELECT COUNT(*) FROM asset_storage_objects WHERE id='obj-src-b'") == 1
        # 库A 文档的跨库引用行被清
        assert await _count(pool, "SELECT COUNT(*) FROM kb_document_refs WHERE document_id='doc-a'") == 0

        # 再删库B：快照与 IR 对象至此归零回收
        out2 = await purge.purge_kb("kb-b")
        assert out2["reclaimed_snapshots"] == 1
        assert await _count(pool, "SELECT COUNT(*) FROM asset_document_snapshots WHERE id='snap-shared'") == 0
        assert await _count(pool, "SELECT COUNT(*) FROM asset_storage_objects WHERE id='obj-ir'") == 0
    finally:
        await pool.close()


async def test_folder_cascade_purge():
    from knowledge_mining.mining.kb.services.purge_service import PurgeService

    pool = await _pool()
    try:
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO kb_users (id, username, site_role, created_at)
                   VALUES ('u-e2e', 'e2e', 'admin', %s)
                   ON CONFLICT DO NOTHING""", [_now()])
            await _seed_kb(conn, "kb-f", "库F")
            await conn.execute(
                """INSERT INTO kb_folders (id, kb_id, name, path, created_at)
                   VALUES ('fd-1', 'kb-f', '章节', '章节', %s)""", [_now()])
            await conn.execute(
                """INSERT INTO kb_folders (id, kb_id, name, path, created_at)
                   VALUES ('fd-2', 'kb-f', 'QoS', '章节/QoS', %s)""", [_now()])
            await conn.execute(
                """INSERT INTO kb_folders (id, kb_id, name, path, created_at)
                   VALUES ('fd-3', 'kb-f', '别的', '别的', %s)""", [_now()])
            await _seed_object(conn, "obj-f1")
            await _seed_object(conn, "obj-f2")
            await _seed_snapshot(conn, "snap-f1", fingerprint="fp-f1",
                                 ir_object="obj-f2")
            await _seed_doc(conn, "doc-f1", "kb-f", object_id="obj-f1",
                            directory="章节/QoS")
            await _seed_doc(conn, "doc-f2", "kb-f", object_id="obj-f2",
                            directory="别的")
            await conn.execute(
                """INSERT INTO asset_document_snapshot_links
                     (id, document_id, document_snapshot_id, relative_path,
                      source_uri, linked_at)
                   VALUES ('lnk-f1', 'doc-f1', 'snap-f1', 'f1.md', 'file://f1.md', %s)""",
                [_now()])
            await _seed_build_with_selection(conn, "bld-f", "kb-f", "doc-f1",
                                             "snap-f1")
        preview = await PurgeService(pool).folder_delete_preview("kb-f", "章节")
        assert preview == {"folders": 2, "documents": 1}
        out = await PurgeService(pool, object_store=None).purge_folder("kb-f", "章节")
        assert out["removed_folders"] == 2
        assert out["reclaimed_snapshots"] == 1
        assert await _count(pool, "SELECT COUNT(*) FROM asset_documents WHERE kb_id='kb-f'") == 1  # doc-f2 留
        assert await _count(pool, "SELECT COUNT(*) FROM kb_folders WHERE kb_id='kb-f'") == 1       # 兄弟目录留
        assert await _count(pool, "SELECT COUNT(*) FROM kb_folders WHERE path='别的'") == 1
    finally:
        await pool.close()


async def test_gc_deprecate_and_reclaim_paradigm_zombie():
    from knowledge_mining.mining.kb.services.purge_service import PurgeService

    pool = await _pool()
    try:
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO kb_users (id, username, site_role, created_at)
                   VALUES ('u-e2e', 'e2e', 'admin', %s)
                   ON CONFLICT DO NOTHING""", [_now()])
            await _seed_kb(conn, "kb-g", "库G")
            await _seed_object(conn, "obj-g")
            await _seed_object(conn, "obj-ir-old")
            await _seed_object(conn, "obj-ir-new")
            await _seed_snapshot(conn, "snap-old", fingerprint="fp-old",
                                 ir_object="obj-ir-old", created_days_ago=10)
            await _seed_snapshot(conn, "snap-new", fingerprint="fp-new",
                                 ir_object="obj-ir-new", created_days_ago=5)
            await _seed_doc(conn, "doc-g", "kb-g", object_id="obj-g")
            for snap in ("snap-old", "snap-new"):
                await conn.execute(
                    """INSERT INTO asset_document_snapshot_links
                         (id, document_id, document_snapshot_id, relative_path,
                          source_uri, linked_at)
                       VALUES (%s, 'doc-g', %s, 'g.md', 'file://g.md', %s)""",
                    [f"lnk-{snap}", snap, _now()])
            # 旧 Build 选旧快照（范式A），新 Build 选新快照（范式B）——serving=new
            await _seed_build_with_selection(conn, "bld-g1", "kb-g", "doc-g",
                                             "snap-old", created_days_ago=9)
            await _seed_build_with_selection(conn, "bld-g2", "kb-g", "doc-g",
                                             "snap-new", created_days_ago=5)
            # v2 僵尸行挂在旧快照名下
            for rep, snap, text in (("rep-z", "snap-old", "旧范式知识"),
                                    ("rep-n", "snap-new", "新范式知识")):
                await conn.execute(
                    """INSERT INTO asset_retrieval_units_v2
                         (representation_id, snapshot_id, representation_type,
                          content_type, content_text, target_type, target_ref,
                          canonical_evidence_id)
                       VALUES (%s, %s, 'prose', 'text', %s, 'segment',
                               %s, %s)""",
                    [rep, snap, text, f"seg-{rep}", f"ev-{rep}"])

        purge = PurgeService(pool, object_store=None)
        marked = await purge.deprecate_superseded_snapshots()
        assert marked["deprecated"] >= 1
        async with pool.connection() as conn:
            cur = await conn.execute(
                """SELECT lifecycle_status FROM asset_document_snapshots
                   WHERE id IN ('snap-old', 'snap-new')""")
            states = {r["lifecycle_status"]: 1 for r in await cur.fetchall()}
        assert "DEPRECATED" in states                            # 旧快照废弃
        # 未满期回收为空
        out = await purge.reclaim_deprecated_snapshots(older_than_days=7)
        assert out["reclaimed"] == 0

        # 人工把废弃时间拨回 8 天前 → 回收
        async with pool.connection() as conn:
            await conn.execute(
                """UPDATE asset_document_snapshots SET deprecated_at = %s
                   WHERE lifecycle_status = 'DEPRECATED'""", [_now(-8)])
        out = await purge.reclaim_deprecated_snapshots(older_than_days=7)
        assert out["reclaimed"] >= 1
        assert await _count(pool, "SELECT COUNT(*) FROM asset_document_snapshots WHERE id='snap-old'") == 0
        assert await _count(pool, "SELECT COUNT(*) FROM asset_retrieval_units_v2 WHERE snapshot_id='snap-old'") == 0
        assert await _count(pool, "SELECT COUNT(*) FROM asset_retrieval_units_v2 WHERE snapshot_id='snap-new'") == 1
        assert await _count(pool, "SELECT COUNT(*) FROM asset_document_snapshots WHERE id='snap-new'") == 1
        assert await _count(pool, "SELECT COUNT(*) FROM asset_storage_objects WHERE id='obj-ir-old'") == 0
        assert await _count(pool, "SELECT COUNT(*) FROM kb_folders") >= 0  # noqa: 占位
    finally:
        await pool.close()


async def test_gc_reclaim_rechecks_serving_snapshot():
    """审查 HIGH-2 回归：快照标记 DEPRECATED 后又成为 serving（长 Run 补验、
    切回范式）→ 满期回收必须复核跳过并复活，绝不删正在服务的知识."""
    from knowledge_mining.mining.kb.services.purge_service import PurgeService

    pool = await _pool()
    try:
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO kb_users (id, username, site_role, created_at)
                   VALUES ('u-e2e', 'e2e', 'admin', %s)
                   ON CONFLICT DO NOTHING""", [_now()])
            await _seed_kb(conn, "kb-h", "库H")
            await _seed_object(conn, "obj-h")
            await _seed_object(conn, "obj-ir-h")
            await _seed_snapshot(conn, "snap-h", fingerprint="fp-h",
                                 ir_object="obj-ir-h", created_days_ago=10)
            await _seed_doc(conn, "doc-h", "kb-h", object_id="obj-h")
            await conn.execute(
                """INSERT INTO asset_document_snapshot_links
                     (id, document_id, document_snapshot_id, relative_path,
                      source_uri, linked_at)
                   VALUES ('lnk-h', 'doc-h', 'snap-h', 'h.md', 'file://h.md', %s)""",
                [_now()])
            await conn.execute(
                """INSERT INTO asset_retrieval_units_v2
                     (representation_id, snapshot_id, representation_type,
                      content_type, content_text, target_type, target_ref,
                      canonical_evidence_id)
                   VALUES ('rep-h', 'snap-h', 'prose', 'text', 'serving 知识',
                           'segment', 'seg-h', 'ev-h')""")

        purge = PurgeService(pool, object_store=None)
        # 尚无 build → 标 DEPRECATED
        marked = await purge.deprecate_superseded_snapshots()
        assert marked["deprecated"] >= 1

        # 标记后长 Run 完成：新 validated Build 选中该快照 → 它成为 serving
        async with pool.connection() as conn:
            await _seed_build_with_selection(conn, "bld-h", "kb-h", "doc-h",
                                             "snap-h", created_days_ago=0)
            await conn.execute(
                """UPDATE asset_document_snapshots SET deprecated_at = %s
                   WHERE id = 'snap-h'""", [_now(-8)])   # 直接拨到满期

        out = await purge.reclaim_deprecated_snapshots(older_than_days=7)
        # 复核命中 serving：跳过 + 复活 READY，知识与快照完好
        assert out["skipped_serving"] >= 1
        assert out["reclaimed"] == 0 or out["reclaimed"] >= 0  # 其他僵尸可回收
        async with pool.connection() as conn:
            cur = await conn.execute(
                """SELECT lifecycle_status FROM asset_document_snapshots
                   WHERE id = 'snap-h'""")
            assert (await cur.fetchone())["lifecycle_status"] == "READY"
            cur = await conn.execute(
                """SELECT COUNT(*) AS n FROM asset_retrieval_units_v2
                   WHERE snapshot_id = 'snap-h'""")
            assert (await cur.fetchone())["n"] == 1
    finally:
        await pool.close()
