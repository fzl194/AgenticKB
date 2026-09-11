"""KB document routes — /api/kb/{kb_id}/documents.

云端文件管理观感：上传（含 zip 自动解压）/ 列表 / 详情 / 改元信息 / 下载 / 软撤回。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from psycopg.errors import UniqueViolation
from pydantic import BaseModel

from knowledge_mining.mining.infra.upload_config import UploadConfig
from knowledge_mining.mining.api.deps import get_parse_result_service
from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.deps import get_document_service, get_folder_service, get_kb_db
from knowledge_mining.mining.kb.routes.kbs import _map_error
from knowledge_mining.mining.kb.services.document_service import (
    ContentRevisionConflict, DocumentService, UploadTooLarge,
)
from knowledge_mining.mining.kb.services.folder_service import FolderService
from knowledge_mining.mining.kb.services.kb_service import Duplicate, Forbidden, NotFound

router = APIRouter(prefix="/api/kb/{kb_id}/documents", tags=["kb-documents"])

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
    view: str = "current_serving",
):
    """结构化数据（A0-1 双视图，默认 current_serving）.

    - current_serving：该文档当前被搜索/Agent 使用的版本（与 Java serving 对
      该文档的选择规则一致——按 kb 层 build 反查，不由「最新解析成功」冒充）；
      无 current_serving 时回落展示 latest 并标记「尚未进入搜索」；
    - latest_revision：当前上传文件的最新解析结果。
    响应含 versioning（serving/latest 快照身份、in_sync、latest_state）。
    """
    if view not in ("current_serving", "latest_revision"):
        raise HTTPException(
            422, "view must be 'current_serving' or 'latest_revision'"
        )
    if not await kbdb.is_visible(kb_id=kb_id, user_id=user["id"]):
        raise HTTPException(404, "Document not found")
    document = await kbdb.get_document_identity(document_id)
    if document is None or document.get("kb_id") != kb_id:
        raise HTTPException(404, "Document not found")
    service = await get_parse_result_service(request, domain=document["domain"])
    from knowledge_mining.mining.contracts.storage.errors import StorageObjectMissing

    # serving 上下文（两视图都返回对比信息）：按 Java serving 同规则反查
    serving = await kbdb.get_current_serving_snapshot(kb_id, document_id)
    try:
        result = await service.get_parse_result(
            domain=document["domain"], document_id=document_id,
            view=view, serving=serving,
        )
    except StorageObjectMissing:
        raise HTTPException(409, "Current parsed artifact is unavailable") from None
    if result is None:
        raise HTTPException(404, "Current document revision has no structured result")
    # A3：doc_key 即投影 document_ref（网页 tables tab 构造内部表格查询 ref 用）
    snapshot_block = result.get("snapshot")
    if isinstance(snapshot_block, dict):
        snapshot_block.setdefault("document_ref", document.get("document_key"))
    return result


_ROUTE_CHUNK = 256 * 1024


async def _upload_file_chunks(file: UploadFile) -> AsyncIterator[bytes]:
    """分块读 multipart 文件（P01-S1）：不整读，内存与文件大小无关。"""
    while True:
        chunk = await file.read(_ROUTE_CHUNK)
        if not chunk:
            break
        yield chunk


@router.post("", status_code=201)
async def upload_document(
    kb_id: str,
    file: UploadFile = File(...),
    directory: str | None = Form(None),
    document_type: str | None = Form(None),
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    filename = file.filename or "unnamed"
    try:
        result = await svc.intake_upload(
            kb_id=kb_id, owner_id=user["id"], filename=filename,
            stream=_upload_file_chunks(file),
            directory_path=directory, document_type=document_type,
            mime=file.content_type,
        )
    except UploadTooLarge as exc:
        raise HTTPException(
            413, f"文件超过大小上限（{exc.limit_bytes} 字节）：{exc}"
        ) from None
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except (UniqueViolation, Duplicate) as exc:
        # KB 内同名文档已存在（uq_asset_documents_kb_key）——幂等冲突给 409，
        # 而不是把 SQL 异常裸抛成 500。
        raise HTTPException(
            409, f"Document named {filename!r} already exists in this KB"
        ) from None

    if result["kind"] == "file":
        return result["document"]
    if result["kind"] == "archive":
        return {"documents": result["documents"]}
    return JSONResponse(status_code=202, content={
        "archive_task_id": result["archive_task_id"],
        "status": "processing",
        "message": (f"归档 {filename!r} 正在后台解压入库，"
                    f"请轮询任务状态获取进度"),
    })


@router.get("/archive-tasks/{task_id}")
async def get_archive_task(
    kb_id: str,
    task_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    """归档后台解压任务状态（批次2c）：前端轮询至 completed/failed。"""
    from knowledge_mining.mining.kb.services.archive_tasks import registry
    task = registry.get(task_id)
    if task is None or task["kb_id"] != kb_id:
        raise HTTPException(404, f"archive task {task_id!r} not found")
    await svc._svc._assert_read(kb_id, user["id"])  # noqa: SLF001
    return task


@router.get("")
async def list_documents(
    kb_id: str,
    directory: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    """目录内文件（服务端分页：limit ≤500 / offset）。

    2026-09-08 修复：此前 limit 固定 200 且无 offset——大库（万级文件）
    被静默截断且前端无感知。前端默认 50/页 + count 端点拿总数。
    """
    try:
        return await svc.list_documents(
            kb_id=kb_id, user_id=user["id"], directory=directory,
            limit=limit, offset=offset,
        )
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None


@router.get("/count")
async def count_documents(
    kb_id: str,
    directory: str | None = None,
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    """文件总数（与 list 同过滤口径）——分页总数。

    注：注册顺序在 /{document_id} 之前——FastAPI 按注册序匹配，
    字面量 /count 优先于路径参数，不会被当成 document_id。
    """
    try:
        total = await svc.count_documents(
            kb_id=kb_id, user_id=user["id"], directory=directory,
        )
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None
    return {"total": total}


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
    except (NotFound, Forbidden, Duplicate) as exc:
        raise _map_error(exc) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/{document_id}/content")
async def replace_document_content(
    kb_id: str,
    document_id: str,
    file: UploadFile = File(...),
    expected_revision: int = Form(..., ge=0),
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    """Explicitly replace current bytes; never silently overwrite on upload."""
    try:
        return await svc.replace_content(
            kb_id=kb_id, document_id=document_id, user_id=user["id"],
            filename=file.filename or "", expected_revision=expected_revision,
            stream=_upload_file_chunks(file), max_bytes=UploadConfig().upload_max_file_size,
        )
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None
    except ContentRevisionConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except UploadTooLarge as exc:
        raise HTTPException(413, "替换文件超过上传大小限制。") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/{document_id}/preview-url")
async def document_preview_url(
    kb_id: str,
    document_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    """大文件在线预览的直连地址（短时效预签名，10 分钟自失效）.

    浏览器拿到后 iframe/img 直接指向对象存储，自带 Range 分页按需
    加载（首屏只拉首页字节）——替代"后端全量转发→前端 Blob"的全量
    下载路径。legacy 本地文档无对象可签，返回 404 由前端回落。
    """
    try:
        presigned = await svc.presign_document(
            document_id=document_id, user_id=user["id"],
        )
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None
    if presigned is None:
        raise HTTPException(404, "Document has no presignable object")
    _, _, url = presigned
    return {"url": url, "expires_in": 600}


@router.get("/{document_id}/download")
async def download_document(
    kb_id: str,
    document_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    try:
        # 对象存储文档：流式转发对象字节（不整读内存）；legacy 本地文档
        # 回落文件路径。
        obj = await svc.download_object(
            document_id=document_id, user_id=user["id"],
        )
        if obj is not None:
            filename, mime, stream = obj
            from fastapi.responses import StreamingResponse
            from urllib.parse import quote

            # HTTP 头只允许 latin-1：中文文件名走 RFC 5987 的 filename*，
            # 并给不支持它的老客户端一个 ASCII 回落名。
            ascii_name = filename.encode("ascii", "ignore").decode() or "document"
            disposition = (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(filename)}"
            )
            return StreamingResponse(
                stream,
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
    """软删 KB 文档（P08-S1）：盖 deleted_at，历史 Build 不被改写；restore 可恢复。"""
    try:
        await svc.delete(document_id=document_id, user_id=user["id"])
        return {"ok": True}
    except (NotFound, Forbidden) as exc:
        raise _map_error(exc) from None


@router.post("/{document_id}/restore")
async def restore_document(
    kb_id: str,
    document_id: str,
    user: dict[str, Any] = Depends(current_user),
    svc: DocumentService = Depends(get_document_service),
):
    """恢复软删文档（写权限校验在 service 内；幂等——未删状态原样返回）。"""
    try:
        return await svc.restore(document_id=document_id, user_id=user["id"])
    except (NotFound, Forbidden, Duplicate) as exc:
        raise _map_error(exc) from None
    except ValueError as exc:
        # 目录已删等位置冲突：可解释的 409，不落裸 500（与 43 号交付承诺一致）
        raise HTTPException(409, str(exc)) from None


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
