"""MCP 工具族内部数据端点（批次7）：mcp_server 持 X-Internal-Auth 转发用户级操作。

身份模型：mcp_server 已按密钥验明 username；本组端点信任该身份并做**资源级授权**
（is_visible / can_write），不重复验密钥。路径前缀为静态字面量，需在 kb_router
（动态 /api/kb/{kb_id}）之前注册。

上传（2026-09-11 直传改造，用户拍板只留一条路）：
- ``POST /begin-upload``：签发一次性上传票据（TTL 10 分钟、单次使用、绑定
  kb/user/filename）。工具层把它包装成 Agent 可 PUT 的公网直传 URL。
- ``PUT /upload-direct/{ticket}`：mcp_server 流式转发 Agent 的**原始字节**
  （无 base64），走同一条 upload_stream + 自动挖掘入队管线。
- 旧 base64 端点（POST /upload）已退役：MCP 工具参数是 JSON，大文件
  base64 既撑爆上下文又翻倍传输。
"""
from __future__ import annotations

import logging
import secrets
import time
from hmac import compare_digest
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request

from knowledge_mining.mining.infra.control_plane import get_internal_verify_secret
from knowledge_mining.mining.kb.deps import get_document_service, get_kb_db
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services import auto_mine
from knowledge_mining.mining.kb.services.document_service import UploadTooLarge

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kb/mcp-tools", tags=["kb-mcp-tools"])

#: 直传硬上限（与常规上传上限独立、更保守：Agent 场景）。
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

#: 票据有效期（秒）。窗口内未完成的直传作废，Agent 重取即可。
UPLOAD_TICKET_TTL = 600


def _require_internal(request: Request) -> None:
    secret = get_internal_verify_secret()
    if not secret:
        raise HTTPException(401, "auth not initialized")
    if not compare_digest(request.headers.get("X-Internal-Auth", ""), secret):
        raise HTTPException(401, "unauthenticated")


def _require_internal_body(request: Request) -> dict[str, Any]:
    _require_internal(request)
    return {}


class _UploadTicketStore:
    """一次性上传票据账本（capability URL 的服务端侧）。

    票据即凭证：随机 192 位、TTL 内单次使用、绑定 (kb_id, user_id, username,
    filename)。进程内存储——单实例部署约定；重启丢票据只是 10 分钟窗口内的
    直传作废。过期清理在签发/兑换时顺手做，不起后台线程。
    """

    def __init__(self, ttl_seconds: int = UPLOAD_TICKET_TTL) -> None:
        self._ttl = ttl_seconds
        self._tickets: dict[str, dict[str, Any]] = {}

    def issue(
        self, *, kb_id: str, user_id: str, username: str, filename: str,
    ) -> dict[str, Any]:
        self._sweep()
        ticket = f"up_{secrets.token_urlsafe(24)}"
        self._tickets[ticket] = {
            "kb_id": kb_id, "user_id": user_id, "username": username,
            "filename": filename, "expires_at": time.monotonic() + self._ttl,
        }
        return {"ticket": ticket, "expires_in": self._ttl}

    def redeem(self, ticket: str, *, username: str) -> dict[str, Any] | None:
        """取出并作废票据；用户不匹配视为无效（票据不得跨密钥转手）。"""
        self._sweep()
        entry = self._tickets.pop(ticket, None)  # pop = 单次使用
        if entry is None or entry["expires_at"] < time.monotonic():
            return None
        if entry["username"] != username:
            return None
        return entry

    def _sweep(self) -> None:
        now = time.monotonic()
        for stale in [k for k, v in self._tickets.items() if v["expires_at"] < now]:
            del self._tickets[stale]


_TICKETS = _UploadTicketStore()


async def _user_id(kbdb: KbDB, username: str) -> str:
    user = await kbdb.get_user_by_username(username)
    if user is None:
        raise HTTPException(401, f"unknown user: {username}")
    return user["id"]


async def _visible_kb(kbdb: KbDB, user_id: str, kb_id: str) -> None:
    if not await kbdb.is_visible(kb_id=kb_id, user_id=user_id):
        # 不泄露存在性——与 KB 族路由同语义
        raise HTTPException(404, f"knowledge base not found: {kb_id}")


def _validated_filename(raw: Any) -> str:
    filename = str(raw or "").strip()
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(422, "invalid filename")
    return filename


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


@router.post("/begin-upload", dependencies=[Depends(_require_internal_body)])
async def begin_upload(
    body: dict[str, Any],
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    """直传第一步：校验权限与文件名，签发一次性上传票据。"""
    username = str(body.get("username") or "")
    user_id = await _user_id(kbdb, username)
    kb_id = str(body.get("kb_id") or "")
    await _visible_kb(kbdb, user_id, kb_id)
    if not await kbdb.can_write(kb_id=kb_id, user_id=user_id):
        raise HTTPException(403, "only owner or editor may upload")
    filename = _validated_filename(body.get("filename"))

    issued = _TICKETS.issue(
        kb_id=kb_id, user_id=user_id, username=username, filename=filename,
    )
    logger.info("[mcp-tools] begin-upload by %s -> kb=%s file=%s ticket=%s",
                username, kb_id, filename, issued["ticket"][:11] + "…")
    return {
        "ticket": issued["ticket"],
        "upload_path": f"/api/kb/mcp-tools/upload-direct/{issued['ticket']}",
        "max_bytes": MAX_UPLOAD_BYTES,
        "expires_in": issued["expires_in"],
    }


@router.put("/upload-direct/{ticket}")
async def upload_direct(
    ticket: str,
    request: Request,
    kbdb: KbDB = Depends(get_kb_db),
    doc_svc: Any = Depends(get_document_service),
) -> dict[str, Any]:
    """直传第二步：票据 + 内部密钥 + 用户绑定三重校验，流式落库并自动入队挖掘。

    调用方是 mcp_server（持 X-Internal-Auth）；Agent 面向的公网 URL 在
    mcp_server 侧（PUT /upload/{ticket}，验 MCP Bearer 密钥后转发到这里）。
    """
    _require_internal(request)
    username = request.headers.get("X-MCP-Username", "")
    entry = _TICKETS.redeem(ticket, username=username)
    if entry is None:
        # 不泄露票据是否存在/过期/归属——统一 404
        raise HTTPException(404, "upload ticket invalid or expired")

    async def _stream() -> AsyncIterator[bytes]:
        async for chunk in request.stream():
            yield chunk

    try:
        result = await doc_svc.upload_stream(
            kb_id=entry["kb_id"], owner_id=entry["user_id"],
            filename=entry["filename"], stream=_stream(),
            max_bytes=MAX_UPLOAD_BYTES,
        )
    except UploadTooLarge as exc:
        raise HTTPException(
            413, f"file too large (>{MAX_UPLOAD_BYTES // (1024*1024)}MB)"
        ) from exc
    logger.info("[mcp-tools] upload-direct by %s -> kb=%s file=%s",
                username, entry["kb_id"], entry["filename"])

    # 自动挖掘入队：失败只降级，绝不影响上传结果
    kb = await kbdb.get_kb(entry["kb_id"])
    auto = (
        await auto_mine.enqueue_auto_mining(
            app_state=request.app.state, kbdb=kbdb, kb=kb,
            user_id=entry["user_id"], username=username,
        )
        if kb is not None else {"auto_mined": False, "reason": "internal"}
    )
    if auto.get("auto_mined"):
        message = (
            f"已上传并{auto.get('detail') or '入队挖掘'}"
            f"（run={str(auto.get('run_id'))[:8]}）；挖掘完成后内容才可检索"
        )
    else:
        reason = auto_mine.REASON_MESSAGES.get(
            str(auto.get("reason") or ""), "未知原因"
        )
        message = f"已上传（自动挖掘未触发：{reason}）；可在平台界面手动发起挖掘"
    return {
        "document_id": result.get("id"),
        "document_name": result.get("document_name"),
        "auto_mined": bool(auto.get("auto_mined")),
        **({"run_id": auto["run_id"]} if auto.get("run_id") else {}),
        **({"reason": auto["reason"]} if auto.get("reason") else {}),
        "message": message,
    }
