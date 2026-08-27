"""批次2c：归档包本体存档 + 大包异步解压任务。

决策（2026-08-27）：①原始 zip/hdx/chm 包本体存 MinIO（可追溯/可重解压）；
②成员数超同步阈值（200）的大包异步解压——进程内任务注册表 + 状态查询端点
+ 进度回调，前端轮询；小包保持同步即时返回。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.kb.services.archive_tasks import ArchiveTaskRegistry
from knowledge_mining.mining.kb.services.document_service import (
    SYNC_ARCHIVE_MEMBERS,
    DocumentService,
)


class _Db:
    def __init__(self):
        self.documents: list[dict] = []

    async def get_kb(self, kb_id):
        return {"id": kb_id, "domain": "generic"}

    async def is_visible(self, *, kb_id, user_id):
        return True

    async def can_write(self, *, kb_id, user_id):
        return True

    async def find_folder_by_path(self, kb_id, path):
        return None

    async def insert_folder(self, **values):
        return {"id": f"folder-{len(values)}", **values}

    async def find_document_by_key(self, kb_id, document_key, *, include_deleted=False):
        return None

    async def revive_document_from_storage(self, document_id, **kw):
        return None

    async def insert_document_from_storage(self, **values):
        doc = {"id": f"doc-{len(self.documents) + 1}", "status": "uploaded",
               "content_revision": 1, **values}
        self.documents.append(doc)
        return doc


def _svc(tmp_path, store=None):
    db = _Db()
    store = store or FakeObjectStore(root_path=str(tmp_path / "objects"))
    svc = DocumentService(
        db,  # type: ignore[arg-type]
        object_store=store,
        storage_objects=MemoryStorageObjectRepository(),
        source_bucket="kbs-source",
    )
    return svc, db, store


@pytest.mark.asyncio
async def test_archive_itself_is_persisted_to_object_store(tmp_path):
    """包本体也存 MinIO（artifact_class=archive），可追溯。"""
    svc, db, store = _svc(tmp_path)
    zp = tmp_path / "pack.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("a.txt", "aaa")
    await svc.upload_archive_path(
        kb_id="kb-1", owner_id="alice", archive_path=zp, archive_name="pack.zip",
        persist_archive=True,
    )
    assert len(db.documents) == 1  # 成员 a.txt 一条文档
    # 对象登记两条：成员内容 + 包本体（内容寻址，不同 sha）
    assert len(svc._storage_objects._by_id) == 2  # noqa: SLF001


@pytest.mark.asyncio
async def test_progress_callback_reports_member_completion(tmp_path):
    svc, db, _ = _svc(tmp_path)
    zp = tmp_path / "pack.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("a.txt", "aaa")
        zf.writestr("sub/b.txt", "bbb")
    seen = []
    await svc.upload_archive_path(
        kb_id="kb-1", owner_id="alice", archive_path=zp, archive_name="pack.zip",
        on_progress=lambda done, total, name: seen.append((done, total, name)),
    )
    assert seen == [(1, 2, "a.txt"), (2, 2, "b.txt")]


def test_sync_threshold_is_200():
    assert SYNC_ARCHIVE_MEMBERS == 200


@pytest.mark.asyncio
async def test_count_zip_members_without_extraction(tmp_path):
    zp = tmp_path / "n.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        for i in range(7):
            zf.writestr(f"f{i}.txt", "x")
    from knowledge_mining.mining.kb.services.document_service import count_archive_members
    assert await count_archive_members(zp) == 7


def test_task_registry_lifecycle():
    reg = ArchiveTaskRegistry()
    tid = reg.create(kb_id="kb-1", archive_name="big.hdx")
    assert reg.get(tid)["status"] == "processing"
    reg.update(tid, done=10, total=100)
    assert reg.get(tid)["progress"] == {"done": 10, "total": 100}
    reg.complete(tid, document_count=100, failed=2)
    snap = reg.get(tid)
    assert snap["status"] == "completed"
    assert snap["document_count"] == 100 and snap["failed"] == 2
    reg.fail(tid, "boom")
    # complete 后 fail 不覆盖终态
    assert reg.get(tid)["status"] == "completed"


def test_task_registry_cap_and_eviction():
    reg = ArchiveTaskRegistry(max_entries=3)
    ids = [reg.create(kb_id="k", archive_name=f"a{i}.zip") for i in range(5)]
    assert len(reg.snapshot()) == 3  # 只留最近 3 个，防内存无界
    assert reg.get(ids[0]) is None
    assert reg.get(ids[4]) is not None
