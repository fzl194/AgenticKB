"""KB document routes — /api/kb/{kb_id}/documents.

云端文件管理观感：上传（含 zip 自动解压）/ 列表 / 详情 / 改元信息 / 下载 / 软撤回。
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
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
    DocumentService, UploadTooLarge, SYNC_ARCHIVE_MEMBERS, count_archive_members,
)
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


_ROUTE_CHUNK = 256 * 1024

#: 后台归档任务的强引用（防事件循环只留弱引用被 GC）
_archive_bg_tasks: set = set()


async def _upload_file_chunks(file: UploadFile) -> AsyncIterator[bytes]:
    """分块读 multipart 文件（P01-S1）：不整读，内存与文件大小无关。"""
    while True:
        chunk = await file.read(_ROUTE_CHUNK)
        if not chunk:
            break
        yield chunk


#: chm 无廉价成员枚举——按包体大小决策同步/异步（超过即异步）
_ASYNC_ARCHIVE_BYTES = 10 * 1024 * 1024


async def _run_archive_task(task_id: str, svc: DocumentService, *,
                            kb_id: str, owner_id: str, archive_path: Path,
                            archive_name: str, max_member_bytes: int | None) -> None:
    """后台解压任务：进度回写注册表，结束删暂存包。"""
    from knowledge_mining.mining.kb.services.archive_tasks import registry
    try:
        docs = await svc.upload_archive_path(
            kb_id=kb_id, owner_id=owner_id, archive_path=archive_path,
            archive_name=archive_name, persist_archive=True,
            max_member_bytes=max_member_bytes,
            on_progress=lambda done, total, name: registry.update(
                task_id, done=done, total=total),
        )
        registry.complete(task_id, document_count=len(docs), failed=0)
    except Exception as exc:
        registry.fail(task_id, f"{type(exc).__name__}: {exc}")
    finally:
        try:
            archive_path.unlink(missing_ok=True)
        except OSError:
            pass


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
    cfg = UploadConfig()
    try:
        if _is_archive(filename):
            # 归档需完整字节——分块落临时文件（内存有界），额度强制。
            with TemporaryDirectory(prefix="agentickb-archive-") as tmp:
                archive_path = Path(tmp) / Path(filename).name
                written = 0
                with archive_path.open("wb") as fh:
                    async for chunk in _upload_file_chunks(file):
                        written += len(chunk)
                        if written > cfg.upload_max_archive_size:
                            raise UploadTooLarge(
                                (f"归档上传 {written} 字节超过上限 "
                                 f"{cfg.upload_max_archive_size} 字节"),
                                limit_bytes=cfg.upload_max_archive_size,
                            )
                        fh.write(chunk)

                ext = archive_path.suffix.lower()
                # 同步阈值：zip/hdx 按成员数；chm 按包体大小
                if ext == ".chm":
                    go_async = archive_path.stat().st_size > _ASYNC_ARCHIVE_BYTES
                else:
                    try:
                        member_count = await count_archive_members(archive_path)
                    except Exception:
                        member_count = SYNC_ARCHIVE_MEMBERS + 1  # 数不清→异步兜底
                    go_async = member_count > SYNC_ARCHIVE_MEMBERS

                if go_async:
                    # 大包异步（批次2c）：包转存进程级暂存（请求临时目录随请求
                    # 结束清理），后台解压，立即返回任务 ID 供轮询。
                    from knowledge_mining.mining.kb.services.archive_tasks import (
                        registry,
                    )
                    task_id = registry.create(kb_id=kb_id, archive_name=filename)
                    staging_dir = Path(tempfile.gettempdir()) / "agentickb-archive-tasks"
                    staging_dir.mkdir(parents=True, exist_ok=True)
                    staging_path = staging_dir / f"{task_id}{archive_path.suffix}"
                    shutil.copyfile(archive_path, staging_path)
                    task = asyncio.create_task(_run_archive_task(
                        task_id, svc, kb_id=kb_id, owner_id=user["id"],
                        archive_path=staging_path, archive_name=filename,
                        max_member_bytes=cfg.upload_max_file_size,
                    ))
                    _archive_bg_tasks.add(task)
                    task.add_done_callback(_archive_bg_tasks.discard)
                    return JSONResponse(status_code=202, content={
                        "archive_task_id": task_id,
                        "status": "processing",
                        "message": (f"归档 {filename!r} 正在后台解压入库，"
                                    f"请轮询任务状态获取进度"),
                    })

                docs = await svc.upload_archive_path(
                    kb_id=kb_id, owner_id=user["id"], archive_path=archive_path,
                    archive_name=filename, persist_archive=True,
                )
                return {"documents": docs}
        return await svc.upload_stream(
            kb_id=kb_id, owner_id=user["id"], filename=filename,
            stream=_upload_file_chunks(file),
            directory_path=directory, document_type=document_type,
            mime=file.content_type, max_bytes=cfg.upload_max_file_size,
        )
    except UploadTooLarge as exc:
        raise HTTPException(
            413, f"文件超过大小上限（{exc.limit_bytes} 字节）：{exc}"
        ) from None
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
