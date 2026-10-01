from __future__ import annotations

import asyncio
import hashlib
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import re
from threading import Lock
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from mcp_server.identity import Identity, MINING_URL, _internal_auth_secret

logger = logging.getLogger(__name__)

current_access_call: ContextVar[dict[str, Any] | None] = ContextVar(
    "mcp_access_call", default=None
)

_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_HTTP_ERROR = re.compile(r"^HTTP\s+(\d{3})$", re.IGNORECASE)
ACCESS_RECORD_WRITE_TIMEOUT_SECONDS = 1.0
ACCESS_RECORD_WRITE_CONCURRENCY = 8
REQUEST_PAYLOAD_MAX_BYTES = 256 * 1024
RESPONSE_PAYLOAD_MAX_BYTES = 4 * 1024 * 1024
PAYLOAD_SCHEMA_VERSION = 1
SANITIZE_MAX_DEPTH = 64
SANITIZE_MAX_NODES = 50_000
UPLOAD_TICKET_TTL_SECONDS = 600
_RECORD_WRITE_SEMAPHORE = asyncio.Semaphore(ACCESS_RECORD_WRITE_CONCURRENCY)
_UPLOAD_LOCK = Lock()
_UPLOAD_TICKETS: dict[str, dict[str, Any]] = {}
_METRICS_LOCK = Lock()
_ACCESS_RECORD_WRITE_FAILURES = 0
_SENSITIVE_KEY_MARKERS = frozenset({
    "authorization", "password", "passwd", "secret", "token", "cookie",
    "uploadurl", "ticket", "apikey", "accesskey", "mcpkey", "jwt",
})
_CONTENT_KEYS = frozenset({
    "content", "text", "body", "raw", "markdown", "html", "bytes", "data",
})

async def post_access_record(payload: dict[str, Any]) -> None:
    """Write without blocking the event loop or allowing unbounded fan-out."""
    secret = _internal_auth_secret()
    if not secret:
        raise RuntimeError("internal auth is not configured")
    async with asyncio.timeout(ACCESS_RECORD_WRITE_TIMEOUT_SECONDS):
        async with _RECORD_WRITE_SEMAPHORE:
            timeout = httpx.Timeout(ACCESS_RECORD_WRITE_TIMEOUT_SECONDS)
            limits = httpx.Limits(
                max_connections=ACCESS_RECORD_WRITE_CONCURRENCY,
                max_keepalive_connections=ACCESS_RECORD_WRITE_CONCURRENCY,
            )
            async with httpx.AsyncClient(
                timeout=timeout, limits=limits, trust_env=False,
            ) as client:
                response = await client.post(
                    f"{MINING_URL}/api/internal/retrieval-records",
                    json=payload,
                    headers={"X-Internal-Auth": secret},
                )
                response.raise_for_status()


async def expire_pending_uploads() -> int:
    """Ask Mining to terminalize orphan pending uploads from prior MCP runs."""

    secret = _internal_auth_secret()
    if not secret:
        raise RuntimeError("internal auth is not configured")
    async with asyncio.timeout(ACCESS_RECORD_WRITE_TIMEOUT_SECONDS):
        timeout = httpx.Timeout(ACCESS_RECORD_WRITE_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                f"{MINING_URL}/api/internal/retrieval-records/"
                "expire-pending-uploads",
                headers={"X-Internal-Auth": secret},
            )
            response.raise_for_status()
            body = response.json()
            return int(body.get("expired_count") or 0)


def increment_access_record_write_failures() -> None:
    """Count best-effort ledger failures without affecting business calls."""
    global _ACCESS_RECORD_WRITE_FAILURES
    with _METRICS_LOCK:
        _ACCESS_RECORD_WRITE_FAILURES += 1


def get_access_record_metrics() -> dict[str, int]:
    with _METRICS_LOCK:
        return {"access_record_write_failures": _ACCESS_RECORD_WRITE_FAILURES}


def reset_access_record_metrics() -> None:
    """Test/operations hook; process restart also resets this process-local metric."""
    global _ACCESS_RECORD_WRITE_FAILURES
    with _METRICS_LOCK:
        _ACCESS_RECORD_WRITE_FAILURES = 0


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")


def _sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


class _SanitizeBudget:
    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self.remaining_bytes = max_bytes
        self.remaining_nodes = SANITIZE_MAX_NODES
        self.truncated = False

    def enter(self) -> bool:
        if self.remaining_nodes <= 0:
            self.truncated = True
            return False
        self.remaining_nodes -= 1
        return True

    def consume(self, value: Any) -> bool:
        if isinstance(value, str):
            size = len(value.encode("utf-8"))
        else:
            size = len(str(value).encode("utf-8"))
        if size > self.remaining_bytes:
            self.truncated = True
            return False
        self.remaining_bytes -= size
        return True


def _sanitizer_marker(reason: str) -> dict[str, Any]:
    return {"__truncated__": True, "__sanitizer_truncated__": reason}


def _sanitize_json(
    value: Any, *, path: str = "", redactions: list[str] | None = None,
    max_bytes: int = RESPONSE_PAYLOAD_MAX_BYTES,
    _budget: _SanitizeBudget | None = None, _depth: int = 0,
) -> Any:
    redactions = redactions if redactions is not None else []
    budget = _budget or _SanitizeBudget(max_bytes)
    if _depth >= SANITIZE_MAX_DEPTH:
        budget.truncated = True
        return _sanitizer_marker("max_depth")
    if not budget.enter():
        return _sanitizer_marker("max_nodes")
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        items = list(value.items())
        for index, (raw_key, raw_value) in enumerate(items):
            key = str(raw_key)
            child_path = f"{path}.{key}" if path else key
            if _sensitive_key(key):
                redactions.append(child_path)
                continue
            if not budget.consume(key):
                sanitized["__sanitizer_truncated__"] = "max_bytes"
                sanitized["__truncated__"] = True
                sanitized["__omitted_count__"] = len(items) - index
                break
            sanitized[key] = _sanitize_json(
                raw_value, path=child_path, redactions=redactions,
                max_bytes=max_bytes, _budget=budget, _depth=_depth + 1,
            )
            if budget.truncated and budget.remaining_bytes <= 0:
                sanitized["__sanitizer_truncated__"] = "max_bytes"
                sanitized["__truncated__"] = True
                sanitized["__omitted_count__"] = len(items) - index - 1
                break
        return sanitized
    if isinstance(value, (list, tuple)):
        sanitized_list: list[Any] = []
        for index, item in enumerate(value):
            if budget.remaining_nodes <= 0 or budget.remaining_bytes <= 0:
                budget.truncated = True
                sanitized_list.append({
                    "__truncated__": True,
                    "__sanitizer_truncated__": (
                        "max_nodes" if budget.remaining_nodes <= 0 else "max_bytes"
                    ),
                    "__omitted_count__": len(value) - index,
                })
                break
            sanitized_list.append(_sanitize_json(
                item, path=f"{path}[{index}]", redactions=redactions,
                max_bytes=max_bytes, _budget=budget, _depth=_depth + 1,
            ))
        return sanitized_list
    if value is None or isinstance(value, (int, float, bool)):
        if budget.consume(value):
            return value
        return _sanitizer_marker("max_bytes")
    if isinstance(value, datetime):
        value = value.isoformat()
    elif not isinstance(value, str):
        value = str(value)
    if budget.consume(value):
        return value
    encoded = value.encode("utf-8")
    preview = encoded[:max(0, budget.remaining_bytes)].decode("utf-8", errors="ignore")
    budget.remaining_bytes = 0
    return {
        "__truncated__": True,
        "__sanitizer_truncated__": "max_bytes",
        "__original_bytes__": len(encoded),
        "__preview__": preview,
    }


def _contains_sanitizer_truncation(value: Any) -> bool:
    if isinstance(value, dict):
        return "__sanitizer_truncated__" in value or any(
            _contains_sanitizer_truncation(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_sanitizer_truncation(item) for item in value)
    return False
def _without_content(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _without_content(item)
            for key, item in value.items()
            if re.sub(r"[^a-z0-9]", "", str(key).casefold()) not in _CONTENT_KEYS
        }
    if isinstance(value, list):
        return [_without_content(item) for item in value]
    return value


def _truncation_envelope(
    encoded: bytes, *, max_bytes: int, digest: str,
) -> dict[str, Any]:
    preview_limit = max(0, max_bytes - 256)
    preview = encoded[:preview_limit].decode("utf-8", errors="ignore")
    envelope: dict[str, Any] = {
        "__truncated__": True,
        "__original_bytes__": len(encoded),
        "__sha256__": digest,
        "__preview__": preview,
    }
    if b"__sanitizer_truncated__" in encoded:
        envelope["__sanitizer_truncated__"] = "budget"
    while len(_json_bytes(envelope)) > max_bytes and preview:
        overshoot = len(_json_bytes(envelope)) - max_bytes
        preview = preview[:max(0, len(preview) - max(1, overshoot))]
        envelope["__preview__"] = preview
    return envelope


def _bounded_json(
    value: Any, *, max_bytes: int,
) -> tuple[Any, int, int, bool, int, str]:
    encoded = _json_bytes(value)
    original_bytes = len(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    if original_bytes <= max_bytes:
        return value, original_bytes, original_bytes, False, 0, digest

    if isinstance(value, dict):
        for collection_key in (
            "evidence", "rows", "items", "segments", "documents", "nodes", "results",
        ):
            items = value.get(collection_key)
            if not isinstance(items, list) or not items:
                continue
            candidate = {**value, collection_key: []}
            if len(_json_bytes(candidate)) > max_bytes:
                continue
            low, high = 0, len(items)
            while low < high:
                middle = (low + high + 1) // 2
                candidate[collection_key] = items[:middle]
                if len(_json_bytes(candidate)) <= max_bytes:
                    low = middle
                else:
                    high = middle - 1
            candidate[collection_key] = items[:low]
            stored_bytes = len(_json_bytes(candidate))
            return (
                candidate, stored_bytes, original_bytes, True,
                max(1, len(items) - low), digest,
            )

    envelope = _truncation_envelope(
        encoded, max_bytes=max_bytes, digest=digest,
    )
    return envelope, len(_json_bytes(envelope)), original_bytes, True, 1, digest


def _capture_payload(
    *, tool_name: str, arguments: dict[str, Any], identity: Identity | None,
    body: dict[str, Any], state: dict[str, Any] | None,
) -> dict[str, Any]:
    redactions: list[str] = []
    safe_request = _sanitize_json(
        arguments, redactions=redactions, max_bytes=REQUEST_PAYLOAD_MAX_BYTES,
    )
    effective_context: dict[str, Any] = {
        "domain": _domain(arguments, identity),
        "kb_ids": _kb_ids(arguments, identity),
    }
    if state and state.get("paradigm_id"):
        effective_context["paradigm_id"] = state["paradigm_id"]
    if state and state.get("paradigm_version") is not None:
        effective_context["paradigm_version"] = state["paradigm_version"]

    effective_bytes = len(_json_bytes(effective_context))
    request_budget = max(512, REQUEST_PAYLOAD_MAX_BYTES - effective_bytes)
    request_json, stored_request_bytes, _, _, _, _ = _bounded_json(
        safe_request, max_bytes=request_budget,
    )
    request_bytes = stored_request_bytes + effective_bytes
    safe_response = (
        _sanitize_json(body, redactions=redactions, max_bytes=RESPONSE_PAYLOAD_MAX_BYTES)
        if body else None
    )
    response_mode = "summary"
    response_json = None
    response_refs_json: list[Any] = []
    if safe_response is not None:
        view = str(safe_response.get("view") or "")
        if tool_name == "get_knowledge" and view in {
            "evidence_content", "document_content",
        }:
            response_mode = "reference"
            response_refs_json = [_without_content(safe_response)]
        elif tool_name == "manage_files":
            response_mode = "reference"
            response_refs_json = [safe_response]
        else:
            response_mode = "snapshot"
            response_json = safe_response

    response_value = (
        response_json if response_json is not None else response_refs_json
    )
    reference_overhead = len(_json_bytes(response_refs_json)) if response_json is not None else 0
    response_budget = max(512, RESPONSE_PAYLOAD_MAX_BYTES - reference_overhead)
    (
        bounded_response, stored_response_bytes, original_response_bytes,
        response_truncated, response_omitted_count, _,
    ) = _bounded_json(response_value, max_bytes=response_budget)
    if response_json is not None:
        response_json = bounded_response
    else:
        response_refs_json = bounded_response
    response_bytes = stored_response_bytes + reference_overhead
    response_original_bytes = original_response_bytes + reference_overhead
    if _contains_sanitizer_truncation(response_value):
        response_truncated = True
        response_omitted_count = max(1, response_omitted_count)
        response_original_bytes = max(response_original_bytes, response_bytes + 1)
    response_sha256 = hashlib.sha256(_json_bytes({
        "response_json": safe_response if response_json is not None else None,
        "response_refs_json": (
            [] if response_json is not None else response_value
        ),
    })).hexdigest()

    return {
        "request_json": request_json,
        "effective_context_json": effective_context,
        "response_mode": response_mode,
        "response_json": response_json,
        "response_refs_json": response_refs_json,
        "request_bytes": request_bytes,
        "response_bytes": response_bytes,
        "response_truncated": response_truncated,
        "response_original_bytes": response_original_bytes,
        "response_omitted_count": response_omitted_count,
        "response_sha256": response_sha256,
        "redactions_json": sorted(set(redactions)),
        "payload_schema_version": PAYLOAD_SCHEMA_VERSION,
    }

def build_access_payload(
    *,
    call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    identity: Identity | None,
    result: Any,
    failure: BaseException | None,
    duration_ms: int,
    forced_status: str | None = None,
) -> dict[str, Any]:
    body = _result_dict(result)
    status, error_code = _status(tool_name, body, failure, forced_status)
    if failure is not None and not body:
        body = {
            "error": error_code or "tool_failed",
            "message": _public_error_message(status),
        }
    payload: dict[str, Any] = {
        "id": call_id,
        "domain": _domain(arguments, identity),
        "actor_user_id": identity.user_id if identity else None,
        "actor_username": identity.username if identity else None,
        "source": "mcp",
        "operation": _operation(tool_name, arguments),
        "tool_name": tool_name,
        "mcp_key_id": identity.key_id if identity else None,
        "kb_ids": _kb_ids(arguments, identity),
        "query_text": (
            str(arguments.get("query") or "")[:4000] or None
            if tool_name == "search_knowledge" else None
        ),
        "status": status,
        "result_count": _result_count(tool_name, body, status),
        "duration_ms": max(0, int(duration_ms)),
        "error_code": error_code,
        "details_json": _details(tool_name, arguments, body),
    }
    if status != "pending":
        payload["completed_at"] = datetime.now(timezone.utc).isoformat()
        payload["details_json"] = {
            **dict(payload.get("details_json") or {}),
            "terminal_owner": "mcp",
        }
    state = current_access_call.get()
    if state:
        if state.get("paradigm_id"):
            payload["paradigm_id"] = state["paradigm_id"]
        if state.get("paradigm_version") is not None:
            payload["paradigm_version"] = state["paradigm_version"]
    payload["payload"] = _capture_payload(
        tool_name=tool_name, arguments=arguments, identity=identity,
        body=body, state=state,
    )
    if failure is not None:
        payload["payload"]["response_mode"] = "summary"
    return payload


def register_upload_tickets(payload: dict[str, Any], result: Any) -> None:
    body = _result_dict(result)
    uploads = body.get("uploads")
    if not isinstance(uploads, list) or not uploads:
        return
    group = {
        "payload": dict(payload),
        "total": len(uploads),
        "completed": 0,
        "failed": 0,
        "tickets": set(),
        "expires_at": time.monotonic() + _upload_ticket_ttl(uploads),
    }
    with _UPLOAD_LOCK:
        _sweep_upload_tickets()
        for upload in uploads:
            if not isinstance(upload, dict):
                continue
            path = urlparse(str(upload.get("upload_url") or "")).path
            ticket = path.rsplit("/", 1)[-1]
            if not ticket:
                continue
            group["tickets"].add(ticket)
            _UPLOAD_TICKETS[ticket] = group


def complete_upload_ticket(
    ticket: str, *, success: bool, error_code: str | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    with _UPLOAD_LOCK:
        _sweep_upload_tickets()
        group = _UPLOAD_TICKETS.pop(ticket, None)
        if group is None:
            return None
        group["completed"] += 1
        if not success:
            group["failed"] += 1
        terminal = group["completed"] >= group["total"]
        payload = dict(group["payload"])
        payload["status"] = (
            "failed" if terminal and group["failed"]
            else "success" if terminal
            else "pending"
        )
        payload["error_code"] = (
            (error_code or "upload_failed")
            if terminal and group["failed"] else None
        )
        captured = dict(payload.get("payload") or {})
        response_refs = list(captured.get("response_refs_json") or [])
        completed_refs = []
        if isinstance(result, dict):
            result_redactions: list[str] = []
            safe_result = _sanitize_json(result, redactions=result_redactions)
            allowed = {
                key: safe_result[key]
                for key in (
                    "filename", "document_id", "document_ref", "run_id",
                    "auto_mined", "status", "error_code",
                )
                if key in safe_result
            }
            if allowed:
                completed_refs.append(allowed)
            captured["redactions_json"] = sorted(set(
                list(captured.get("redactions_json") or []) + result_redactions
            ))
        if completed_refs:
            response_refs.extend(completed_refs)
            (
                bounded_refs, response_bytes, response_original_bytes,
                response_truncated, response_omitted_count, response_sha256,
            ) = _bounded_json(
                response_refs, max_bytes=RESPONSE_PAYLOAD_MAX_BYTES,
            )
            captured.update({
                "response_refs_json": bounded_refs,
                "response_bytes": response_bytes,
                "response_original_bytes": response_original_bytes,
                "response_truncated": response_truncated,
                "response_omitted_count": response_omitted_count,
                "response_sha256": response_sha256,
            })
            payload["payload"] = captured
            group["payload"] = dict(payload)
        payload["details_json"] = {
            **dict(payload.get("details_json") or {}),
            "uploaded_count": group["completed"] - group["failed"],
            "failed_count": group["failed"],
        }
        if terminal:
            payload["completed_at"] = datetime.now(timezone.utc).isoformat()
            payload["details_json"] = {
                **dict(payload.get("details_json") or {}),
                "terminal_owner": "mcp",
            }
            for sibling in group["tickets"]:
                _UPLOAD_TICKETS.pop(sibling, None)
        else:
            payload.pop("completed_at", None)
        return payload


def _upload_ticket_ttl(uploads: list[Any]) -> int:
    values = []
    for upload in uploads:
        if not isinstance(upload, dict):
            continue
        try:
            ttl = int(upload.get("expires_in") or UPLOAD_TICKET_TTL_SECONDS)
        except (TypeError, ValueError):
            ttl = UPLOAD_TICKET_TTL_SECONDS
        values.append(max(1, min(ttl, UPLOAD_TICKET_TTL_SECONDS)))
    return min(values, default=UPLOAD_TICKET_TTL_SECONDS)


def _sweep_upload_tickets() -> None:
    now = time.monotonic()
    expired_groups = {
        id(group)
        for group in _UPLOAD_TICKETS.values()
        if float(group.get("expires_at") or 0) <= now
    }
    if not expired_groups:
        return
    for ticket, group in list(_UPLOAD_TICKETS.items()):
        if id(group) in expired_groups:
            _UPLOAD_TICKETS.pop(ticket, None)


def _result_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    for name in ("structuredContent", "structured_content"):
        value = getattr(result, name, None)
        if isinstance(value, dict):
            return value
    return {}


def _domain(arguments: dict[str, Any], identity: Identity | None) -> str:
    if identity and identity.key_domain:
        return identity.key_domain
    explicit = arguments.get("domain")
    return str(explicit).strip() if explicit and str(explicit).strip() else "unknown"


def _operation(tool_name: str, arguments: dict[str, Any]) -> str:
    """58号§5：manage_files 按 action 分记——upload（新增）/replace（替换）。"""
    if tool_name == "manage_files":
        return "replace" if str(arguments.get("action") or "") == "replace" else "upload"
    return {
        "search_knowledge": "search",
        "get_knowledge": "read",
    }.get(tool_name, "read")


def _kb_ids(arguments: dict[str, Any], identity: Identity | None) -> list[str]:
    if identity is None:
        return []
    by_name = {
        str(item.get("name") or "").strip().casefold(): str(item.get("id") or "")
        for item in identity.open_kbs
    }
    single = arguments.get("kb_name")
    if single:
        hit = by_name.get(str(single).strip().casefold())
        return [hit] if hit else []
    names = arguments.get("kb_names")
    if isinstance(names, list) and names:
        return [
            by_name[str(name).strip().casefold()]
            for name in names
            if str(name).strip().casefold() in by_name
        ]
    return list(identity.open_kb_ids)


def _status(
    tool_name: str,
    body: dict[str, Any],
    failure: BaseException | None,
    forced_status: str | None,
) -> tuple[str, str | None]:
    if forced_status:
        return forced_status, "access_denied" if forced_status == "denied" else None
    if failure is not None:
        classified_status = getattr(failure, "ledger_status", None)
        classified_code = str(getattr(failure, "error_code", "") or "")
        if classified_status in {"denied", "invalid", "timeout", "failed"}:
            return (
                classified_status,
                classified_code
                if _ERROR_CODE.fullmatch(classified_code)
                else "tool_failed",
            )
        name = failure.__class__.__name__.lower()
        if "timeout" in name:
            return "timeout", "tool_timeout"
        if name.endswith("toolerror"):
            return "invalid", "invalid_tool_call"
        return "failed", "tool_failed"
    error = body.get("error")
    if error:
        code = str(error)
        match = _HTTP_ERROR.fullmatch(code.strip())
        if match:
            status_code = int(match.group(1))
            safe_code = f"http_{status_code}"
            if status_code in {401, 403}:
                return "denied", safe_code
            if status_code in {408, 504}:
                return "timeout", safe_code
            if status_code in {400, 404, 409, 410, 413, 422}:
                return "invalid", safe_code
            return "failed", safe_code
        safe = code if _ERROR_CODE.fullmatch(code) else "tool_failed"
        return ("timeout" if "timeout" in safe else "failed"), safe
    if tool_name == "manage_files":
        return "pending", None
    if tool_name == "search_knowledge" and not (body.get("evidence") or []):
        return "no_result", None
    return "success", None


def _public_error_message(status: str) -> str:
    return {
        "denied": "无权执行该操作。",
        "invalid": "调用参数无效。",
        "timeout": "调用超时，请稍后重试。",
    }.get(status, "检索执行失败，请稍后重试。")
def _result_count(
    tool_name: str, body: dict[str, Any], status: str,
) -> int | None:
    if tool_name == "search_knowledge" and status in {"success", "no_result"}:
        evidence = body.get("evidence")
        return len(evidence) if isinstance(evidence, list) else 0
    return None


def _details(
    tool_name: str, arguments: dict[str, Any], body: dict[str, Any],
) -> dict[str, Any]:
    if tool_name == "manage_files":
        filenames = arguments.get("filenames")
        return {"file_count": len(filenames) if isinstance(filenames, list) else 0,
                "action": str(arguments.get("action") or "")}
    if tool_name != "get_knowledge":
        return {}
    view = str(body.get("view") or _read_action(arguments))
    details: dict[str, Any] = {"action": view}
    ref = str(arguments.get("ref") or "")
    if ref.startswith("ev_"):
        details["ref_type"] = "evidence"
    elif ref.startswith("doc_"):
        details["ref_type"] = "document"
    elif ref:
        details["ref_type"] = "structure"
    return details


def _read_action(arguments: dict[str, Any]) -> str:
    ref = str(arguments.get("ref") or "")
    if not ref:
        return "documents" if arguments.get("kb_name") else "kb_tree"
    if ref.startswith("ev_"):
        return "evidence_content"
    if ref.startswith("doc_"):
        return "document_content"
    if arguments.get("query") is not None:
        return "structured_query"
    if arguments.get("relation"):
        return "navigation"
    return "capabilities"


__all__ = [
    "ACCESS_RECORD_WRITE_TIMEOUT_SECONDS",
    "REQUEST_PAYLOAD_MAX_BYTES",
    "RESPONSE_PAYLOAD_MAX_BYTES",
    "build_access_payload",
    "complete_upload_ticket",
    "current_access_call",
    "get_access_record_metrics",
    "expire_pending_uploads",
    "increment_access_record_write_failures",
    "post_access_record",
    "register_upload_tickets",
    "reset_access_record_metrics",
]
