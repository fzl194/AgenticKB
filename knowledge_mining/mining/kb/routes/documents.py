"""KB document routes — /api/kb/{kb_id}/documents.

云端文件管理观感：上传（含 zip 自动解压）/ 列表 / 详情 / 改元信息 / 下载 / 软撤回。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from psycopg.errors import UniqueViolation
from pydantic import BaseModel

from knowledge_mining.mining.infra.upload_config import UploadConfig
from knowledge_mining.mining.api.deps import get_parse_result_service
from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.deps import get_document_service, get_folder_service, get_kb_db
from knowledge_mining.mining.kb.routes.kbs import _map_error
from knowledge_mining.mining.kb.services.document_service import DocumentService
from knowledge_mining.mining.kb.services.folder_service import FolderService
from knowledge_mining.mining.kb.services.kb_service import Duplicate, Forbidden, NotFound

router = APIRouter(prefix="/api/kb/{kb_id}/documents", tags=["kb-documents"])

_archive_exts = UploadConfig().archive_exts_set


def _is_archive(filename: str) -> bool:
    return Path(filename).suffix.lower() in _archive_exts


class DocPatch(BaseModel):
    document_name: str | None = None
    document_type: str | None = None


class DocMove(BaseModel):
    target_folder_id: str | None = None  # None = 移到根


@router.get("/{doc_id}/knowledge")
async def document_knowledge(
    kb_id: str,
    doc_id: str,
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
):
    """文档当前知识（文件详情多 tab 用）：原始预览之外的切片/检索单元/实体提及。
    KB 无 build 或文档未入选 → {"mined": False}，前端只显原始预览。"""
    if not await kbdb.is_visible(kb_id=kb_id, user_id=user["id"]):
        raise HTTPException(404, f"KB {kb_id} not found")
    return await kbdb.get_document_knowledge(kb_id, doc_id)


@router.get("/{document_id}/parse-result")
async def document_parse_result(
    kb_id: str,
    document_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
):
    """Return the current document revision's structured result to KB members."""
    if not await kbdb.is_visible(kb_id=kb_id, user_id=user["id"]):
        raise HTTPException(404, "Document not found")
    document = await kbdb.get_document_identity(document_id)
    if document is None or document.get("kb_id") != kb_id:
        raise HTTPException(404, "Document not found")
    service = await get_parse_result_service(request, domain=document["domain"])
    from knowledge_mining.mining.contracts.storage.errors import StorageObjectMissing

    try:
        result = await service.get_parse_result(
            domain=document["domain"], document_id=document_id,
        )
    except StorageObjectMissing:
        raise HTTPException(409, "Current parsed artifact is unavailable") from None
    if result is None:
        raise HTTPException(404, "Current document revision has no structured result")
    return result


@router.post("", status_code=201)
async def upload_document(
    kb_id: str,
    file: UploadFile = File(...),
    directory: str | None = Form(None),
    document_type: str | None = Form(None),
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    content = await file.read()
    filename = file.filename or "unnamed"
    try:
        if _is_archive(filename):
            docs = await svc.upload_zip(
                kb_id=kb_id, owner_id=user["id"], zip_bytes=content, filename=filename,
            )
            return {"documents": docs}
        return await svc.upload(
            kb_id=kb_id, owner_id=user["id"], filename=filename, content=content,
            directory_path=directory, document_type=document_type, mime=file.content_type,
        )
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except UniqueViolation as exc:
        # KB 内同名文档已存在（uq_asset_documents_kb_key）——幂等冲突给 409，
        # 而不是把 SQL 异常裸抛成 500。
        raise HTTPException(
            409, f"Document named {filename!r} already exists in this KB"
        ) from None


@router.get("")
async def list_documents(
    kb_id: str,
    directory: str | None = None,
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    try:
        return await svc.list_documents(kb_id=kb_id, user_id=user["id"], directory=directory)
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None


@router.get("/{document_id}")
async def get_document(
    kb_id: str,
    document_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    try:
        return await svc.get_document(document_id=document_id, user_id=user["id"])
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None


@router.patch("/{document_id}")
async def patch_document(
    kb_id: str,
    document_id: str,
    body: DocPatch,
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    try:
        return await svc.patch_document(
            document_id=document_id, user_id=user["id"],
            document_name=body.document_name, document_type=body.document_type,
        )
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None


@router.get("/{document_id}/download")
async def download_document(
    kb_id: str,
    document_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    try:
        # 对象存储文档：流式返回对象字节；legacy 本地文档回落文件路径。
        obj = await svc.download_object(
            document_id=document_id, user_id=user["id"],
        )
        if obj is not None:
            filename, mime, stream = obj
            chunks = [chunk async for chunk in stream]
            from fastapi.responses import Response
            from urllib.parse import quote

            # HTTP 头只允许 latin-1：中文文件名走 RFC 5987 的 filename*，
            # 并给不支持它的老客户端一个 ASCII 回落名。
            ascii_name = filename.encode("ascii", "ignore").decode() or "document"
            disposition = (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(filename)}"
            )
            return Response(
                content=b"".join(chunks),
                media_type=mime or "application/octet-stream",
                headers={"Content-Disposition": disposition},
            )
        p = await svc.download_path(document_id=document_id, user_id=user["id"])
        return FileResponse(str(p), filename=p.name)
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None


@router.delete("/{document_id}")
async def delete_document(
    kb_id: str,
    document_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    """删除 KB 文档（磁盘文件 + 身份行）。撤回（从发布中移除）是另一个概念，待 release 机制接线。"""
    try:
        await svc.delete(document_id=document_id, user_id=user["id"])
        return {"ok": True}
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None


@router.post("/{document_id}/move")
async def move_document(
    kb_id: str,
    document_id: str,
    body: DocMove,
    user: dict[str, Any] = Depends(current_user),
    svc: FolderService = Depends(get_folder_service),
):
    try:
        return await svc.move_document(
            document_id=document_id, target_folder_id=body.target_folder_id, user_id=user["id"],
        )
    except (NotFound, Forbidden, Duplicate) as exc:
        raise _map_error(exc) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
