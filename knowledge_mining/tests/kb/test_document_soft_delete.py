"""BUG-1 / P08-S1（批次1）：KB 文档软删替代硬删——历史 Build 不再被改写。

原实现 DELETE FROM asset_documents 借 FK CASCADE 抹掉 snapshot_links 与
build_document_snapshots（改写历史 Build）。软删盖 deleted_at，读面过滤退出。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.db import KbDB


def _kbdb(calls: list) -> KbDB:
    import asyncio

    class _Conn:
        async def execute(self, sql, params):
            calls.append((sql, params))
            class _Cur:
                async def fetchone(self):
                    class _Row(dict):
                        pass
                    return None
                async def fetchall(self):
                    return []
            return _Cur()

    class _Pool:
        def connection(self):
            class _Ctx:
                async def __aenter__(self):
                    return _Conn()
                async def __aexit__(self, *a):
                    return False
            return _Ctx()

    db = object.__new__(KbDB)
    db._pool = _Pool()
    return db


# ── 读面过滤（SQL 内容断言：9 类查询点逐一钉死） ─────────────────────────────

def test_document_count_subqueries_exclude_soft_deleted():
    import inspect
    src = inspect.getsource(KbDB)
    assert src.count("WHERE d.kb_id = kb.id AND d.deleted_at IS NULL") == 2


def test_list_and_status_queries_filter_soft_deleted():
    import inspect
    src = inspect.getsource(KbDB)
    assert 'clause = "d.kb_id = %s AND d.deleted_at IS NULL"' in src
    assert "WHERE d.kb_id = ANY(%s) AND d.deleted_at IS NULL\n                    GROUP BY 1" in src
    assert "WHERE d.kb_id = ANY(%s) AND d.deleted_at IS NULL\n                    GROUP BY d.kb_id" in src
    assert "WHERE kb_id = %s AND deleted_at IS NULL" in src  # 文件夹计数
    assert "WHERE d.id = %s AND d.deleted_at IS NULL" in src  # derive_document_status


def test_identity_lookup_filters_by_default_and_can_include_deleted():
    calls: list = []
    db = _kbdb(calls)
    import asyncio
    asyncio.run(db.get_document_identity("doc-1"))
    sql = calls[0][0]
    assert "AND d.deleted_at IS NULL" in sql
    calls.clear()
    asyncio.run(db.get_document_identity("doc-1", include_deleted=True))
    assert "AND d.deleted_at IS NULL" not in calls[0][0]


def test_soft_delete_writes_timestamp_not_delete():
    calls: list = []
    db = _kbdb(calls)
    import asyncio
    asyncio.run(db.soft_delete_document("doc-1"))
    sql, params = calls[0]
    assert "UPDATE asset_documents SET deleted_at" in sql
    assert "DELETE FROM asset_documents" not in sql
    assert params[1] == "doc-1"


def test_revive_moves_pointer_and_clears_deleted():
    calls: list = []
    db = _kbdb(calls)
    import asyncio
    asyncio.run(db.revive_document_from_storage(
        "doc-1", storage_object_id="obj-2", source_raw_hash="h2",
        file_size=9, modified_at="2026-08-27T00:00:00+00:00",
    ))
    sql = calls[0][0]
    assert "deleted_at = NULL" in sql and "content_revision = content_revision + 1" in sql
    assert "deleted_at IS NOT NULL" in sql  # 只复活软删行


def test_find_by_key_filters_by_default():
    calls: list = []
    db = _kbdb(calls)
    import asyncio
    asyncio.run(db.find_document_by_key("kb-1", "doc:/a.pdf"))
    assert "AND deleted_at IS NULL" in calls[0][0]
    calls.clear()
    asyncio.run(db.find_document_by_key("kb-1", "doc:/a.pdf", include_deleted=True))
    assert "deleted_at IS NULL" not in calls[0][0].split("FROM")[1]


# ── DocumentService 行为（内存假件） ────────────────────────────────────────

class _FakeDb:
    def __init__(self) -> None:
        self.row: dict = {"id": "doc-1", "kb_id": "kb-1", "storage_path": None,
                          "deleted_at": None}
        self.operations: list[str] = []

    async def get_kb(self, kb_id):
        return {"id": kb_id, "domain": "generic"}

    async def is_visible(self, *, kb_id, user_id):
        return True

    async def can_write(self, *, kb_id, user_id):
        return True

    async def get_document_identity(self, document_id, *, include_deleted=False):
        if not include_deleted and self.row.get("deleted_at"):
            return None
        return dict(self.row)

    async def soft_delete_document(self, document_id):
        self.operations.append("soft_delete")
        self.row["deleted_at"] = "2026-08-27T00:00:00+00:00"

    async def clear_document_deleted(self, document_id):
        self.operations.append("restore")
        self.row["deleted_at"] = None

    async def find_document_by_key(self, kb_id, document_key, *, include_deleted=False):
        if not include_deleted and self.row.get("deleted_at"):
            return None
        return dict(self.row) if self.row.get("kb_id") == kb_id else None

    async def revive_document_from_storage(self, document_id, **kw):
        self.operations.append("revive")
        self.row.update(deleted_at=None, content_revision=2)
        return dict(self.row)

    async def insert_document_from_storage(self, **values):
        self.operations.append("insert")
        return {"id": "doc-new", "status": "uploaded", **values}


@pytest.mark.asyncio
async def test_delete_is_soft_and_idempotent_restore(tmp_path):
    from knowledge_mining.mining.file_management.repositories_memory import (
        MemoryStorageObjectRepository,
    )
    from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
    from knowledge_mining.mining.kb.services.document_service import DocumentService

    db = _FakeDb()
    svc = DocumentService(
        db,  # type: ignore[arg-type]
        object_store=FakeObjectStore(root_path=str(tmp_path / "o")),
        storage_objects=MemoryStorageObjectRepository(),
        source_bucket="kbs-source",
    )
    await svc.delete(document_id="doc-1", user_id="alice")
    assert db.operations == ["soft_delete"]  # 没有硬删路径
    assert db.row["deleted_at"] is not None

    # 软删后身份读取默认不可见（详情/下载/patch 入口都走它）
    assert await db.get_document_identity("doc-1") is None

    # restore 恢复
    restored = await svc.restore(document_id="doc-1", user_id="alice")
    assert db.operations == ["soft_delete", "restore"]
    assert restored["deleted_at"] is None


@pytest.mark.asyncio
async def test_reupload_after_soft_delete_revives_identity(tmp_path):
    """软删行占着唯一键——同名重传必须复活而非 409。"""
    from knowledge_mining.mining.file_management.repositories_memory import (
        MemoryStorageObjectRepository,
    )
    from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
    from knowledge_mining.mining.kb.services.document_service import DocumentService

    db = _FakeDb()
    svc = DocumentService(
        db,  # type: ignore[arg-type]
        object_store=FakeObjectStore(root_path=str(tmp_path / "o")),
        storage_objects=MemoryStorageObjectRepository(),
        source_bucket="kbs-source",
    )
    await svc.delete(document_id="doc-1", user_id="alice")
    doc = await svc.upload(
        kb_id="kb-1", owner_id="alice", filename="a.pdf", content=b"x",
    )
    assert db.operations == ["soft_delete", "revive"]  # 无 insert、无 409
    assert doc["content_revision"] == 2
