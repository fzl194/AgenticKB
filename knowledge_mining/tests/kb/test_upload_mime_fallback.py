"""BUG-2（批次1）：上传 mime 必须有扩展名回落，不能盲信 multipart content_type。

实测症状：.md 经浏览器/curl 上传时 content_type 常为 application/octet-stream
（或空），盲信该值导致 storage object 记错 mime，解析链拒收。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.services.document_service import resolve_upload_mime


def test_generic_or_missing_declared_mime_falls_back_to_extension() -> None:
    assert resolve_upload_mime("notes.md", "application/octet-stream") == "text/markdown"
    assert resolve_upload_mime("notes.md", None) == "text/markdown"
    assert resolve_upload_mime("notes.md", "") == "text/markdown"
    assert resolve_upload_mime("readme.markdown", "application/octet-stream") == "text/markdown"
    assert resolve_upload_mime("data.txt", "application/octet-stream") == "text/plain"
    assert resolve_upload_mime("report.pdf", None) == "application/pdf"


def test_specific_declared_mime_is_preserved() -> None:
    # 浏览器明确给了正确类型时不覆盖
    assert resolve_upload_mime("a.md", "text/markdown") == "text/markdown"
    assert resolve_upload_mime("a.pdf", "application/pdf") == "application/pdf"
    # 声明与扩展名冲突但声明是具体类型：以声明为准（如 .bin 显式声明）
    assert resolve_upload_mime("weird.md", "application/x-my-format") == "application/x-my-format"


def test_unknown_extension_keeps_declared_or_octet_stream() -> None:
    assert resolve_upload_mime("data.xyz", "application/octet-stream") == "application/octet-stream"
    assert resolve_upload_mime("data.xyz", None) == "application/octet-stream"


@pytest.mark.asyncio
async def test_upload_records_resolved_mime_for_markdown(tmp_path) -> None:
    """端到端边界：octet-stream 声明的 .md 必须以 text/markdown 落进 storage object。"""
    from knowledge_mining.mining.file_management.repositories_memory import (
        MemoryStorageObjectRepository,
    )
    from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
    from knowledge_mining.mining.kb.services.document_service import DocumentService

    class _Db:
        async def get_kb(self, kb_id):
            return {"id": kb_id, "domain": "generic"}

        async def find_document_by_key(self, kb_id, document_key, *, include_deleted=False):
            return None

        async def is_visible(self, *, kb_id, user_id):
            return True

        async def can_write(self, *, kb_id, user_id):
            return True

        async def insert_document_from_storage(self, **values):
            return {"id": "doc-1", "status": "uploaded", **values}

    registry = MemoryStorageObjectRepository()
    service = DocumentService(
        _Db(),  # type: ignore[arg-type]
        object_store=FakeObjectStore(root_path=str(tmp_path / "objects")),
        storage_objects=registry,
        source_bucket="kbs-source",
    )

    document = await service.upload(
        kb_id="kb-1", owner_id="alice", filename="notes.md",
        content=b"# title\nbody", mime="application/octet-stream",
    )
    storage_object = await registry.get(document["storage_object_id"])
    assert storage_object is not None
    assert storage_object.mime == "text/markdown"
