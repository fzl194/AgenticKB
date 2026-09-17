"""MCP 工具族内部数据端点（批次7）：mcp_server 持 X-Internal-Auth 转发用户级操作。

身份模型：mcp_server 已按密钥验明 username；本组端点信任该身份并做**资源级授权**
（is_visible / can_write），不重复验密钥。路径前缀为静态字面量，需在 kb_router
（动态 /api/kb/{kb_id}）之前注册。

上传（2026-09-11 直传改造，用户拍板只留一条路）：
- ``POST /begin-upload``：签发一次性上传票据（TTL 10 分钟、单次使用、绑定
  kb/user/filename）。工具层把它包装成 Agent 可 PUT 的公网直传 URL。
- ``PUT /upload-direct/{ticket}`：mcp_server 流式转发 Agent 的**原始字节**
  （无 base64），走与网页上传同源的 intake_upload（普通文件直传 /
  归档自动解压）+ 自动挖掘入队管线。
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
from knowledge_mining.mining.infra.upload_config import UploadConfig
from knowledge_mining.mining.kb.deps import get_document_service, get_kb_db
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services import auto_mine
from knowledge_mining.mining.kb.services.document_service import (
    UploadTooLarge,
    is_upload_archive,
)

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
    filename, key_id——51号批次2 起直传前还须钥匙 active)。进程内存储——
    单实例部署约定；重启丢票据只是 10 分钟窗口内的
    直传作废。过期清理在签发/兑换时顺手做，不起后台线程。
    """

    def __init__(self, ttl_seconds: int = UPLOAD_TICKET_TTL) -> None:
        self._ttl = ttl_seconds
        self._tickets: dict[str, dict[str, Any]] = {}

    def issue(
        self, *, kb_id: str, user_id: str, username: str, filename: str,
        key_id: str = "",
    ) -> dict[str, Any]:
        self._sweep()
        ticket = f"up_{secrets.token_urlsafe(24)}"
        self._tickets[ticket] = {
            "kb_id": kb_id, "user_id": user_id, "username": username,
            "filename": filename, "key_id": key_id,
            "expires_at": time.monotonic() + self._ttl,
        }
        return {"ticket": ticket, "expires_in": self._ttl}

    def peek(self, ticket: str) -> dict[str, Any] | None:
        """查看票据但不消费（过期判定与 redeem 共用；供兑换前的预检用）。"""
        self._sweep()
        entry = self._tickets.get(ticket)
        if entry is None or entry["expires_at"] < time.monotonic():
            return None
        return entry

    def redeem(self, ticket: str) -> dict[str, Any] | None:
        """取出并作废票据（单次使用；过期或未知返回 None）。"""
        entry = self.peek(ticket)
        if entry is not None:
            self._tickets.pop(ticket, None)  # pop = 单次使用
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


async def _key_scope(kbdb: KbDB, username: str, key_id: str) -> tuple[str, dict]:
    """解析 body 携带的 username+key_id → (user_id, key行)。

    51号批次2：钥匙级收口——key 不存在/非本人/非 active → 401 "invalid
    mcp key"。注：本端点在 X-Internal-Auth 之后、探测面为零，未知 username
    沿用 _user_id 的既有 401 "unknown user: …" 文案（两段文案不同属既有
    行为）。key_id 缺失/空走 get_mcp_key("")→None 天然拒掉。
    """
    user_id = await _user_id(kbdb, username)
    key = await kbdb.get_mcp_key(key_id=key_id)
    if key is None or key["user_id"] != user_id or key["status"] != "active":
        raise HTTPException(401, "invalid mcp key")
    return user_id, key


def _validated_filename(raw: Any) -> str:
    filename = str(raw or "").strip()
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(422, "invalid filename")
    return filename


@router.post("/list-kbs", dependencies=[Depends(_require_internal_body)])
async def list_kbs(body: dict[str, Any], kbdb: KbDB = Depends(get_kb_db)) -> dict[str, Any]:
    """该用户 MCP 开放的库（∩ 实时可见）：id/名称/文档数/绑定范式。"""
    user_id, _key = await _key_scope(
        kbdb, str(body.get("username") or ""), str(body.get("key_id") or ""),
    )
    open_ids = await kbdb.key_open_kb_ids(key_id=str(body.get("key_id") or ""))
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
    """库内文件清单（软删过滤，状态内联派生）。limit≤200，offset 分页。

    47 号引用：外部引用文档合并在后（referenced=true，只读语义），Agent
    看到的是「自有 + 引用」的完整可用知识面。
    """
    key_id = str(body.get("key_id") or "")
    user_id, _key = await _key_scope(
        kbdb, str(body.get("username") or ""), key_id,
    )
    kb_id = str(body.get("kb_id") or "")
    await _visible_kb(kbdb, user_id, kb_id)
    if kb_id not in await kbdb.key_open_kb_ids(key_id=key_id):
        # 钥匙开放集之外的库：与不可见同语义（404 防探测）
        raise HTTPException(404, f"knowledge base not found: {kb_id}")
    limit = min(int(body.get("limit") or 50), 200)
    offset = max(int(body.get("offset") or 0), 0)
    docs = await kbdb.list_documents_in_kb(kb_id=kb_id, limit=limit, offset=offset)
    out = [
        {
            "id": d["id"],
            "name": d["document_name"],
            "status": d.get("status"),
            "file_size": d.get("file_size"),
            "modified_at": str(d.get("modified_at") or d.get("created_at") or ""),
            "referenced": False,
        }
        for d in docs
    ]
    if offset == 0:
        for d in await kbdb.list_referenced_documents(kb_id):
            out.append({
                "id": d["id"],
                "name": d.get("document_name"),
                "status": "referenced",
                "file_size": d.get("file_size"),
                "modified_at": str(d.get("referenced_at") or ""),
                "referenced": True,
                "directory_path": d.get("directory_path"),
            })
    return {"documents": out}


@router.post("/begin-upload", dependencies=[Depends(_require_internal_body)])
async def begin_upload(
    body: dict[str, Any],
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    """直传第一步：校验权限与文件名，签发一次性上传票据。"""
    username = str(body.get("username") or "")
    key_id = str(body.get("key_id") or "")
    user_id, _key = await _key_scope(kbdb, username, key_id)
    kb_id = str(body.get("kb_id") or "")
    await _visible_kb(kbdb, user_id, kb_id)
    if kb_id not in await kbdb.key_open_kb_ids(key_id=key_id):
        # 钥匙开放集之外的库：与不可见同语义（404 防探测）
        raise HTTPException(404, f"knowledge base not found: {kb_id}")
    if not await kbdb.can_write(kb_id=kb_id, user_id=user_id):
        raise HTTPException(403, "only owner or editor may upload")
    filename = _validated_filename(body.get("filename"))

    # 归档（zip/hdx/chm）与网页上传同限（默认 500MB）；普通文件走 MCP 上限
    max_bytes = (
        UploadConfig().upload_max_archive_size
        if is_upload_archive(filename) else MAX_UPLOAD_BYTES
    )
    issued = _TICKETS.issue(
        kb_id=kb_id, user_id=user_id, username=username, filename=filename,
        key_id=key_id,
    )
    logger.info("[mcp-tools] begin-upload by %s -> kb=%s file=%s ticket=%s",
                username, kb_id, filename, issued["ticket"][:11] + "…")
    return {
        "ticket": issued["ticket"],
        "upload_path": f"/api/kb/mcp-tools/upload-direct/{issued['ticket']}",
        "max_bytes": max_bytes,
        "expires_in": issued["expires_in"],
    }


@router.put("/upload-direct/{ticket}")
async def upload_direct(
    ticket: str,
    request: Request,
    kbdb: KbDB = Depends(get_kb_db),
    doc_svc: Any = Depends(get_document_service),
) -> dict[str, Any]:
    """直传第二步：票据即凭证 + 内部密钥，流式落库并自动入队挖掘。

    调用方是 mcp_server（持 X-Internal-Auth）；Agent 面向的公网 URL 在
    mcp_server 侧（PUT /upload/{ticket}，不验 MCP 密钥——密钥只在 MCP
    客户端配置里，模型拿不到；票据本身 192bit/单次/TTL 10min 即凭证，
    与 S3 预签名 URL 同一信任模型）。归属用户/库/文件名全部取票据绑定值。
    """
    _require_internal(request)
    entry = _TICKETS.peek(ticket)
    if entry is None:
        # 不泄露票据是否存在/过期——统一 404
        raise HTTPException(404, "upload ticket invalid or expired")
    # 票据绑钥匙：吊销钥匙后未消费票据立即失效（与无效票据同语义防探测）。
    # 先 peek 再查库——查库异常不消费票据，Agent 重试同一 ticket 不白吃。
    key = await kbdb.get_mcp_key(key_id=str(entry.get("key_id") or ""))
    if key is None or key["status"] != "active":
        raise HTTPException(404, "upload ticket invalid or expired")
    entry = _TICKETS.redeem(ticket)
    if entry is None:
        # peek 与 redeem 之间被并发消费——同语义 404
        raise HTTPException(404, "upload ticket invalid or expired")
    username = entry["username"]

    async def _stream() -> AsyncIterator[bytes]:
        async for chunk in request.stream():
            yield chunk

    try:
        # 与网页上传同源（intake_upload）：普通文件直传 / zip/hdx/chm 自动解压
        result = await doc_svc.intake_upload(
            kb_id=entry["kb_id"], owner_id=entry["user_id"],
            filename=entry["filename"], stream=_stream(),
            file_max_bytes=MAX_UPLOAD_BYTES,
        )
    except UploadTooLarge as exc:
        raise HTTPException(
            413, f"file too large（上限 {exc.limit_bytes} 字节）"
        ) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    logger.info("[mcp-tools] upload-direct by %s -> kb=%s file=%s kind=%s",
                username, entry["kb_id"], entry["filename"], result["kind"])

    # 自动挖掘入队：失败只降级，绝不影响上传结果
    kb = await kbdb.get_kb(entry["kb_id"])
    auto = (
        await auto_mine.enqueue_auto_mining(
            app_state=request.app.state, kbdb=kbdb, kb=kb,
            user_id=entry["user_id"], username=username,
        )
        if kb is not None else {"auto_mined": False, "reason": "internal"}
    )
    auto_fields = {
        "auto_mined": bool(auto.get("auto_mined")),
        **({"run_id": auto["run_id"]} if auto.get("run_id") else {}),
        **({"reason": auto["reason"]} if auto.get("reason") else {}),
    }
    if result["kind"] == "file":
        document = result["document"]
        return _upload_response(document.get("id"), document.get("document_name"),
                                auto, "已上传")
    if result["kind"] == "archive":
        docs = result["documents"]
        return {
            "kind": "archive",
            "document_count": len(docs),
            "documents": [
                {"document_id": d.get("id"), "document_name": d.get("document_name")}
                for d in docs
            ],
            **auto_fields,
            "message": (
                f"归档已解压为 {len(docs)} 个文档并"
                f"{'入队挖掘' if auto.get('auto_mined') else '未自动挖掘'}"
            ),
        }
    # 大归档后台解压中：挖掘 Run 已入队，认领时通常解压已完成（本地磁盘）
    return {
        "kind": "archive_task",
        "archive_task_id": result["archive_task_id"],
        "status": "processing",
        **auto_fields,
        "message": (
            "归档较大，正在后台解压入库；解压完成后用 get_knowledge(kb_name=…) "
            "可看到全部文档，挖掘任务已排队"
        ),
    }


def _upload_response(document_id, document_name, auto, prefix) -> dict[str, Any]:
    if auto.get("auto_mined"):
        message = (
            f"{prefix}并{auto.get('detail') or '入队挖掘'}"
            f"（run={str(auto.get('run_id'))[:8]}）；挖掘完成后内容才可检索"
        )
    else:
        reason = auto_mine.REASON_MESSAGES.get(
            str(auto.get("reason") or ""), "未知原因"
        )
        message = f"{prefix}（自动挖掘未触发：{reason}）；可在平台界面手动发起挖掘"
    return {
        "kind": "file",
        "document_id": document_id,
        "document_name": document_name,
        "auto_mined": bool(auto.get("auto_mined")),
        **({"run_id": auto["run_id"]} if auto.get("run_id") else {}),
        **({"reason": auto["reason"]} if auto.get("reason") else {}),
        "message": message,
    }
