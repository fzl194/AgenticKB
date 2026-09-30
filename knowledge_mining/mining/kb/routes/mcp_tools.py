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
from datetime import datetime, timezone
from hmac import compare_digest
from pathlib import Path
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
from knowledge_mining.mining.services.retrieval_records import RetrievalRecordService

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
    """One-time upload tickets; expired entries are returned for DB closure."""

    def __init__(self, ttl_seconds: int = UPLOAD_TICKET_TTL) -> None:
        self._ttl = ttl_seconds
        self._tickets: dict[str, dict[str, Any]] = {}

    def issue(
        self, *, kb_id: str, user_id: str, username: str, filename: str,
        key_id: str = "", access_record_id: str = "",
        access_record_total: int = 1, domain: str = "",
        document_id: str = "", expected_revision: int | None = None,
    ) -> dict[str, Any]:
        ticket = f"up_{secrets.token_urlsafe(24)}"
        self._tickets[ticket] = {
            "kb_id": kb_id, "user_id": user_id, "username": username,
            "filename": filename, "key_id": key_id,
            "access_record_id": access_record_id,
            "access_record_total": access_record_total,
            "domain": domain,
            "document_id": document_id,
            "expected_revision": expected_revision,
            "expires_at": time.monotonic() + self._ttl,
        }
        return {"ticket": ticket, "expires_in": self._ttl}

    def peek(self, ticket: str) -> dict[str, Any] | None:
        entry = self._tickets.get(ticket)
        if entry is None or entry["expires_at"] < time.monotonic():
            return None
        return entry

    def redeem(self, ticket: str) -> dict[str, Any] | None:
        entry = self.peek(ticket)
        if entry is not None:
            self._tickets.pop(ticket, None)
        return entry

    def drain_expired(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        expired: list[dict[str, Any]] = []
        for stale in [
            key for key, value in self._tickets.items()
            if value["expires_at"] < now
        ]:
            entry = self._tickets.pop(stale, None)
            if entry is not None:
                expired.append(entry)
        return expired

    def _sweep(self) -> None:
        self.drain_expired()

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


def _document_list_item(
    d: dict[str, Any], *, referenced: bool = False, kb_name: str | None = None,
) -> dict[str, Any]:
    """文件清单/搜索结果的统一条目形状（57 号：+content_revision/+directory_path）。

    content_revision 是 upload_document 替换语义的「版本暗号」来源——Agent
    从这里读到当前值，替换时原样回传。
    """
    item = {
        "id": d["id"],
        "name": d.get("document_name"),
        "status": "referenced" if referenced else d.get("status"),
        "file_size": d.get("file_size"),
        "modified_at": str(
            d.get("referenced_at") if referenced
            else (d.get("modified_at") or d.get("created_at"))
            or ""
        ),
        "referenced": referenced,
        "content_revision": None if referenced else d.get("content_revision"),
        "directory_path": d.get("directory_path"),
    }
    if kb_name is not None:
        item["kb"] = kb_name
    return item


@router.post("/list-documents", dependencies=[Depends(_require_internal_body)])
async def list_documents(
    body: dict[str, Any], kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    """库内文件清单 / 跨库文件搜索（57 号）。limit≤200，offset 分页。

    - 带 kb_id：该库清单（软删过滤，状态内联派生）。offset=0 时外部引用
      文档合并在后（referenced=true，只读语义，47 号）。
    - 不带 kb_id：**跨该钥匙全部开放库按文件名搜索**（query 必填）——每库
      取回后按 modified_at 合并排序再窗口分页（开放库数量小，近似全局有序）；
      跨库结果不含外部引用文档，条目带 kb=库名。

    状态词表（status 过滤）：uploaded 待挖掘 / mining 挖掘中 / mined 已入库 /
    failed 挖掘失败 / update_failed 更新失败（旧知识仍可检索）。
    """
    key_id = str(body.get("key_id") or "")
    user_id, _key = await _key_scope(
        kbdb, str(body.get("username") or ""), key_id,
    )
    query = str(body.get("query") or "").strip() or None
    directory_prefix = str(body.get("directory_prefix") or "").strip() or None
    status = str(body.get("status") or "").strip() or None
    limit = min(int(body.get("limit") or 50), 200)
    offset = max(int(body.get("offset") or 0), 0)
    kb_id = str(body.get("kb_id") or "")

    if not kb_id:
        if not query:
            raise HTTPException(
                422, "跨库文件搜索必须提供 query（不带 kb_id 时不可纯浏览）。")
        merged: list[dict[str, Any]] = []
        for open_id in await kbdb.key_open_kb_ids(key_id=key_id):
            kb = await kbdb.get_kb(open_id)
            if kb is None or not await kbdb.is_visible(kb_id=open_id, user_id=user_id):
                continue  # 开放后软删/权限收窄：即时从结果消失
            try:
                docs = await kbdb.list_documents_in_kb(
                    kb_id=open_id, query=query, directory_prefix=directory_prefix,
                    status=status, limit=200, offset=0,
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            merged.extend(
                _document_list_item(d, kb_name=str(kb.get("name") or "")) for d in docs
            )
        merged.sort(key=lambda item: str(item["modified_at"] or ""), reverse=True)
        return {"documents": merged[offset:offset + limit], "query": query}

    await _visible_kb(kbdb, user_id, kb_id)
    if kb_id not in await kbdb.key_open_kb_ids(key_id=key_id):
        # 钥匙开放集之外的库：与不可见同语义（404 防探测）
        raise HTTPException(404, f"knowledge base not found: {kb_id}")
    try:
        docs = await kbdb.list_documents_in_kb(
            kb_id=kb_id, query=query, directory_prefix=directory_prefix,
            status=status, limit=limit, offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    out = [_document_list_item(d) for d in docs]
    if offset == 0 and not query:
        for d in await kbdb.list_referenced_documents(kb_id):
            out.append(_document_list_item(d, referenced=True))
    return {"documents": out}


@router.post("/begin-upload", dependencies=[Depends(_require_internal_body)])
async def begin_upload(
    body: dict[str, Any],
    request: Request,
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    """直传第一步：校验权限与文件名，签发一次性上传票据。

    57号D5：body 可选带 document_id + expected_revision = 替换语义（新字节
    替换该文档，名称/目录/身份不变）。签发时即校验：文档存在且属于该库、
    同扩展名、版本暗号匹配（不匹配 409——并发替换互相不盲覆盖）。
    """
    await _expire_upload_tickets(request)
    username = str(body.get("username") or "")
    key_id = str(body.get("key_id") or "")
    user_id, key = await _key_scope(kbdb, username, key_id)
    kb_id = str(body.get("kb_id") or "")
    await _visible_kb(kbdb, user_id, kb_id)
    if kb_id not in await kbdb.key_open_kb_ids(key_id=key_id):
        # 钥匙开放集之外的库：与不可见同语义（404 防探测）
        raise HTTPException(404, f"knowledge base not found: {kb_id}")
    if not await kbdb.can_write(kb_id=kb_id, user_id=user_id):
        raise HTTPException(403, "only owner or editor may upload")
    filename = _validated_filename(body.get("filename"))
    access_record_id = str(body.get("access_record_id") or "")
    try:
        access_record_total = max(1, min(500, int(body.get("access_record_total") or 1)))
    except (TypeError, ValueError):
        access_record_total = 1

    # 57号D5 替换目标校验（全部即时 4xx，票据只签发校验通过的目标）
    document_id = str(body.get("document_id") or "")
    expected_revision: int | None = None
    if document_id:
        raw_revision = body.get("expected_revision")
        if isinstance(raw_revision, bool) or not isinstance(raw_revision, int) or raw_revision < 0:
            raise HTTPException(
                422, "expected_revision 必须是非负整数（该文档当前 content_revision）。")
        expected_revision = raw_revision
        doc = await kbdb.get_document_identity(document_id)
        if doc is None or doc.get("kb_id") != kb_id:
            raise HTTPException(404, f"document not found: {document_id}")
        target_suffix = Path(str(doc.get("document_name") or "")).suffix.lower()
        if Path(filename).suffix.lower() != target_suffix:
            raise HTTPException(
                400, "替换文件须与当前文件格式相同；其他格式请作为新文件上传。")
        if doc.get("content_revision") != expected_revision:
            raise HTTPException(
                409,
                "文件版本已变化（content_revision 不匹配）：请重新读取该文档的"
                " content_revision 后重试。")

    # 归档（zip/hdx/chm）与网页上传同限（默认 500MB）；普通文件走 MCP 上限。
    # 替换目标不可能是归档（同扩展名约束下只能是单文件）。
    max_bytes = (
        UploadConfig().upload_max_archive_size
        if is_upload_archive(filename) and not document_id else MAX_UPLOAD_BYTES
    )
    issued = _TICKETS.issue(
        kb_id=kb_id, user_id=user_id, username=username, filename=filename,
        key_id=key_id,
        access_record_id=access_record_id,
        access_record_total=access_record_total,
        domain=str(key.get("domain") or ""),
        document_id=document_id,
        expected_revision=expected_revision,
    )
    logger.info("[mcp-tools] begin-upload by %s -> kb=%s file=%s ticket=%s replace=%s",
                username, kb_id, filename, issued["ticket"][:11] + "…", bool(document_id))
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

    replace_doc_id = str(entry.get("document_id") or "")

    async def _fail(status: int, message: str, code: str) -> HTTPException:
        await _complete_upload_access_record(
            request, entry, success=False, error_code=code)
        return HTTPException(status, message)

    if replace_doc_id:
        # 57号D5 替换直传：与网页替换同源（replace_content）——CAS 版本暗号 +
        # 同扩展名 + 开箱检查 + 旧知识保底全部继承；成功后同样自动入队挖掘。
        from knowledge_mining.mining.kb.services.document_service import (
            ContentRevisionConflict,
        )
        from knowledge_mining.mining.infra.upload_config import UploadConfig as _UC
        try:
            updated = await doc_svc.replace_content(
                kb_id=entry["kb_id"], document_id=replace_doc_id,
                user_id=entry["user_id"], filename=entry["filename"],
                expected_revision=int(entry.get("expected_revision") or 0),
                stream=_stream(), max_bytes=_UC().upload_max_file_size,
            )
        except ContentRevisionConflict as exc:
            raise await _fail(
                409, f"{exc} 请重新读取该文档的 content_revision 后重试。",
                "revision_conflict")
        except UploadTooLarge as exc:
            raise await _fail(
                413, f"file too large（上限 {exc.limit_bytes} 字节）",
                "upload_too_large")
        except ValueError as exc:
            raise await _fail(400, str(exc), "upload_invalid")
        kb = await kbdb.get_kb(entry["kb_id"])
        auto = (
            await auto_mine.enqueue_auto_mining(
                app_state=request.app.state, kbdb=kbdb, kb=kb,
                user_id=entry["user_id"], username=username,
            )
            if kb is not None else {"auto_mined": False, "reason": "internal"}
        )
        response = {
            "kind": "replace",
            "document_id": updated.get("id"),
            "document_name": updated.get("document_name"),
            "content_revision": updated.get("content_revision"),
            "auto_mined": bool(auto.get("auto_mined")),
            **({"run_id": auto["run_id"]} if auto.get("run_id") else {}),
            **({"reason": auto["reason"]} if auto.get("reason") else {}),
            "message": (
                "已替换原文件并自动排队挖掘；挖完前旧知识继续可检索"
                if auto.get("auto_mined")
                else "已替换原文件；自动挖掘未触发，可请管理员在平台手动发起"
            ),
        }
        await _complete_upload_access_record(
            request, entry, success=True, response_refs=_safe_upload_refs(response))
        return response

    try:
        # 与网页上传同源（intake_upload）：普通文件直传 / zip/hdx/chm 自动解压
        result = await doc_svc.intake_upload(
            kb_id=entry["kb_id"], owner_id=entry["user_id"],
            filename=entry["filename"], stream=_stream(),
            file_max_bytes=MAX_UPLOAD_BYTES,
        )
    except UploadTooLarge as exc:
        await _complete_upload_access_record(
            request, entry, success=False, error_code="upload_too_large")
        raise HTTPException(
            413, f"file too large（上限 {exc.limit_bytes} 字节）"
        ) from exc
    except ValueError as exc:
        await _complete_upload_access_record(
            request, entry, success=False, error_code="upload_invalid")
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
        response = _upload_response(
            document.get("id"), document.get("document_name"), auto, "已上传"
        )
    elif result["kind"] == "archive":
        docs = result["documents"]
        response = {
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
    else:
        # 大归档后台解压中：挖掘 Run 已入队，认领时通常解压已完成（本地磁盘）
        response = {
            "kind": "archive_task",
            "archive_task_id": result["archive_task_id"],
            "status": "processing",
            **auto_fields,
            "message": (
                "归档较大，正在后台解压入库；解压完成后用 get_knowledge(kb_name=…) "
                "可看到全部文档，挖掘任务已排队"
            ),
        }
    await _complete_upload_access_record(
        request, entry, success=True, response_refs=_safe_upload_refs(response),
    )
    return response

async def _expire_upload_tickets(request: Request) -> None:
    for entry in _TICKETS.drain_expired():
        await _complete_upload_access_record(
            request, entry, success=False, error_code="upload_expired",
        )


async def _complete_upload_access_record(
    request: Request,
    entry: dict[str, Any],
    *,
    success: bool,
    error_code: str | None = None,
    response_refs: list[dict[str, Any]] | None = None,
) -> None:
    record_id = str(entry.get("access_record_id") or "")
    domain = str(entry.get("domain") or "")
    if not record_id or not domain:
        return
    total = max(1, int(entry.get("access_record_total") or 1))
    try:
        pool = await request.app.state.domain_pools.async_pool(domain)
        service = RetrievalRecordService(pool)
        if total > 1:
            await service.complete_upload_file(
                record_id=record_id,
                success=success,
                error_code=error_code,
                response_refs=response_refs,
            )
            return

        payload = None
        if response_refs:
            payload = {
                "response_mode": "reference",
                "response_json": None,
                "response_refs_json": response_refs,
                "response_truncated": False,
                "response_omitted_count": 0,
                "redactions_json": [],
                "payload_schema_version": 1,
            }
        await service.write_record({
            "id": record_id,
            "occurred_at": None,
            "completed_at": datetime.now(timezone.utc),
            "domain": domain,
            "actor_user_id": entry.get("user_id"),
            "actor_username": entry.get("username"),
            "source": "mcp",
            "operation": "upload",
            "tool_name": "upload_document",
            "mcp_key_id": entry.get("key_id"),
            "kb_ids": [entry.get("kb_id")] if entry.get("kb_id") else [],
            "query_text": None,
            "paradigm_id": None,
            "paradigm_version": None,
            "status": "success" if success else "failed",
            "result_count": None,
            "duration_ms": None,
            "error_code": error_code if not success else None,
            "details_json": {
                "file_count": 1,
                "completed_count": 1,
                "uploaded_count": 1 if success else 0,
                "failed_count": 0 if success else 1,
                "terminal_owner": "mining",
            },
            **({"payload": payload} if payload is not None else {}),
        })
    except Exception as exc:
        logger.warning(
            "access_record_write_failed",
            extra={
                "record_id": record_id,
                "tool": "upload_document",
                "phase": "upload_complete",
                "error_class": exc.__class__.__name__,
            },
        )


def _safe_upload_refs(response: dict[str, Any]) -> list[dict[str, Any]]:
    common = {
        key: response[key]
        for key in ("run_id", "auto_mined")
        if key in response
    }
    if response.get("kind") in ("file", "replace"):
        return [{
            **common,
            **{
                key: response[key]
                for key in ("document_id", "document_name")
                if response.get(key) is not None
            },
        }]
    if response.get("kind") == "archive":
        return [
            {
                **common,
                **{
                    key: document[key]
                    for key in ("document_id", "document_name")
                    if document.get(key) is not None
                },
            }
            for document in response.get("documents") or []
            if isinstance(document, dict)
        ]
    return [{
        **common,
        **{
            key: response[key]
            for key in ("archive_task_id", "status")
            if response.get(key) is not None
        },
    }]

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
