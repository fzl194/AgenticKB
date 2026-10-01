"""MCP 工具族的数据面（批次7 + 批次8 R8）：内部端点转发用户级操作。

两条转发面：
- mining 内部端点（批次7）：list/upload 等管理浏览类工具；
- serving 内部端点（批次8 R7/R8）：get_evidence / get_document / inspect_knowledge /
  navigate_structure / query_structured_asset——带 X-Internal-Auth（密钥 =
  serving 的 SERVING_INTERNAL_AUTH_SECRET，与 mining 的 internal_verify_secret 相互独立）。

mcp_server 不直连库：身份与资源授权都在上游判定（内部密钥防线 + username 信任 +
资源级校验），本模块只做 HTTP 转发与形状收敛。serving 的 typed error
（25 号 §7.2）以 ``ServingToolError`` 原样上抛，供工具层转成 Agent 可修正的错误信息。
"""
from __future__ import annotations

import base64
import logging
import os

import httpx

from mcp_server.identity import _internal_auth_secret

logger = logging.getLogger(__name__)

MINING_URL = os.environ.get("MINING_URL", "http://localhost:8901").rstrip("/")
TOOLS_TIMEOUT = float(os.environ.get("MCP_TOOLS_TIMEOUT", "60.0"))
#: 直传流式 PUT 的整体超时：50MB 慢速上链也要能传完（票据 TTL 600s 对齐）。
UPLOAD_TIMEOUT = float(os.environ.get("MCP_UPLOAD_TIMEOUT", "600.0"))

#: serving internal REST（批次8 R7）：容器内同网 127.0.0.1:8081。
SERVING_INTERNAL_URL = os.environ.get(
    "SERVING_INTERNAL_URL", "http://127.0.0.1:8081").rstrip("/")
SERVING_TOOLS_TIMEOUT = float(os.environ.get("SERVING_TOOLS_TIMEOUT", "60.0"))


class ToolBackendError(Exception):
    """上游错误，message 面向 Agent。"""


class ServingToolError(ToolBackendError):
    """serving 结构工具的 typed error（§7.2）：code + details 供 Agent 反馈式重试。"""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _post(path: str, payload: dict, *, timeout: float = TOOLS_TIMEOUT) -> dict:
    secret = _internal_auth_secret()
    if not secret:
        raise ToolBackendError("服务端未完成内部鉴权配置，请联系管理员。")
    try:
        resp = httpx.post(
            f"{MINING_URL}{path}",
            json=payload,
            headers={"X-Internal-Auth": secret},
            timeout=timeout,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        logger.warning("mcp-tools %s unreachable: %s", path, exc)
        raise ToolBackendError("知识服务暂不可用，请稍后重试。") from None
    if resp.status_code == 404:
        raise ToolBackendError("目标知识库或文档不存在（或你对它没有权限）。")
    if resp.status_code == 403:
        raise ToolBackendError("当前身份无权执行该操作（需要库的编辑权限）。")
    if resp.status_code == 413:
        raise ToolBackendError("文件过大：MCP 上传上限 50MB。")
    if resp.status_code != 200:
        detail = ""
        try:
            detail = str(resp.json().get("detail") or "")[:120]
        except Exception:
            detail = resp.text[:120]
        raise ToolBackendError(f"操作失败（HTTP {resp.status_code}）：{detail}")
    return resp.json()


def _serving_secret() -> str:
    """serving 内部密钥：独立 env（与 mining 的 MCP_INTERNAL_AUTH_SECRET 分开管理）。"""
    return os.environ.get("SERVING_INTERNAL_AUTH_SECRET", "").strip()


def _post_serving(path: str, payload: dict) -> dict:
    """POST serving /api/internal/*（X-Internal-Auth；typed error 原样上抛）。"""
    secret = _serving_secret()
    if not secret:
        logger.error("SERVING_INTERNAL_AUTH_SECRET 未配置——结构工具不可用")
        raise ToolBackendError("检索服务端未完成内部鉴权配置，请联系管理员。")
    try:
        resp = httpx.post(
            f"{SERVING_INTERNAL_URL}{path}",
            json=payload,
            headers={"X-Internal-Auth": secret},
            timeout=SERVING_TOOLS_TIMEOUT,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        logger.warning("serving-internal %s unreachable: %s", path, exc)
        raise ToolBackendError("检索服务暂不可用，请稍后重试。") from None
    if resp.status_code != 200:
        _raise_serving_error(resp)
    try:
        return resp.json()
    except ValueError:
        raise ToolBackendError("检索服务返回了无法解析的响应。") from None


def _raise_serving_error(resp: httpx.Response) -> None:
    """把 serving 的稳定错误体 {"error": {code, message, details}} 转成 typed 异常。"""
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict) and err.get("code"):
            raise ServingToolError(
                str(err["code"]),
                str(err.get("message") or err["code"]),
                err.get("details") if isinstance(err.get("details"), dict) else {},
            )
        detail = str((body or {}).get("detail") or body)[:160]
    except ValueError:
        detail = resp.text[:160]
    raise ToolBackendError(f"操作失败（HTTP {resp.status_code}）：{detail}")


# ── serving 结构工具族（批次8 R7/R8，25 号 §8.1） ─────────────────────────


def get_evidence(
    username: str, kb_ids: list[str], domain: str, ref: str, mode: str | None = None
) -> dict:
    """ev_ ref → 完整/更大粒度原文（EvidenceResponse truncated=true 的取回通道）。"""
    payload: dict = {"domain": domain, "kb_ids": kb_ids, "username": username}
    if mode:
        payload["mode"] = mode
    return _post_serving(f"/api/internal/evidence/{ref}", payload)


def get_document(
    username: str, kb_ids: list[str], domain: str, ref: str,
    limit: int | None = None, cursor: str | None = None,
) -> dict:
    """doc_ ref → 结构化章节（有界稳定分页；不再要求 kb name + 内部 document id）。"""
    payload: dict = {"domain": domain, "kb_ids": kb_ids, "username": username}
    if limit is not None:
        payload["limit"] = limit
    if cursor:
        payload["cursor"] = cursor
    return _post_serving(f"/api/internal/document/{ref}", payload)


def inspect_knowledge(username: str, kb_ids: list[str], domain: str, ref: str) -> dict:
    """document_ref/structure_ref/asset_ref/evidence ref → capabilities/schema/relations。"""
    return _post_serving("/api/internal/inspect", {
        "domain": domain, "kb_ids": kb_ids, "username": username, "ref": ref,
    })


def navigate_structure(
    username: str, kb_ids: list[str], domain: str, ref: str, relation: str,
    depth: int | None = None, limit: int | None = None, cursor: str | None = None,
) -> dict:
    """st_ ref + 白名单关系导航（public refs + stable cursor）。"""
    payload: dict = {
        "domain": domain, "kb_ids": kb_ids, "username": username,
        "ref": ref, "relation": relation,
    }
    if depth is not None:
        payload["depth"] = depth
    if limit is not None:
        payload["limit"] = limit
    if cursor:
        payload["cursor"] = cursor
    return _post_serving("/api/internal/navigate", payload)


def query_structured_asset(
    username: str, kb_ids: list[str], domain: str, ref: str, query: dict,
) -> dict:
    """st_ asset ref + schema-bound DSL（filter/select/order/aggregate，typed rows）。"""
    return _post_serving("/api/internal/structured-query", {
        "domain": domain, "kb_ids": kb_ids, "username": username, "ref": ref, "query": query,
    })


def list_knowledge_bases(username: str, key_id: str) -> dict:
    return _post("/api/kb/mcp-tools/list-kbs", {
        "username": username, "key_id": key_id,
    })


def list_documents(
    username: str, key_id: str, kb_id: str | None,
    limit: int = 50, offset: int = 0,
    *, query: str | None = None, directory_prefix: str | None = None,
    status: str | None = None,
) -> dict:
    """库内文件清单（kb_id 必给）或跨开放库文件搜索（kb_id=None 时 query 必给）。"""
    payload: dict = {
        "username": username, "key_id": key_id,
        "limit": limit, "offset": offset,
    }
    if kb_id:
        payload["kb_id"] = kb_id
    if query:
        payload["query"] = query
    if directory_prefix:
        payload["directory_prefix"] = directory_prefix
    if status:
        payload["status"] = status
    return _post("/api/kb/mcp-tools/list-documents", payload)


def browse_directory(
    username: str, key_id: str, kb_id: str,
    directory: str, limit: int = 50, offset: int = 0,
) -> dict:
    """目录逐层浏览（58号§3）：直属子目录（空目录可见）+ 直属文件（不递归）。"""
    return _post("/api/kb/mcp-tools/browse-directory", {
        "username": username, "key_id": key_id, "kb_id": kb_id,
        "directory": directory, "limit": limit, "offset": offset,
    })


# 注：批次8 R8 起 get_document 切 serving document_ref 通道（见上方 get_document）。
# mining 的旧 /api/kb/mcp-tools/get-document 端点已随代码瘦身批次3 删除。


def begin_upload(
    username: str,
    key_id: str,
    kb_id: str,
    filename: str,
    *,
    directory: str = "",
    document_id: str = "",
    expected_revision: int | None = None,
    access_record_id: str = "",
    access_record_total: int = 1,
) -> dict:
    """直传第一步：签发一次性上传票据（mining 校验权限/文件名后返回）。

    directory（58号§2.5）= 上传目标目录（根=空串/不传）：mining 校验形状与
    存在性（不自动创建）后绑定进票据，PUT 无从篡改。
    document_id + expected_revision（57号D5）= 替换语义票据：mining 即时校验
    同扩展名与版本暗号（不匹配 409）。
    """
    payload: dict = {
        "username": username, "key_id": key_id, "kb_id": kb_id,
        "filename": filename,
        "access_record_id": access_record_id,
        "access_record_total": access_record_total,
    }
    if directory:
        payload["directory"] = directory
    if document_id:
        payload["document_id"] = document_id
        payload["expected_revision"] = expected_revision or 0
    return _post("/api/kb/mcp-tools/begin-upload", payload)


async def put_upload_direct(ticket: str, stream) -> tuple[int, dict]:
    """直传第二步：把 Agent 的原始字节流式转发给 mining（无 base64）。

    归属用户由票据绑定值决定（mining 侧消费），本层不传用户名——公网
    PUT 不验 MCP 密钥（密钥只在 MCP 客户端，模型不可见；票据即凭证）。
    返回 (http_status, body)；把 mining 的状态码语义原样带回给自定义路由。
    """
    secret = _internal_auth_secret()
    if not secret:
        raise ToolBackendError("服务端未完成内部鉴权配置，请联系管理员。")
    import httpx

    try:
        async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT, trust_env=False) as client:
            resp = await client.put(
                f"{MINING_URL}/api/kb/mcp-tools/upload-direct/{ticket}",
                content=stream,
                headers={
                    "X-Internal-Auth": secret,
                    "Content-Type": "application/octet-stream",
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("mcp-upload-direct %s unreachable: %s", ticket[:11], exc)
        raise ToolBackendError("知识服务暂不可用，请稍后重试。") from None
    try:
        body = resp.json()
    except ValueError:
        body = {"detail": resp.text[:200]}
    return resp.status_code, body
