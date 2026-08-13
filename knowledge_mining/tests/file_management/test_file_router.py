"""HTTP router tests for the File Management surface (M1.3; ADR-0003 D-023).

Uses FastAPI ``TestClient`` with ``app.dependency_overrides`` injecting the
in-memory fakes + ``FakeObjectStore``. No PostgreSQL, no MinIO, no network.

Coverage:
- GET documents / document (200 + body shape)
- POST download-url (200; MISSING object -> 409)
- PATCH rename (200)
- DELETE soft_delete (200; list hides it)
- POST restore (200)
- PUT content replace (200, revision+1)
- PUT content stale revision -> 409
- error-mapping smoke: 404 (unknown doc), 422 (checksum), 503 (storage
  unavailable) via an injected misbehaving store.
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Windows: psycopg-async needs the SelectorEventLoop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_mining.mining.contracts.storage.errors import (  # noqa: E402
    ChecksumMismatch,
    StorageObjectMissing,
    StorageUnavailable,
)
from knowledge_mining.mining.file_management.file_service import (  # noqa: E402
    FileManagementService,
    FileManagementServiceConfig,
)
from knowledge_mining.mining.file_management.repositories_memory import (  # noqa: E402
    MemoryDocumentCurrentContentRepository,
    MemoryFileAuditRepository,
    MemoryQuotaRepository,
    MemoryStorageObjectRepository,
    MemoryUploadSessionRepository,
)
from knowledge_mining.mining.file_management.router import (  # noqa: E402
    get_file_management_service,
    get_upload_session_service,
    router as file_management_router,
)
from knowledge_mining.mining.file_management.service import (  # noqa: E402
    UploadSessionService,
    UploadSessionServiceConfig,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def app_env():
    """Wire a fresh FastAPI app + fakes; return (app, client, handles)."""
    root = tempfile.mkdtemp(prefix="fmrouter_")
    store = FakeObjectStore(root)
    sessions = MemoryUploadSessionRepository()
    storage_objects = MemoryStorageObjectRepository()
    documents = MemoryDocumentCurrentContentRepository()
    audits = MemoryFileAuditRepository()
    quotas = MemoryQuotaRepository()
    quotas.seed("kb1", 10_000_000)

    up_svc = UploadSessionService(
        object_store=store, sessions=sessions, storage_objects=storage_objects,
        documents=documents, audits=audits, quotas=quotas,
        config=UploadSessionServiceConfig(),
    )
    fm_svc = FileManagementService(
        object_store=store, documents=documents, storage_objects=storage_objects,
        audits=audits, quotas=quotas, sessions=sessions,
        config=FileManagementServiceConfig(),
    )

    app = FastAPI()
    app.include_router(file_management_router)
    app.dependency_overrides[get_file_management_service] = lambda: fm_svc
    app.dependency_overrides[get_upload_session_service] = lambda: up_svc

    handles = type(
        "H",
        (),
        {
            "store": store, "sessions": sessions, "storage_objects": storage_objects,
            "documents": documents, "audits": audits, "quotas": quotas,
            "up_svc": up_svc, "fm_svc": fm_svc,
        },
    )
    return app, TestClient(app), handles


def _seed(client, env, *, filename="doc.txt", data=b"seed body") -> str:
    """Drive an upload session to COMMITTED via HTTP; return document_id.

    ``env`` is the handles object returned as ``app_env[2]``.
    """
    up_svc = env.up_svc
    loop = asyncio.new_event_loop()
    try:
        session, _ = loop.run_until_complete(
            up_svc.initiate(
                kb_id="kb1", folder_id=None, actor="u1", filename=filename,
                expected_size=len(data), expected_mime="text/plain",
                idempotency_key=f"ik-{filename}-{_sha256(data)[:8]}",
            )
        )
        loop.run_until_complete(up_svc.stage_from_bytes(session.id, data))
        result = loop.run_until_complete(up_svc.complete(session.id))
        return result.document_id
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# GET endpoints
# ---------------------------------------------------------------------------


def test_list_documents_returns_items(app_env):
    _, client, env = app_env
    _seed(client, env, filename="a.txt", data=b"AAA")
    r = client.get("/api/kb/kb1/documents")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["kb_id"] == "kb1"
    assert item["size"] == 3
    assert item["mime"] == "text/plain"


def test_get_document_returns_full_view(app_env):
    _, client, env = app_env
    doc_id = _seed(client, env, data=b"hello")
    r = client.get(f"/api/kb/kb1/documents/{doc_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["document_id"] == doc_id
    assert body["content_revision"] == 1


def test_get_unknown_document_returns_404(app_env):
    _, client, _ = app_env
    r = client.get("/api/kb/kb1/documents/doc_nope")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# download-url
# ---------------------------------------------------------------------------


def test_download_url_ok(app_env):
    _, client, env = app_env
    doc_id = _seed(client, env, data=b"dl")
    r = client.post(
        f"/api/kb/kb1/documents/{doc_id}/download-url",
        json={"expires_seconds": 120},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "GET"
    assert body["expires_in_seconds"] == 120


def test_download_url_missing_object_returns_409(app_env):
    _, client, env = app_env
    doc_id = _seed(client, env, data=b"gone")
    # Evict the storage object row to simulate a MISSING integrity incident.
    env.storage_objects._by_id.pop(
        env.documents._docs[doc_id]["storage_object_id"], None
    )
    r = client.post(
        f"/api/kb/kb1/documents/{doc_id}/download-url",
        json={"expires_seconds": 120},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# PATCH rename / move
# ---------------------------------------------------------------------------


def test_rename_ok(app_env):
    _, client, env = app_env
    doc_id = _seed(client, env, filename="orig.txt", data=b"x")
    r = client.patch(
        f"/api/kb/kb1/documents/{doc_id}",
        json={"document_name": "renamed.txt"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "renamed.txt"


def test_rename_unknown_returns_404(app_env):
    _, client, _ = app_env
    r = client.patch(
        "/api/kb/kb1/documents/doc_nope",
        json={"document_name": "x"},
    )
    assert r.status_code == 404


def test_move_ok(app_env):
    _, client, env = app_env
    doc_id = _seed(client, env, data=b"x")
    r = client.post(
        f"/api/kb/kb1/documents/{doc_id}/move",
        json={"target_folder_id": "fold_1"},
    )
    assert r.status_code == 200
    assert r.json()["folder_id"] == "fold_1"


# ---------------------------------------------------------------------------
# DELETE soft_delete / restore
# ---------------------------------------------------------------------------


def test_soft_delete_then_restore(app_env):
    _, client, env = app_env
    doc_id = _seed(client, env, data=b"bye")
    r = client.delete(f"/api/kb/kb1/documents/{doc_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Default list hides the soft-deleted doc.
    items = client.get("/api/kb/kb1/documents").json()["items"]
    assert all(i["document_id"] != doc_id for i in items)

    # Restore re-shows it.
    r2 = client.post(f"/api/kb/kb1/documents/{doc_id}/restore")
    assert r2.status_code == 200
    assert r2.json()["deleted_at"] is None

    items_after = client.get("/api/kb/kb1/documents").json()["items"]
    assert any(i["document_id"] == doc_id for i in items_after)


# ---------------------------------------------------------------------------
# PUT content replace
# ---------------------------------------------------------------------------


def test_replace_content_ok(app_env):
    _, client, env = app_env
    doc_id = _seed(client, env, data=b"v1")
    r = client.put(
        f"/api/kb/kb1/documents/{doc_id}/content",
        params={"expected_revision": 1, "mime": "text/plain", "actor": "u1"},
        content=b"v2 brand new body",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["content_revision"] == 2
    assert body["raw_hash"] == _sha256(b"v2 brand new body")


def test_replace_content_stale_revision_returns_409(app_env):
    _, client, env = app_env
    doc_id = _seed(client, env, data=b"v1")
    r = client.put(
        f"/api/kb/kb1/documents/{doc_id}/content",
        params={"expected_revision": 99, "mime": "text/plain", "actor": "u1"},
        content=b"conflict",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Error-mapping smoke (503 storage unavailable, 422 checksum)
# ---------------------------------------------------------------------------


class _UnavailableStore:
    """ObjectStorePort stub whose presign_get raises StorageUnavailable.

    Only the methods the download-url path touches are implemented.
    """

    provider = "fake"

    async def presign_get(self, location, expires_in_seconds=900):
        raise StorageUnavailable("simulated outage")

    async def put_stream(self, location, stream, options):
        raise StorageUnavailable("simulated outage")


def test_storage_unavailable_maps_to_503(app_env):
    app, client, env = app_env
    doc_id = _seed(client, env, data=b"uhoh")
    # Swap the object store for one that is unavailable.
    env.fm_svc._store = _UnavailableStore()
    r = client.post(
        f"/api/kb/kb1/documents/{doc_id}/download-url",
        json={"expires_seconds": 120},
    )
    assert r.status_code == 503


class _ChecksumStore:
    """ObjectStorePort stub whose put_stream raises ChecksumMismatch."""

    provider = "fake"

    async def put_stream(self, location, stream, options):
        raise ChecksumMismatch("forced", expected="x", actual="y")

    async def copy(self, src, dst, options):
        raise ChecksumMismatch("forced", expected="x", actual="y")

    async def delete(self, location):
        return None


def test_checksum_mismatch_maps_to_422(app_env):
    app, client, env = app_env
    doc_id = _seed(client, env, data=b"v1")
    env.fm_svc._store = _ChecksumStore()
    r = client.put(
        f"/api/kb/kb1/documents/{doc_id}/content",
        params={"expected_revision": 1, "mime": "text/plain", "actor": "u1"},
        content=b"bad bytes",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# upload-session routes (initiate + complete) end-to-end over HTTP
# ---------------------------------------------------------------------------


def test_initiate_upload_session_returns_201(app_env):
    _, client, _ = app_env
    r = client.post(
        "/api/kb/kb1/upload-sessions",
        json={
            "folder_id": None,
            "actor": "u1",
            "filename": "upload.txt",
            "expected_size": 5,
            "expected_mime": "text/plain",
            "idempotency_key": "router-ik-1",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["session"]["state"] == "INITIATED"
    assert body["presigned_put"]["method"] == "PUT"
