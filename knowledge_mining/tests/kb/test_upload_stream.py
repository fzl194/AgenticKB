"""P01-S1（批次2）：KB 上传流式化 + 大小上限 + zip-bomb 防护。

原路径 `await file.read()` 整读（100MB 文件 = RSS +100MB，且无上限）；
zip 成员 `read_bytes()` 整读、解压器无额度。本批：分块流式 + 413 +
解压额度（成员数/单成员/总量）。
"""
from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import pytest

from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.kb.services.document_service import (
    DocumentService,
    UploadTooLarge,
)


class _Db:
    def __init__(self):
        self.documents: list[dict] = []
        self.by_key: dict[tuple, dict] = {}

    async def get_kb(self, kb_id):
        return {"id": kb_id, "domain": "generic"}

    async def is_visible(self, *, kb_id, user_id):
        return True

    async def can_write(self, *, kb_id, user_id):
        return True

    async def find_document_by_key(self, kb_id, document_key, *, include_deleted=False):
        row = self.by_key.get((kb_id, document_key))
        if row is None or (not include_deleted and row.get("deleted_at")):
            return None
        return row

    async def revive_document_from_storage(self, document_id, **kw):
        row = next(d for d in self.documents if d["id"] == document_id)
        row.update(deleted_at=None, content_revision=row.get("content_revision", 1) + 1)
        return row

    async def find_folder_by_path(self, kb_id, path):
        return None

    async def insert_folder(self, **values):
        return {"id": f"folder-{len(values)}", **values}

    async def insert_document_from_storage(self, **values):
        doc = {"id": f"doc-{len(self.documents) + 1}", "status": "uploaded",
               "content_revision": 1, **values}
        self.documents.append(doc)
        self.by_key[(values["kb_id"], values["document_key"])] = doc
        return doc


class _ChunkCountingStore(FakeObjectStore):
    """put_stream 时记录分块数——证明 service 侧真在流式喂。"""

    def __init__(self, root_path: str):
        super().__init__(root_path=root_path)
        self.chunk_counts: list[int] = []

    async def put_stream(self, location, stream, options):
        chunks = []
        async for c in stream:
            chunks.append(c)
        self.chunk_counts.append(len(chunks))

        async def replay():
            for c in chunks:
                yield c

        return await super().put_stream(location, replay(), options)


def _svc(tmp_path, db=None, store=None) -> tuple[DocumentService, _Db, _ChunkCountingStore]:
    db = db or _Db()
    store = store or _ChunkCountingStore(str(tmp_path / "objects"))
    svc = DocumentService(
        db,  # type: ignore[arg-type]
        object_store=store,
        storage_objects=MemoryStorageObjectRepository(),
        source_bucket="kbs-source",
    )
    return svc, db, store


async def _gen(total: int, chunk: int = 64 * 1024):
    payload = b"z" * chunk
    sent = 0
    while sent < total:
        n = min(chunk, total - sent)
        yield payload[:n]
        sent += n


@pytest.mark.asyncio
async def test_upload_stream_feeds_put_stream_in_chunks(tmp_path):
    """service 层不物化整包：put_stream 收到多个分块。"""
    svc, db, store = _svc(tmp_path)
    doc = await svc.upload_stream(
        kb_id="kb-1", owner_id="alice", filename="big.bin",
        stream=_gen(1024 * 1024, chunk=64 * 1024),  # 1MB / 64KB = 16 块
        mime="application/octet-stream", max_bytes=100 * 1024 * 1024,
    )
    assert doc["status"] == "uploaded"
    assert store.chunk_counts and store.chunk_counts[0] >= 2  # 多块=流式（256KB 重读）
    assert db.documents[0]["file_size"] == 1024 * 1024


@pytest.mark.asyncio
async def test_upload_stream_rejects_oversize_with_413_semantics(tmp_path):
    svc, db, store = _svc(tmp_path)
    with pytest.raises(UploadTooLarge) as ei:
        await svc.upload_stream(
            kb_id="kb-1", owner_id="alice", filename="big.bin",
            stream=_gen(10 * 1024 * 1024), max_bytes=1024 * 1024,
        )
    assert ei.value.limit_bytes == 1024 * 1024
    assert db.documents == []  # 超限不落库
    assert store.chunk_counts == []  # 也没碰对象存储


@pytest.mark.asyncio
async def test_upload_stream_dedups_identical_content(tmp_path):
    svc, db, store = _svc(tmp_path)
    await svc.upload_stream(kb_id="kb-1", owner_id="alice", filename="a.txt",
                            stream=_gen(4096, 1024), max_bytes=10**6)
    await svc.upload_stream(kb_id="kb-1", owner_id="alice", filename="b.txt",
                            stream=_gen(4096, 1024), max_bytes=10**6)
    assert db.documents[0]["storage_object_id"] == db.documents[1]["storage_object_id"]


@pytest.mark.asyncio
async def test_upload_bytes_wrapper_still_works(tmp_path):
    """旧签名（bytes）保留为薄包装——既有调用方/测试零改动。"""
    svc, db, store = _svc(tmp_path)
    doc = await svc.upload(kb_id="kb-1", owner_id="alice", filename="a.txt",
                           content=b"hello world")
    assert doc["status"] == "uploaded"


@pytest.mark.asyncio
async def test_upload_zip_members_streamed_with_limits(tmp_path):
    svc, db, store = _svc(tmp_path)
    zp = tmp_path / "pack.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("dir/a.txt", "aaaa" * (256 * 1024))
        zf.writestr("dir/b.txt", "bbbb" * (256 * 1024))
    docs = await svc.upload_zip_path(
        kb_id="kb-1", owner_id="alice", zip_path=zp,
        max_archive_bytes=10 * 1024 * 1024,
        max_member_bytes=1024 * 1024,
    )
    names = sorted(d["document_name"] for d in docs)
    assert names == ["a.txt", "b.txt"]
    # 成员从磁盘流式喂给 put_stream（不止 1 块）
    assert all(c > 1 for c in store.chunk_counts)


@pytest.mark.asyncio
async def test_upload_zip_rejects_oversize_member(tmp_path):
    svc, db, store = _svc(tmp_path)
    zp = tmp_path / "bomb.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("small.txt", "ok")
        zf.writestr("huge.txt", "h" * (2 * 1024 * 1024))  # 2MB 超单成员上限
    # 解压期发现超限 → ValueError（路由 400，错误信息含上限值）
    with pytest.raises(ValueError, match="单成员上限"):
        await svc.upload_zip_path(
            kb_id="kb-1", owner_id="alice", zip_path=zp,
            max_archive_bytes=10 * 1024 * 1024,
            max_member_bytes=1024 * 1024,
        )


# ── 解压器额度（zip-bomb 防护） ─────────────────────────────────────────────

def _make_zip(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def test_extract_zip_enforces_total_budget(tmp_path):
    from knowledge_mining.mining.infra.archive_extractor import extract_zip

    zp = tmp_path / "bomb.zip"
    _make_zip(zp, {f"f{i}.txt": "x" * (600 * 1024) for i in range(3)})  # 总量 1.8MB
    result = extract_zip(zp, tmp_path / "out",
                         max_total_bytes=1024 * 1024)
    assert result.error and ("总量" in result.error or "total" in result.error.lower())


def test_extract_zip_enforces_member_count(tmp_path):
    from knowledge_mining.mining.infra.archive_extractor import extract_zip

    zp = tmp_path / "many.zip"
    _make_zip(zp, {f"f{i}.txt": "x" for i in range(10)})
    result = extract_zip(zp, tmp_path / "out", max_members=5)
    assert result.error


def test_extract_zip_enforces_single_member_size(tmp_path):
    from knowledge_mining.mining.infra.archive_extractor import extract_zip

    zp = tmp_path / "one_big.zip"
    _make_zip(zp, {"big.txt": "x" * (2 * 1024 * 1024)})
    result = extract_zip(zp, tmp_path / "out", max_member_bytes=1024 * 1024)
    assert result.error


def test_extract_zip_within_budget_still_works(tmp_path):
    from knowledge_mining.mining.infra.archive_extractor import extract_zip

    zp = tmp_path / "ok.zip"
    _make_zip(zp, {"a/b.txt": "hello", "c.txt": "world"})
    result = extract_zip(zp, tmp_path / "out",
                         max_members=10, max_member_bytes=1024, max_total_bytes=4096)
    assert result.error is None
    assert sorted(result.extracted_files) == ["a/b.txt", "c.txt"]
