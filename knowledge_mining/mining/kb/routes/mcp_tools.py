"""MCP 工具族内部数据端点（批次7）：mcp_server 持 X-Internal-Auth 转发用户级操作。

身份模型：mcp_server 已按密钥验明 username；本组端点信任该身份并做**资源级授权**
（is_visible / can_write），不重复验密钥。路径前缀为静态字面量，需在 kb_router
（动态 /api/kb/{kb_id}）之前注册。
"""
from __future__ import annotations

import base64
import json
import logging
from hmac import compare_digest
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from knowledge_mining.mining.infra.control_plane import get_internal_verify_secret
from knowledge_mining.mining.kb.deps import get_document_service, get_kb_db
from knowledge_mining.mining.kb.db import KbDB

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kb/mcp-tools", tags=["kb-mcp-tools"])

#: 上传内容 base64 解码后的硬上限（与常规上传上限独立、更保守：Agent 场景）。
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _require_internal(request: Request) -> None:
    secret = get_internal_verify_secret()
    if not secret:
        raise HTTPException(401, "auth not initialized")
    if not compare_digest(request.headers.get("X-Internal-Auth", ""), secret):
        raise HTTPException(401, "unauthenticated")


def _require_internal_body(request: Request) -> dict[str, Any]:
    _require_internal(request)
    return {}


async def _user_id(kbdb: KbDB, username: str) -> str:
    user = await kbdb.get_user_by_username(username)
    if user is None:
        raise HTTPException(401, f"unknown user: {username}")
    return user["id"]


async def _visible_kb(kbdb: KbDB, user_id: str, kb_id: str) -> None:
    if not await kbdb.is_visible(kb_id=kb_id, user_id=user_id):
        # 不泄露存在性——与 KB 族路由同语义
        raise HTTPException(404, f"knowledge base not found: {kb_id}")


@router.post("/list-kbs", dependencies=[Depends(_require_internal_body)])
async def list_kbs(body: dict[str, Any], kbdb: KbDB = Depends(get_kb_db)) -> dict[str, Any]:
    """该用户 MCP 开放的库（∩ 实时可见）：id/名称/文档数/绑定范式。"""
    user_id = await _user_id(kbdb, str(body.get("username") or ""))
    access = await kbdb.get_mcp_access(user_id)
    if access is None:
        return {"knowledge_bases": []}
    open_ids = access.get("open_kb_ids") or []
    out: list[dict[str, Any]] = []
    for kb_id in open_ids:
        kb = await kbdb.get_kb(kb_id)
        if kb is None:
            continue  # 开放后软删：自动从清单消失
        if not await kbdb.is_visible(kb_id=kb_id, user_id=user_id):
            continue  # 权限收窄即时生效
        out.append({
            "id": kb["id"],
            "name": kb["name"],
            "description": kb.get("description"),
            "domain": kb.get("domain"),
            "default_paradigm_id": kb.get("default_paradigm_id"),
        })
    return {"knowledge_bases": out}


@router.post("/list-documents", dependencies=[Depends(_require_internal_body)])
async def list_documents(
    body: dict[str, Any], kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    """库内文件清单（软删过滤，状态内联派生）。limit≤200，offset 分页。"""
    user_id = await _user_id(kbdb, str(body.get("username") or ""))
    kb_id = str(body.get("kb_id") or "")
    await _visible_kb(kbdb, user_id, kb_id)
    limit = min(int(body.get("limit") or 50), 200)
    offset = max(int(body.get("offset") or 0), 0)
    docs = await kbdb.list_documents_in_kb(kb_id=kb_id, limit=limit, offset=offset)
    return {"documents": [
        {
            "id": d["id"],
            "name": d["document_name"],
            "status": d.get("status"),
            "file_size": d.get("file_size"),
            "modified_at": str(d.get("modified_at") or d.get("created_at") or ""),
        }
        for d in docs
    ]}


@router.post("/get-document", dependencies=[Depends(_require_internal_body)])
async def get_document(
    body: dict[str, Any], kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    """单文档的结构化知识（切片/检索单元，走既有读路径：限量+软删过滤）。"""
    user_id = await _user_id(kbdb, str(body.get("username") or ""))
    kb_id = str(body.get("kb_id") or "")
    await _visible_kb(kbdb, user_id, kb_id)
    knowledge = await kbdb.get_document_knowledge(kb_id, str(body.get("document_id") or ""))
    if not knowledge:
        raise HTTPException(404, "document not found (or never mined)")
    # 瘦身：MCP 场景只需要切片文本与检索单元标题
    segments = [
        {"index": s.get("segment_index"), "block_type": s.get("block_type"),
         "text": s.get("raw_text"), "section": s.get("section_title")}
        for s in (knowledge.get("segments") or [])
    ]
    return {
        "document_id": knowledge.get("document_id"),
        "kb_id": kb_id,
        "truncated": bool(knowledge.get("truncated")),
        "total_segments": knowledge.get("total_segments"),
        "segments": segments,
    }


@router.post("/upload", dependencies=[Depends(_require_internal_body)])
async def upload(
    body: dict[str, Any],
    request: Request,
    kbdb: KbDB = Depends(get_kb_db),
    doc_svc: Any = Depends(get_document_service),
) -> dict[str, Any]:
    """Agent 上传文件入库（不自动触发挖掘——D2 用户拍板）。"""
    user_id = await _user_id(kbdb, str(body.get("username") or ""))
    kb_id = str(body.get("kb_id") or "")
    await _visible_kb(kbdb, user_id, kb_id)
    if not await kbdb.can_write(kb_id=kb_id, user_id=user_id):
        raise HTTPException(403, "only owner or editor may upload")

    filename = str(body.get("filename") or "").strip()
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(422, "invalid filename")
    try:
        content = base64.b64decode(str(body.get("content_b64") or ""), validate=True)
    except Exception:
        raise HTTPException(422, "content_b64 is not valid base64") from None
    if not content:
        raise HTTPException(422, "empty content")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file too large (>{MAX_UPLOAD_BYTES // (1024*1024)}MB)")

    async def _stream():
        yield content

    result = await doc_svc.upload_stream(
        kb_id=kb_id, owner_id=user_id, filename=filename, stream=_stream(),
    )
    logger.info("[mcp-tools] upload by %s -> kb=%s file=%s",
                body.get("username"), kb_id, filename)
    return {
        "document_id": result.get("id"),
        "document_name": result.get("document_name"),
        "message": "已上传（未自动挖掘）：请在平台界面发起挖掘后内容才可检索",
    }
