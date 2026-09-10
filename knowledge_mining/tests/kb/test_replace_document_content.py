"""Replace a current object without deleting identity or the serving knowledge."""
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI

from knowledge_mining.mining.file_management.repositories_memory import MemoryStorageObjectRepository
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.deps import get_document_service
from knowledge_mining.mining.kb.routes.documents import router
from knowledge_mining.mining.kb.services.document_service import DocumentService, UploadTooLarge
from knowledge_mining.mining.kb.services.kb_service import Forbidden, NotFound

pytestmark = pytest.mark.asyncio


class ReplaceDb:
    def __init__(self):
        self.doc = dict(id="d1", kb_id="k1", document_name="manual.txt",
                        document_key="doc:/manual.txt", directory_path="docs",
                        storage_object_id="old-object", source_raw_hash="old-hash",
                        content_revision=1, storage_path=None, file_size=3)
        self.serving = {"document_snapshot_id": "old-snapshot", "build_id": "old-build"}
        self.writable = True
        self.conflict = False

    async def get_document_identity(self, document_id, **kwargs):
        return deepcopy(self.doc) if document_id == "d1" else None

    async def get_kb(self, kb_id):
        return {"id": "k1", "domain": "default", "status": "active"}

    async def is_visible(self, **kwargs):
        return True

    async def can_write(self, **kwargs):
        return self.writable

    async def replace_document_content(self, document_id, **values):
        if self.conflict or values["expected_revision"] != self.doc["content_revision"]:
            return None
        if values.get("expected_document_name") is not None and values["expected_document_name"] != self.doc["document_name"]:
            return None
        self.doc = {**self.doc, **{k: values[k] for k in (
            "storage_object_id", "source_raw_hash", "file_size")},
            "content_revision": self.doc["content_revision"] + 1}
        return deepcopy(self.doc)


def make_service(tmp_path):
    db = ReplaceDb()
    objects = MemoryStorageObjectRepository()
    service = DocumentService(db, object_store=FakeObjectStore(str(tmp_path / "objects")),
                              storage_objects=objects, source_bucket="test-source")
    return service, db, objects


async def chunks(data=b"new manual"):
    yield data[:3]
    yield data[3:]


async def replace(service, **overrides):
    args = dict(kb_id="k1", document_id="d1", user_id="writer",
                filename="updated.txt", expected_revision=1, stream=chunks(), max_bytes=100)
    return await service.replace_content(**{**args, **overrides})


async def test_replace_preserves_identity_location_and_serving_version(tmp_path):
    service, db, objects = make_service(tmp_path)
    before = deepcopy(db.doc)
    updated = await replace(service)
    for key in ("id", "kb_id", "document_key", "document_name", "directory_path", "storage_path"):
        assert updated[key] == before[key]
    assert updated["content_revision"] == 2
    assert updated["storage_object_id"] != before["storage_object_id"]
    obj = await objects.get(updated["storage_object_id"])
    assert obj.state == "AVAILABLE" and obj.mime == "text/plain"
    assert db.serving == {"document_snapshot_id": "old-snapshot", "build_id": "old-build"}
    name, mime, body = await service.download_object(document_id="d1", user_id="writer")
    assert name == "manual.txt" and b"".join([part async for part in body]) == b"new manual"


@pytest.mark.parametrize("changes,error", [
    ({"kb_id": "other"}, NotFound),
    ({"document_id": "missing"}, NotFound),
    ({"filename": "other.pdf"}, ValueError),
    ({"expected_revision": -1}, ValueError),
])
async def test_invalid_replace_does_not_change_document(tmp_path, changes, error):
    service, db, _ = make_service(tmp_path)
    before = deepcopy(db.doc)
    with pytest.raises(error):
        await replace(service, **changes)
    assert db.doc == before


async def test_viewer_cannot_upload_replacement_bytes(tmp_path):
    service, db, _ = make_service(tmp_path)
    db.writable = False

    async def forbidden_stream():
        pytest.fail("unauthorized content must not be read")
        yield b""

    with pytest.raises(Forbidden):
        await replace(service, stream=forbidden_stream())


@pytest.mark.parametrize("early", [True, False])
async def test_stale_revision_returns_conflict_without_switching_pointer(tmp_path, early):
    from knowledge_mining.mining.kb.services.document_service import ContentRevisionConflict
    service, db, _ = make_service(tmp_path)
    db.conflict = not early
    if early:
        db.doc = {**db.doc, "content_revision": 2}
    before = deepcopy(db.doc)
    with pytest.raises(ContentRevisionConflict):
        await replace(service)
    assert db.doc == before


async def test_replacement_size_limit_keeps_old_pointer(tmp_path):
    service, db, _ = make_service(tmp_path)
    before = deepcopy(db.doc)
    with pytest.raises(UploadTooLarge):
        await replace(service, max_bytes=4)
    assert db.doc == before


async def test_rename_during_upload_does_not_bind_bytes_to_another_format(tmp_path):
    from knowledge_mining.mining.kb.services.document_service import ContentRevisionConflict
    service, db, _ = make_service(tmp_path)

    async def racing_stream():
        yield b"first chunk"
        db.doc = {**db.doc, "document_name": "manual.pdf"}
        yield b"second chunk"

    with pytest.raises(ContentRevisionConflict):
        await replace(service, stream=racing_stream())
    assert db.doc["storage_object_id"] == "old-object"
    assert db.doc["document_name"] == "manual.pdf"


async def test_http_replace_then_stale_retry_and_read(tmp_path):
    service, db, _ = make_service(tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: {"id": "writer"}
    app.dependency_overrides[get_document_service] = lambda: service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        url = "/api/kb/k1/documents/d1/content"
        response = await client.post(url, data={"expected_revision": "1"},
                                     files={"file": ("updated.txt", b"new manual", "text/plain")})
        assert response.status_code == 200, response.text
        assert response.json()["content_revision"] == 2
        conflict = await client.post(url, data={"expected_revision": "1"},
                                     files={"file": ("updated.txt", b"stale", "text/plain")})
        assert conflict.status_code == 409
        invalid = await client.post(url, data={"expected_revision": "-1"},
                                    files={"file": ("updated.txt", b"bad", "text/plain")})
        assert invalid.status_code == 422
        wrong_kb = await client.post("/api/kb/other/documents/d1/content",
                                     data={"expected_revision": "2"},
                                     files={"file": ("updated.txt", b"bad", "text/plain")})
        assert wrong_kb.status_code == 404
        download = await client.get("/api/kb/k1/documents/d1/download")
        assert download.status_code == 200 and download.content == b"new manual"
    assert db.serving["document_snapshot_id"] == "old-snapshot"
