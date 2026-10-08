"""Unified retrieval-record read APIs and trusted-service write endpoint."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knowledge_mining.mining.api.domain_scope import require_domain
from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.routes.auth import _require_internal
from knowledge_mining.mining.services.retrieval_records import RetrievalRecordService


router = APIRouter(tags=["retrieval-records"])

Source = Literal["web", "mcp", "api"]
# 58号（codex P1-2）：manage_files 的 replace 动作独立分类——与 DB CHECK
# （006 迁移）同步；漏改此处会把 replace 记录 422 拒掉且被降级成日志，审计缺口。
Operation = Literal["search", "read", "upload", "replace"]
RecordStatus = Literal[
    "pending", "success", "no_result", "denied", "invalid", "timeout", "failed"
]
_ALLOWED_DETAIL_FIELDS = frozenset({
    "action",
    "ref_type",
    "file_count",
    "uploaded_count",
    "failed_count",
    "engine",
    "output",
    "channel",
    "has_more",
    "mode",
    "terminal_owner",
})
_SENSITIVE_DETAIL_KEY_PATTERN = re.compile(
    r"token|secret|password|passphrase|authorization|api[_-]?key|jwt|"
    r"mcp[_-]?key|cookie|credential",
    re.IGNORECASE,
)


def _reject_sensitive_keys(value: Any, *, field_name: str) -> Any:
    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if _SENSITIVE_DETAIL_KEY_PATTERN.search(str(key)):
                    raise ValueError(f"{field_name} contains a sensitive key")
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return value


def _json_size(value: Any) -> int:
    if value is None:
        return 0
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


class RetrievalRecordPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_json: Any | None = None
    effective_context_json: Any | None = None
    response_mode: Literal["snapshot", "reference", "summary"]
    response_json: Any | None = None
    response_refs_json: list[Any] = Field(default_factory=list)
    request_bytes: int | None = Field(default=None, ge=0)
    response_bytes: int | None = Field(default=None, ge=0)
    response_truncated: bool = False
    response_original_bytes: int | None = Field(default=None, ge=0)
    response_omitted_count: int = Field(default=0, ge=0)
    response_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    redactions_json: list[Any] = Field(default_factory=list)
    payload_schema_version: int = Field(default=1, ge=1)

    @field_validator(
        "request_json",
        "effective_context_json",
        "response_json",
        "response_refs_json",
        "redactions_json",
    )
    @classmethod
    def _reject_structured_secrets(cls, value: Any, info: Any) -> Any:
        return _reject_sensitive_keys(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _validate_and_measure(self) -> "RetrievalRecordPayload":
        request_bytes = _json_size(self.request_json) + _json_size(
            self.effective_context_json
        )
        response_bytes = _json_size(self.response_json) + _json_size(
            self.response_refs_json
        )
        if request_bytes > 256 * 1024:
            raise ValueError("payload request data exceeds 256 KiB")
        if response_bytes > 4 * 1024 * 1024:
            raise ValueError("payload response data exceeds 4 MiB")
        if (
            self.response_original_bytes is not None
            and self.response_original_bytes < response_bytes
        ):
            raise ValueError(
                "response_original_bytes must not be smaller than response_bytes"
            )
        self.request_bytes = request_bytes
        self.response_bytes = response_bytes
        if self.response_original_bytes is None:
            self.response_original_bytes = response_bytes
        if self.response_sha256 is None and response_bytes:
            canonical_response = json.dumps(
                {
                    "response_json": self.response_json,
                    "response_refs_json": self.response_refs_json,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            self.response_sha256 = hashlib.sha256(canonical_response).hexdigest()
        return self


class RetrievalRecordWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    occurred_at: datetime | None = None
    completed_at: datetime | None = None
    domain: str = Field(min_length=1, max_length=200)
    actor_user_id: str | None = Field(default=None, max_length=200)
    actor_username: str | None = Field(default=None, max_length=200)
    source: Source
    operation: Operation
    tool_name: str | None = Field(default=None, max_length=200)
    mcp_key_id: str | None = Field(default=None, max_length=200)
    kb_ids: list[str] = Field(default_factory=list, max_length=500)
    query_text: str | None = Field(default=None, max_length=20_000)
    paradigm_id: str | None = Field(default=None, max_length=200)
    paradigm_version: int | None = Field(default=None, ge=0)
    status: RecordStatus
    result_count: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, max_length=200)
    details_json: dict[str, Any] = Field(default_factory=dict)
    payload: RetrievalRecordPayload | None = None

    @field_validator("domain", "id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("kb_ids")
    @classmethod
    def _normalize_kb_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("kb_ids must not contain blanks")
        if any(len(value) > 200 for value in normalized):
            raise ValueError("kb_id is too long")
        return list(dict.fromkeys(normalized))

    @field_validator("details_json")
    @classmethod
    def _reject_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_sensitive_keys(value, field_name="details_json")

        unknown = set(value) - _ALLOWED_DETAIL_FIELDS
        if unknown:
            raise ValueError("details_json contains an unsupported field")
        terminal_owner = value.get("terminal_owner")
        if terminal_owner is not None and terminal_owner not in {
            "serving", "mcp", "mining"
        }:
            raise ValueError("details_json contains an invalid terminal_owner")
        if any(isinstance(item, (dict, list)) for item in value.values()):
            raise ValueError("details_json fields must be scalar")
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 32_768:
            raise ValueError("details_json is too large")
        return value

    @model_validator(mode="after")
    def _complete_terminal_record(self) -> "RetrievalRecordWrite":
        for stamp in (self.occurred_at, self.completed_at):
            if stamp is not None and stamp.utcoffset() is None:
                raise ValueError("timestamps must include a timezone")
        if self.status != "pending" and self.completed_at is None:
            self.completed_at = datetime.now(timezone.utc)
        if (
            self.occurred_at is not None
            and self.completed_at is not None
            and self.completed_at < self.occurred_at
        ):
            raise ValueError("completed_at must not precede occurred_at")
        return self


def build_service(pool: Any) -> RetrievalRecordService:
    return RetrievalRecordService(pool)


async def _service(request: Request, domain: str) -> RetrievalRecordService:
    resolved = require_domain(domain)
    pool = await request.app.state.domain_pools.async_pool(resolved)
    return build_service(pool)


@router.get("/api/retrieval-records/summary")
async def retrieval_summary(
    request: Request,
    domain: str = Query(..., min_length=1),
    days: int = Query(7, ge=1, le=90),
    kb_id: str | None = Query(default=None, min_length=1),
    source: Source | None = None,
    tool_name: str | None = Query(default=None, min_length=1),
    operation: Operation | None = None,
    mcp_key_id: str | None = Query(default=None, min_length=1),
    status: RecordStatus | None = None,
    paradigm_id: str | None = Query(default=None, min_length=1),
    actor_user_id: str | None = Query(default=None, min_length=1),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    resolved = require_domain(domain)
    service = await _service(request, resolved)
    try:
        return await service.summary(
            domain=resolved,
            days=days,
            kb_id=kb_id,
            source=source,
            tool_name=tool_name,
            operation=operation,
            mcp_key_id=mcp_key_id,
            status=status,
            paradigm_id=paradigm_id,
            actor_user_id=actor_user_id,
            user=user,
        )
    except PermissionError as exc:
        raise HTTPException(404, "knowledge base not found") from exc


@router.get("/api/retrieval-records")
async def list_retrieval_records(
    request: Request,
    domain: str = Query(..., min_length=1),
    days: int = Query(7, ge=1, le=90),
    source: Source | None = None,
    tool_name: str | None = Query(default=None, min_length=1),
    operation: Operation | None = None,
    kb_id: str | None = Query(default=None, min_length=1),
    mcp_key_id: str | None = Query(default=None, min_length=1),
    status: RecordStatus | None = None,
    paradigm_id: str | None = Query(default=None, min_length=1),
    actor_user_id: str | None = Query(default=None, min_length=1),
    cursor: str | None = Query(default=None, min_length=3, max_length=500),
    page_size: int = Query(50, ge=1, le=200),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    resolved = require_domain(domain)
    service = await _service(request, resolved)
    try:
        return await service.list_records(
            domain=resolved,
            days=days,
            source=source,
            tool_name=tool_name,
            operation=operation,
            kb_id=kb_id,
            mcp_key_id=mcp_key_id,
            status=status,
            paradigm_id=paradigm_id,
            actor_user_id=actor_user_id,
            cursor=cursor,
            page_size=page_size,
            user=user,
        )
    except PermissionError as exc:
        raise HTTPException(404, "knowledge base not found") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/api/retrieval-records/{record_id}")
async def get_retrieval_record(
    record_id: str,
    request: Request,
    domain: str = Query(..., min_length=1),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    resolved = require_domain(domain)
    service = await _service(request, resolved)
    record = await service.get_record(
        record_id=record_id, domain=resolved, user=user
    )
    if record is None:
        raise HTTPException(404, "retrieval record not found")
    return record


@router.post(
    "/api/internal/retrieval-records/expire-pending-uploads",
    dependencies=[Depends(_require_internal)],
)
async def expire_pending_upload_records(
    request: Request,
) -> dict[str, int]:
    enabled_domains = tuple(
        getattr(request.app.state, "enabled_domains", ()) or ()
    )
    if not enabled_domains:
        service = build_service(request.app.state.pg_pool)
        expired_count = await service.expire_pending_uploads(
            older_than_seconds=600,
        )
        return {"expired_count": expired_count}

    expired_count = 0
    for domain in enabled_domains:
        pool = await request.app.state.domain_pools.async_pool(domain)
        service = build_service(pool)
        expired_count += await service.expire_pending_uploads(
            older_than_seconds=600,
        )
    return {"expired_count": expired_count}

@router.post(
    "/api/internal/retrieval-records",
    dependencies=[Depends(_require_internal)],
)
async def write_retrieval_record(
    body: RetrievalRecordWrite,
    request: Request,
) -> dict[str, Any]:
    resolved = require_domain(body.domain)
    service = await _service(request, resolved)
    payload = body.model_dump()
    payload["domain"] = resolved
    return await service.write_record(payload)
