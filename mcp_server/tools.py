"""MCP 工具族的数据面（批次7）：经 mining 内部端点转发用户级操作。

mcp_server 不直连库：身份与资源授权都在 mining 侧判定（X-Internal-Auth 防线 +
username 信任 + is_visible/can_write 校验），本模块只做 HTTP 转发与形状收敛。
"""
from __future__ import annotations

import base64
import logging

import httpx

from mcp_server.identity import _internal_auth_secret

logger = logging.getLogger(__name__)

MINING_URL = __import__("os").environ.get("MINING_URL", "http://localhost:8901").rstrip("/")
TOOLS_TIMEOUT = float(__import__("os").environ.get("MCP_TOOLS_TIMEOUT", "60.0"))
UPLOAD_TIMEOUT = float(__import__("os").environ.get("MCP_UPLOAD_TIMEOUT", "120.0"))


class ToolBackendError(Exception):
    """上游错误，message 面向 Agent。"""


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


def list_knowledge_bases(username: str) -> dict:
    return _post("/api/kb/mcp-tools/list-kbs", {"username": username})


def list_documents(username: str, kb_id: str, limit: int = 50, offset: int = 0) -> dict:
    return _post("/api/kb/mcp-tools/list-documents", {
        "username": username, "kb_id": kb_id, "limit": limit, "offset": offset,
    })


def get_document(username: str, kb_id: str, document_id: str) -> dict:
    return _post("/api/kb/mcp-tools/get-document", {
        "username": username, "kb_id": kb_id, "document_id": document_id,
    })


def upload_document(username: str, kb_id: str, filename: str, content_b64: str) -> dict:
    return _post(
        "/api/kb/mcp-tools/upload",
        {"username": username, "kb_id": kb_id,
         "filename": filename, "content_b64": content_b64},
        timeout=UPLOAD_TIMEOUT,
    )
