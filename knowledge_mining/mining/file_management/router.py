"""FastAPI router for the File Management surface (M1.3; ADR-0003 D-023).

Exposes the SRS §C01 resource boundary on top of
:class:`FileManagementService` and the M1.2 :class:`UploadSessionService`.
This is a NEW, parallel link (writes ``storage_object_id``); the legacy
``kb/routes/documents`` surface continues to write ``storage_path`` and is
untouched here (SRS §2.3 migration-period coexistence).

Routes (path style mirrors ``kb/routes``):
    GET    /api/kb/{kb_id}/documents
    GET    /api/kb/{kb_id}/documents/{document_id}
    POST   /api/kb/{kb_id}/documents/{document_id}/download-url
    PUT    /api/kb/{kb_id}/documents/{document_id}/content        # replace_content
    PATCH  /api/kb/{kb_id}/documents/{document_id}                # rename
    POST   /api/kb/{kb_id}/documents/{document_id}/move
    DELETE /api/kb/{kb_id}/documents/{document_id}                # soft_delete
    POST   /api/kb/{kb_id}/documents/{document_id}/restore
    POST   /api/kb/{kb_id}/upload-sessions                        # initiate
    POST   /api/kb/{kb_id}/upload-sessions/{session_id}/complete

Authorization (read / write) is delegated to the caller's dependency chain
(``current_user`` + KB-membership enforcement). The router focuses on
error-to-HTTP mapping (SRS §C01).

The production service factory wires PG repos + the MinIO object store from
``app.state``; tests inject memory repos + ``FakeObjectStore`` via
``app.dependency_overrides[get_file_management_service] = ...``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from knowledge_mining.mining.contracts.file_management import (
    DocumentRevisionConflict,
    QuotaExceeded,
    UploadIncomplete,
    UploadSessionExpired,
)
from knowledge_mining.mining.contracts.storage.errors import (
    ChecksumMismatch,
    StorageError,
    StorageObjectMissing,
    StorageUnavailable,
)
from knowledge_mining.mining.file_management.file_service import (
    DocumentView,
    FileManagementService,
    FileManagementServiceError,
    Forbidden,
    NotFound,
)
from knowledge_mining.mining.file_management.service import UploadSessionService

router = APIRouter(prefix="/api/kb/{kb_id}", tags=["file-management"])


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class DownloadUrlRequest(BaseModel):
    expires_seconds: int = 900


class ReplaceContentRequest(BaseModel):
    """JSON metadata for a replace-content call.

    The new object bytes are streamed in the request body (raw PUT), and the
    declared ``expected_revision`` / ``mime`` are provided as query/body
    fields. Kept as a body model so the route signature stays explicit.
    """

    expected_revision: int
    mime: str | None = None


class RenameRequest(BaseModel):
    document_name: str


class MoveRequest(BaseModel):
    target_folder_id: str | None = None


class InitiateUploadRequest(BaseModel):
    folder_id: str | None = None
    actor: str
    filename: str
    expected_size: int
    expected_mime: str | None = None
    idempotency_key: str


class CompleteUploadRequest(BaseModel):
    expected_sha256: str | None = None
    mime: str | None = None
    document_id: str | None = None
    owner_id: str | None = None
    document_type: str | None = None
    actor: str = ""


# ---------------------------------------------------------------------------
# Error -> HTTP mapping (SRS §C01)
# ---------------------------------------------------------------------------


def _map_error(exc: Exception) -> HTTPException:
    """Translate a service/storage error into an HTTPException (SRS §C01).

    Mapping table:
      DocumentRevisionConflict -> 409
      UploadSessionExpired     -> 410
      UploadIncomplete         -> 409
      QuotaExceeded            -> 413
      ChecksumMismatch         -> 422
      StorageObjectMissing     -> 409
      StorageUnavailable       -> 503
      NotFound                 -> 404
      Forbidden                -> 403
      FileManagementService*   -> 400 (generic business error)
      other StorageError       -> 502 (unexpected adapter failure)
    """
    if isinstance(exc, NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc) or "not found")
    if isinstance(exc, Forbidden):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc) or "forbidden")
    if isinstance(exc, DocumentRevisionConflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, UploadSessionExpired):
        return HTTPException(status.HTTP_410_GONE, str(exc))
    if isinstance(exc, UploadIncomplete):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, StorageObjectMissing):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, QuotaExceeded):
        return HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc))
    if isinstance(exc, ChecksumMismatch):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    if isinstance(exc, StorageUnavailable):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    if isinstance(exc, FileManagementServiceError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if isinstance(exc, StorageError):
        # Adapter failure not otherwise classified.
        return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


def _view_to_dict(view: DocumentView) -> dict[str, Any]:
    return {
        "document_id": view.document_id,
        "kb_id": view.kb_id,
        "folder_id": view.folder_id,
        "name": view.name,
        "mime": view.mime,
        "size": view.size,
        "content_revision": view.content_revision,
        "storage_object_id": view.storage_object_id,
        "raw_hash": view.raw_hash,
        "deleted_at": view.deleted_at,
    }


# ---------------------------------------------------------------------------
# Dependency factories
# ---------------------------------------------------------------------------


async def get_file_management_service(request: Request) -> FileManagementService:
    """Production factory: wire FileManagementService from app.state.

    Expects the M1.2/M1.3 composition to have populated
    ``app.state.file_management_service``. Tests override this dependency with
    an in-memory-wired instance (see ``test_file_router.py``).
    """
    svc: FileManagementService | None = getattr(
        request.app.state, "file_management_service", None
    )
    if svc is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "File management service is not configured on this instance",
        )
    return svc


async def get_upload_session_service(request: Request) -> UploadSessionService:
    """Production factory: wire UploadSessionService from app.state."""
    svc: UploadSessionService | None = getattr(
        request.app.state, "upload_session_service", None
    )
    if svc is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Upload session service is not configured on this instance",
        )
    return svc


# ---------------------------------------------------------------------------
# Document directory + lifecycle routes
# ---------------------------------------------------------------------------


@router.get("/documents")
async def list_documents(
    kb_id: str,
    folder_id: str | None = None,
    include_deleted: bool = False,
    svc: FileManagementService = Depends(get_file_management_service),
) -> dict[str, Any]:
    """List documents in a KB (SRS §4.3A). Soft-deleted rows hidden by default."""
    try:
        views = await svc.list_documents(
            kb_id, folder_id=folder_id, include_deleted=include_deleted
        )
    except FileManagementServiceError as exc:
        raise _map_error(exc) from None
    return {"items": [_view_to_dict(v) for v in views]}


@router.get("/documents/{document_id}")
async def get_document(
    kb_id: str,
    document_id: str,
    svc: FileManagementService = Depends(get_file_management_service),
) -> dict[str, Any]:
    """Return a single document with current content + size/mime."""
    try:
        view = await svc.get_document(document_id)
    except FileManagementServiceError as exc:
        raise _map_error(exc) from None
    return _view_to_dict(view)


@router.post("/documents/{document_id}/download-url")
async def create_download_url(
    kb_id: str,
    document_id: str,
    body: DownloadUrlRequest,
    svc: FileManagementService = Depends(get_file_management_service),
) -> dict[str, Any]:
    """Mint a short-lived GET URL for the document's current object."""
    try:
        access = await svc.download_url(
            document_id, expires_seconds=body.expires_seconds
        )
    except (FileManagementServiceError, StorageError) as exc:
        raise _map_error(exc) from None
    return {
        "method": access.method,
        "url": access.url,
        "expires_in_seconds": access.expires_in_seconds,
        "bucket": access.location.bucket,
        "object_key": access.location.object_key,
    }


@router.put("/documents/{document_id}/content")
async def replace_content(
    kb_id: str,
    document_id: str,
    request: Request,
    expected_revision: int,
    mime: str | None = None,
    actor: str = "system",
    svc: FileManagementService = Depends(get_file_management_service),
) -> dict[str, Any]:
    """Online content replace — copy-on-write (SRS §4.3A replace_content).

    The request body is the raw new object bytes (streamed).
    """
    try:
        view = await svc.replace_content(
            document_id,
            stream=_request_stream(request),
            expected_revision=expected_revision,
            mime=mime,
            actor=actor,
        )
    except (FileManagementServiceError, StorageError) as exc:
        raise _map_error(exc) from None
    return _view_to_dict(view)


@router.patch("/documents/{document_id}")
async def patch_document(
    kb_id: str,
    document_id: str,
    body: RenameRequest,
    actor: str = "system",
    svc: FileManagementService = Depends(get_file_management_service),
) -> dict[str, Any]:
    """Rename a document — directory row only (SRS §4.3A rename)."""
    try:
        view = await svc.rename(
            document_id, new_name=body.document_name, actor=actor
        )
    except FileManagementServiceError as exc:
        raise _map_error(exc) from None
    return _view_to_dict(view)


@router.post("/documents/{document_id}/move")
async def move_document(
    kb_id: str,
    document_id: str,
    body: MoveRequest,
    actor: str = "system",
    svc: FileManagementService = Depends(get_file_management_service),
) -> dict[str, Any]:
    """Move a document — directory row only (SRS §4.3A move)."""
    try:
        view = await svc.move(
            document_id, target_folder_id=body.target_folder_id, actor=actor
        )
    except FileManagementServiceError as exc:
        raise _map_error(exc) from None
    return _view_to_dict(view)


@router.delete("/documents/{document_id}")
async def delete_document(
    kb_id: str,
    document_id: str,
    actor: str = "system",
    svc: FileManagementService = Depends(get_file_management_service),
) -> dict[str, Any]:
    """Soft-delete a document (keeps the object; SRS §4.3A, §8.6)."""
    try:
        await svc.soft_delete(document_id, actor=actor)
    except FileManagementServiceError as exc:
        raise _map_error(exc) from None
    return {"ok": True, "document_id": document_id}


@router.post("/documents/{document_id}/restore")
async def restore_document(
    kb_id: str,
    document_id: str,
    actor: str = "system",
    svc: FileManagementService = Depends(get_file_management_service),
) -> dict[str, Any]:
    """Restore a soft-deleted document (SRS §4.3A)."""
    try:
        view = await svc.restore(document_id, actor=actor)
    except FileManagementServiceError as exc:
        raise _map_error(exc) from None
    return _view_to_dict(view)


# ---------------------------------------------------------------------------
# Upload-session routes (initiate + complete; stage is an internal helper)
# ---------------------------------------------------------------------------


@router.post("/upload-sessions", status_code=status.HTTP_201_CREATED)
async def initiate_upload_session(
    kb_id: str,
    body: InitiateUploadRequest,
    svc: UploadSessionService = Depends(get_upload_session_service),
) -> dict[str, Any]:
    """Initiate an upload session (SRS §3.1B, §9.0A). Returns presigned PUT."""
    try:
        session, presign = await svc.initiate(
            kb_id=kb_id,
            folder_id=body.folder_id,
            actor=body.actor,
            filename=body.filename,
            expected_size=body.expected_size,
            expected_mime=body.expected_mime,
            idempotency_key=body.idempotency_key,
        )
    except (FileManagementServiceError, StorageError) as exc:
        raise _map_error(exc) from None
    return {
        "session": {
            "id": session.id,
            "kb_id": session.kb_id,
            "folder_id": session.folder_id,
            "actor": session.actor,
            "original_filename": session.original_filename,
            "expected_size": session.expected_size,
            "expected_mime": session.expected_mime,
            "idempotency_key": session.idempotency_key,
            "expires_at": session.expires_at,
            "state": session.state,
            "staging_bucket": session.staging_bucket,
            "staging_object_key": session.staging_object_key,
        },
        "presigned_put": {
            "method": presign.method,
            "url": presign.url,
            "expires_in_seconds": presign.expires_in_seconds,
        },
    }


@router.post("/upload-sessions/{session_id}/complete")
async def complete_upload_session(
    kb_id: str,
    session_id: str,
    body: CompleteUploadRequest,
    svc: UploadSessionService = Depends(get_upload_session_service),
) -> dict[str, Any]:
    """Commit a staged upload (SRS §4.1A, §9.0A COMMITTED). Idempotent."""
    try:
        result = await svc.complete(
            session_id,
            expected_sha256=body.expected_sha256,
            mime=body.mime,
            document_id=body.document_id,
            owner_id=body.owner_id,
            document_type=body.document_type,
        )
    except (FileManagementServiceError, StorageError) as exc:
        raise _map_error(exc) from None
    return {
        "storage_object_id": result.storage_object_id,
        "document_id": result.document_id,
        "content_revision": result.content_revision,
        "sha256": result.sha256,
        "size": result.size,
    }


# ---------------------------------------------------------------------------
# Streaming helper
# ---------------------------------------------------------------------------


async def _request_stream(request: Request) -> Any:
    """Yield the raw request body as an async byte stream for replace_content.

    Starlette's ``request.stream()`` yields ``bytes`` chunks as they arrive,
    which is exactly the ``AsyncIterator[bytes]`` shape ``put_stream`` expects.
    """
    async for chunk in request.stream():
        if chunk:
            yield chunk


__all__ = [
    "CompleteUploadRequest",
    "DownloadUrlRequest",
    "InitiateUploadRequest",
    "MoveRequest",
    "RenameRequest",
    "ReplaceContentRequest",
    "get_file_management_service",
    "get_upload_session_service",
    "router",
]
