"""Unit coverage for the legacy KB upload route's object-storage boundary.

These tests intentionally use the filesystem-backed fake object store and
memory registry: no PostgreSQL or MinIO service is required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.kb.services.document_service import DocumentService


pytestmark = pytest.mark.asyncio


class _KbUploadDb:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def get_kb(self, kb_id: str) -> dict | None:
        return {"id": kb_id, "domain": "cloud_core_network"}

    async def is_visible(self, *, kb_id: str, user_id: str) -> bool:
        return True

    async def can_write(self, *, kb_id: str, user_id: str) -> bool:
        return True

    async def find_document_by_key(self, kb_id, document_key, *, include_deleted=False):
        return None

    async def insert_document_from_storage(self, **values: object) -> dict:
        document = {
            "id": f"doc-{len(self.documents) + 1}",
            "storage_path": None,
            "content_revision": 1,
            **values,
        }
        self.documents.append(document)
        return document


async def test_upload_registers_available_object_and_document_pointer(tmp_path: Path):
    db = _KbUploadDb()
    registry = MemoryStorageObjectRepository()
    service = DocumentService(
        db,  # type: ignore[arg-type]
        object_store=FakeObjectStore(root_path=str(tmp_path / "objects")),
        storage_objects=registry,
        source_bucket="agentickb-dev-source",
    )

    document = await service.upload(
        kb_id="kb-1", owner_id="alice", filename="report.txt", content=b"hello",
        directory_path="network",
    )

    storage_object = await registry.get(document["storage_object_id"])
    assert storage_object is not None
    assert document["storage_path"] is None
    assert document["source_raw_hash"] == storage_object.sha256
    assert document["content_revision"] == 1
    assert storage_object.bucket == "agentickb-dev-source"
    assert storage_object.state == "AVAILABLE"
    assert storage_object.object_key.startswith("v1/")
    assert not (tmp_path / "uploads").exists()


async def test_upload_reuses_registered_content_addressed_object(tmp_path: Path):
    db = _KbUploadDb()
    registry = MemoryStorageObjectRepository()
    service = DocumentService(
        db,  # type: ignore[arg-type]
        object_store=FakeObjectStore(root_path=str(tmp_path / "objects")),
        storage_objects=registry,
        source_bucket="agentickb-dev-source",
    )

    first = await service.upload(
        kb_id="kb-1", owner_id="alice", filename="first.txt", content=b"same",
    )
    second = await service.upload(
        kb_id="kb-1", owner_id="alice", filename="second.txt", content=b"same",
    )

    assert first["storage_object_id"] == second["storage_object_id"]
    assert len(db.documents) == 2
