# -*- coding: utf-8 -*-
"""引用服务 + 范围 UNION 三类口径（fake 连接捕获 SQL，无 PG）.

口径断言（47 号 §四-5）：
- 检索/证据口径（get_current_serving_snapshot）吃引用；
- 统计口径（_CURRENT_SNAPSHOT_CTE 的 stats_assets / stats_retrieval_unit_types）
  **不吃**引用（保持自有口径，42 号 O4 教训）。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.onenet.refs_service import RefsError, RefsService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------- fake 连接


class FakeCursor:
    """同一连接内顺序 execute：每次 execute 弹出下一组结果集."""

    def __init__(self, script: list[list]):
        self.script = list(script)
        self.sql = ""
        self.sqls: list[str] = []
        self.params: list | None = None
        self._results: list = []

    async def execute(self, sql, params=None):
        self.sql = sql
        self.sqls.append(sql)
        self.params = params
        self._results = list(self.script.pop(0)) if self.script else []
        return self

    async def fetchone(self):
        return self._results.pop(0) if self._results else None

    async def fetchall(self):
        out, self._results = self._results, []
        return out

    def rowcount(self):
        return len(self._consumed) if hasattr(self, "_consumed") else 0


class FakeConn:
    def __init__(self, script):
        self.cursor = FakeCursor(script)

    async def execute(self, sql, params=None):
        return await self.cursor.execute(sql, params)

    async def fetchone(self):
        return await self.cursor.fetchone()

    async def fetchall(self):
        return await self.cursor.fetchall()


class _ConnCtx:
    """async with pool.connection() as conn —— psycopg 池协议的 fake."""

    def __init__(self, conn: FakeConn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class FakePool:
    """每个元素 = 一个连接的「结果集脚本」（每次 execute 消费一组）."""

    def __init__(self, connection_scripts: list[list[list]]):
        self.scripts = list(connection_scripts)
        self.connections: list[FakeConn] = []

    def connection(self):
        script = self.scripts.pop(0) if self.scripts else []
        conn = FakeConn(script)
        self.connections.append(conn)
        return _ConnCtx(conn)


# ---------------------------------------------------------------- serving 范围口径


async def test_serving_snapshot_scope_union_contains_refs_branch():
    pool = FakePool([[[None]]])  # 1 连接 1 查询，无命中行
    db = KbDB(pool)
    await db.get_current_serving_snapshot("kb-biz", "doc-ref-1")
    sql = pool.connections[0].cursor.sql
    assert "kb_document_refs" in sql
    assert "d.kb_id = %s" in sql
    assert "OR EXISTS" in sql
    # 三个占位参数：document_id, kb_id, kb_id（refs 分支复用请求库）
    assert pool.connections[0].cursor.params == ["doc-ref-1", "kb-biz", "kb-biz"]
    # 负载守卫不动
    assert "b.kb_id = d.kb_id" in sql
    assert "d.deleted_at IS NULL" in sql
    assert "selection_status = 'active'" in sql


async def test_stats_scope_stays_owned_only():
    """统计口径不吃引用：CTE SQL 不出现 kb_document_refs（O4 教训）."""
    pool = FakePool([[[{"snapshots": 0, "segments": 0, "retrieval_units": 0, "embeddings": 0}]]])
    db = KbDB(pool)
    await db.stats_assets(kb_ids=["kb-biz"])
    sql = pool.connections[0].cursor.sql
    assert "kb_document_refs" not in sql
    assert "b.kb_id = ANY(%(kb)s)" in sql

    pool2 = FakePool([[]])
    db2 = KbDB(pool2)
    await db2.stats_retrieval_unit_types(kb_ids=["kb-biz"])
    assert "kb_document_refs" not in pool2.connections[0].cursor.sql


# ---------------------------------------------------------------- 归属/列表/授权


async def test_document_in_kb_or_referenced_branches():
    pool = FakePool([[[{"?column?": 1}]]])  # 1 连接 1 查询，EXISTS 命中
    db = KbDB(pool)
    assert await db.document_in_kb_or_referenced("kb-biz", "doc-1") is True
    sql = pool.connections[0].cursor.sql
    assert "kb_document_refs" in sql and "OR EXISTS" in sql

    pool2 = FakePool([[[None]]])  # 无命中
    db2 = KbDB(pool2)
    assert await db2.document_in_kb_or_referenced("kb-biz", "doc-x") is False


async def test_list_referenced_documents_shape():
    row = {"id": "d1", "document_name": "A.jsonl", "directory_path": None,
           "file_size": 5, "created_at": "t", "document_key": "onenet:DOC1:x",
           "source_kb_name": "一张网产品文档", "referenced_at": "t2",
           "domain": "d", "storage_object_id": "o", "source_raw_hash": "h",
           "content_revision": 1}
    pool = FakePool([[[row]]])
    db = KbDB(pool)
    rows = await db.list_referenced_documents("kb-biz")
    assert rows[0]["source_kb_name"] == "一张网产品文档"
    assert "kb_document_refs" in pool.connections[0].cursor.sql
    assert "d.deleted_at IS NULL" in pool.connections[0].cursor.sql


async def test_document_readable_by_user_checks_owning_then_refs():
    # 同一连接两次查询：先属主库（空）→ 再引用库（命中）
    pool = FakePool([[[None], [{"?column?": 1}]]])
    db = KbDB(pool)
    assert await db.document_readable_by_user("doc-1", "u1") is True
    sqls = pool.connections[0].cursor.sqls
    assert "kb_document_refs" not in sqls[0]   # 先查属主库
    assert "kb_document_refs" in sqls[1]       # 再查引用库
    # 两条查询都带 admin/owner/public/member 可见性条件
    for sql in sqls:
        assert "site_role = 'admin'" in sql


# ---------------------------------------------------------------- RefsService


def _kb_row(domain="cloud_core_network"):
    return {"domain": domain}


def _doc_row(domain="cloud_core_network", deleted=False, kb_meta='{"kind": "onenet"}'):
    return {"id": "doc-1", "deleted_at": "t" if deleted else None,
            "metadata_json": "{}", "kb_meta": kb_meta, "owner_domain": domain}


async def test_add_refs_happy_path():
    pool = FakePool([[                      # 单连接三次 execute
        [_kb_row()],                         # 目标库
        [_doc_row()],                        # 文档校验
        [{"document_id": "doc-1"}],          # INSERT RETURNING
    ]])
    svc = RefsService(pool)
    out = await svc.add_refs(kb_id="kb-biz", document_ids=["doc-1"], actor_id="u1")
    assert out == {"added": ["doc-1"], "skipped": []}
    insert_sql = pool.connections[0].cursor.sqls[2]
    assert "ON CONFLICT (kb_id, document_id) DO NOTHING" in insert_sql


async def test_add_refs_cross_domain_rejected():
    pool = FakePool([[
        [_kb_row(domain="cloud_core_network")],
        [_doc_row(domain="vendor_tech_docs")],  # 文档在别的域
    ]])
    svc = RefsService(pool)
    with pytest.raises(RefsError, match="cross_domain_reference"):
        await svc.add_refs(kb_id="kb-biz", document_ids=["doc-1"], actor_id="u1")


async def test_add_refs_non_onenet_rejected():
    pool = FakePool([[
        [_kb_row()],
        [_doc_row(kb_meta='{"kind": "normal"}')],  # 普通库文档
    ]])
    svc = RefsService(pool)
    with pytest.raises(RefsError, match="not_onenet_document"):
        await svc.add_refs(kb_id="kb-biz", document_ids=["doc-1"], actor_id="u1")


async def test_add_refs_deleted_doc_skipped():
    pool = FakePool([[
        [_kb_row()],
        [_doc_row(deleted=True)],
    ]])
    svc = RefsService(pool)
    out = await svc.add_refs(kb_id="kb-biz", document_ids=["doc-1"], actor_id="u1")
    assert out["added"] == []
    assert out["skipped"][0]["reason"] == "not_found"


async def test_add_refs_duplicate_idempotent():
    pool = FakePool([[
        [_kb_row()],
        [_doc_row()],
        [None],   # INSERT ... ON CONFLICT DO NOTHING 无返回（已存在）
    ]])
    svc = RefsService(pool)
    out = await svc.add_refs(kb_id="kb-biz", document_ids=["doc-1"], actor_id="u1")
    assert out["skipped"][0]["reason"] == "already_referenced"


async def test_remove_refs_sql_shape():
    pool = FakePool([[[{"document_id": "d1"}]]])
    svc = RefsService(pool)
    out = await svc.remove_refs(kb_id="kb-biz", document_ids=["d1", "d2"])
    assert out == {"removed": ["d1"]}
    assert "DELETE FROM kb_document_refs" in pool.connections[0].cursor.sql
    assert "kb_id = %s AND document_id = ANY(%s)" in pool.connections[0].cursor.sql
