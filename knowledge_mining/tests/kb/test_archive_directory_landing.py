"""58号§2.5/2.6：归档上传的目录落位（含网页 ZIP 吞目录 bug 回归）。

- upload_archive_path(base_directory=…)：ZIP 落位 {base}/{包名}/…
- intake_upload 把 directory 透传进归档分支（57 号前的现存 bug：归档分支吞掉
  directory，网页在子文件夹传 ZIP 落到库根目录）
- 异步大包路径（_run_archive_task）同样携带 base_directory
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.kb.services import document_service as ds
from knowledge_mining.mining.kb.services.document_service import DocumentService


class _Db:
    def __init__(self):
        self.documents: list[dict] = []
        self.folders: dict[str, dict] = {}

    async def get_kb(self, kb_id):
        return {"id": kb_id, "domain": "generic"}

    async def is_visible(self, *, kb_id, user_id):
        return True

    async def can_write(self, *, kb_id, user_id):
        return True

    async def find_folder_by_path(self, kb_id, path):
        return self.folders.get(path)

    async def insert_folder(self, **values):
        folder = {"id": f"folder-{len(self.folders) + 1}", **values}
        self.folders[folder["path"]] = folder
        return folder

    async def find_document_by_location(self, kb_id, directory_path, document_name, *, include_deleted=False):
        return None

    async def find_document_by_key(self, kb_id, document_key, *, include_deleted=False):
        return None

    async def revive_document_from_storage(self, document_id, **kw):
        return None

    async def insert_document_from_storage(self, **values):
        doc = {"id": f"doc-{len(self.documents) + 1}", "status": "uploaded",
               "content_revision": 1, **values}
        self.documents.append(doc)
        return doc


def _svc(tmp_path) -> tuple[DocumentService, _Db]:
    db = _Db()
    svc = DocumentService(
        db,  # type: ignore[arg-type]
        object_store=FakeObjectStore(root_path=str(tmp_path / "objects")),
        storage_objects=MemoryStorageObjectRepository(),
        source_bucket="kbs-source",
    )
    return svc, db


def _zip(tmp_path: Path, name: str, members: dict[str, str]) -> Path:
    zp = tmp_path / name
    with zipfile.ZipFile(zp, "w") as zf:
        for rel, content in members.items():
            zf.writestr(rel, content)
    return zp


async def _stream_of(payload: bytes):
    yield payload


@pytest.mark.asyncio
async def test_archive_with_base_directory_lands_under_it(tmp_path):
    """ZIP 上传到指定目录：最终落位 {directory}/{压缩包名}/…（58号§2.5）。"""
    svc, db = _svc(tmp_path)
    db.folders["产品文档"] = {"id": "f-1", "path": "产品文档"}
    zp = _zip(tmp_path, "pack.zip", {"a.txt": "aaa", "sub/b.txt": "bbb"})

    await svc.upload_archive_path(
        kb_id="kb-1", owner_id="alice", archive_path=zp,
        archive_name="pack.zip", base_directory="产品文档",
    )
    dirs = sorted(d["directory_path"] for d in db.documents)
    assert dirs == ["产品文档/pack", "产品文档/pack/sub"]


@pytest.mark.asyncio
async def test_archive_root_landing_unchanged(tmp_path):
    """根目录（不传 base_directory）仍落 {压缩包名}/…——现状行为不回归。"""
    svc, db = _svc(tmp_path)
    zp = _zip(tmp_path, "pack.zip", {"a.txt": "aaa"})
    await svc.upload_archive_path(
        kb_id="kb-1", owner_id="alice", archive_path=zp, archive_name="pack.zip",
    )
    assert db.documents[0]["directory_path"] == "pack"


@pytest.mark.asyncio
async def test_intake_upload_forwards_directory_into_archive_branch(tmp_path):
    """bug 回归（58号§2.6）：intake_upload 归档分支必须透传 directory——
    此前 upload_archive_path 调用丢掉 directory，网页在子文件夹传 ZIP 落到库根。"""
    svc, db = _svc(tmp_path)
    db.folders["产品文档"] = {"id": "f-1", "path": "产品文档"}
    zp = _zip(tmp_path, "pack.zip", {"a.txt": "aaa"})

    result = await svc.intake_upload(
        kb_id="kb-1", owner_id="alice", filename="pack.zip",
        stream=_stream_of(zp.read_bytes()), directory_path="产品文档",
    )
    assert result["kind"] == "archive"
    assert db.documents[0]["directory_path"] == "产品文档/pack"


@pytest.mark.asyncio
async def test_intake_upload_normal_file_lands_in_directory(tmp_path):
    svc, db = _svc(tmp_path)
    db.folders["产品文档"] = {"id": "f-1", "path": "产品文档"}
    result = await svc.intake_upload(
        kb_id="kb-1", owner_id="alice", filename="手册.md",
        stream=_stream_of(b"# hi"), directory_path="产品文档",
    )
    assert result["kind"] == "file"
    assert result["document"]["directory_path"] == "产品文档"


@pytest.mark.asyncio
async def test_intake_upload_async_archive_carries_directory(tmp_path, monkeypatch):
    """大包异步路径：intake_upload 必须把 directory 传给 _run_archive_task。"""
    svc, db = _svc(tmp_path)
    members = {f"f{i}.txt": "x" for i in range(ds.SYNC_ARCHIVE_MEMBERS + 1)}
    zp = _zip(tmp_path, "big.zip", members)

    captured: dict = {}

    async def fake_run(task_id, svc_, **kw):
        captured.update(kw)

    monkeypatch.setattr(ds, "_run_archive_task", fake_run)
    import asyncio

    tasks: list = []
    real_create_task = asyncio.create_task

    def tracked(coro, **kw):
        task = real_create_task(coro, **kw)
        tasks.append(task)
        return task

    monkeypatch.setattr(ds.asyncio, "create_task", tracked)

    result = await svc.intake_upload(
        kb_id="kb-1", owner_id="alice", filename="big.zip",
        stream=_stream_of(zp.read_bytes()), directory_path="产品文档",
    )
    assert result["kind"] == "archive_task"
    # 等后台协程真跑完（fake 立即返回，一次 gather 即可）
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    assert captured.get("base_directory") == "产品文档"


@pytest.mark.asyncio
async def test_run_archive_task_forwards_base_directory(tmp_path, monkeypatch):
    """_run_archive_task → upload_archive_path 的 base_directory 透传。"""
    svc, db = _svc(tmp_path)
    captured: dict = {}

    async def fake_upload_archive(**kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(svc, "upload_archive_path", fake_upload_archive)
    await ds._run_archive_task(
        "arch_t", svc, kb_id="kb-1", owner_id="alice",
        archive_path=tmp_path / "x.zip", archive_name="x.zip",
        max_member_bytes=None, base_directory="产品文档",
    )
    assert captured.get("base_directory") == "产品文档"


@pytest.mark.asyncio
async def test_archive_base_directory_deleted_between_ticket_and_put(tmp_path):
    """codex P2-2：签票后目录被删——归档路径不得经 ensure_folder_path 把它重建
    （服务层解压前复核存在性；普通文件路径本就会被 assert_directory_exists 拒）。"""
    svc, db = _svc(tmp_path)  # folders 为空 = base_directory 不存在
    zp = _zip(tmp_path, "pack.zip", {"a.txt": "aaa"})
    with pytest.raises(ValueError, match="目标目录不存在"):
        await svc.upload_archive_path(
            kb_id="kb-1", owner_id="alice", archive_path=zp,
            archive_name="pack.zip", base_directory="已删除的目录",
        )
    assert db.documents == [] and db.folders == {}


@pytest.mark.asyncio
async def test_run_archive_task_runs_callback_before_complete(tmp_path, monkeypatch):
    """codex P2-6：先 on_complete（入队+note_mining）再置 completed——前端见
    completed 即停轮询，先 complete 会让 auto_mine 结果无人可见。"""
    from knowledge_mining.mining.kb.services import archive_tasks

    order: list[str] = []
    docs_holder: dict = {}

    class _FakeRegistry:
        def update(self, *a, **k):
            order.append("update")
        def get(self, task_id):
            order.append("get")
            return {"task_id": task_id, "status": "processing"}
        def complete(self, task_id, *, document_count, failed):
            order.append(f"complete:{document_count}")
        def fail(self, task_id, error):
            order.append("fail")
        def note_mining(self, task_id, auto):
            order.append("note_mining")

    monkeypatch.setattr(archive_tasks, "registry", _FakeRegistry())

    async def fake_upload(**kw):
        docs_holder["docs"] = [{"id": "d1"}, {"id": "d2"}]
        return docs_holder["docs"]

    svc, _db = _svc(tmp_path)
    monkeypatch.setattr(svc, "upload_archive_path", fake_upload)

    async def on_complete(task):
        order.append("callback")

    await ds._run_archive_task(
        "arch_t", svc, kb_id="kb-1", owner_id="alice",
        archive_path=tmp_path / "x.zip", archive_name="x.zip",
        max_member_bytes=None, on_complete=on_complete,
    )
    assert order == ["get", "callback", "complete:2"]
