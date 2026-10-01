"""Application service for record visibility, filtering and cursor paging."""
from __future__ import annotations

from datetime import datetime
import logging
from threading import Lock
from typing import Any

from knowledge_mining.mining.infra.retrieval_records_db import RetrievalRecordRepository
from knowledge_mining.mining.kb.db import KbDB


logger = logging.getLogger(__name__)
_METRICS_LOCK = Lock()
_ACCESS_RECORD_WRITE_FAILURES = 0


def _record_write_failure(
    *,
    record_id: str,
    tool: str,
    phase: str,
    error: BaseException,
) -> None:
    global _ACCESS_RECORD_WRITE_FAILURES
    with _METRICS_LOCK:
        _ACCESS_RECORD_WRITE_FAILURES += 1
    logger.warning(
        "access_record_write_failed",
        extra={
            "record_id": record_id,
            "tool": tool,
            "phase": phase,
            "error_class": error.__class__.__name__,
        },
    )


def get_access_record_metrics() -> dict[str, int]:
    with _METRICS_LOCK:
        return {"access_record_write_failures": _ACCESS_RECORD_WRITE_FAILURES}


def reset_access_record_metrics() -> None:
    global _ACCESS_RECORD_WRITE_FAILURES
    with _METRICS_LOCK:
        _ACCESS_RECORD_WRITE_FAILURES = 0


class RetrievalRecordService:
    def __init__(self, pool: Any) -> None:
        self._repository = RetrievalRecordRepository(pool)
        self._kbdb = KbDB(pool)

    async def _actor_scope(
        self, *, user: dict[str, Any], domain: str, requested_actor: str | None = None
    ) -> str | None:
        can_manage = await self._kbdb.can_manage_domain(
            user_id=str(user["id"]), domain=domain
        )
        if can_manage:
            return requested_actor
        return str(user["id"])

    async def summary(
        self,
        *,
        domain: str,
        days: int,
        kb_id: str | None,
        source: str | None,
        tool_name: str | None,
        operation: str | None,
        mcp_key_id: str | None,
        status: str | None,
        paradigm_id: str | None,
        actor_user_id: str | None,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        if not await self._repository.is_available():
            return _empty_summary(days)
        visible_kb_ids = await self._visible_kb_ids(user=user, domain=domain)
        if kb_id is not None and kb_id not in visible_kb_ids:
            raise PermissionError("knowledge base not visible")
        actor = await self._actor_scope(
            user=user, domain=domain, requested_actor=actor_user_id
        )
        return await self._repository.summary(
            domain=domain,
            days=days,
            actor_user_id=actor,
            kb_id=kb_id,
            visible_kb_ids=visible_kb_ids,
            source=source,
            tool_name=tool_name,
            operation=operation,
            mcp_key_id=mcp_key_id,
            status=status,
            paradigm_id=paradigm_id,
        )

    async def list_records(self, *, user: dict[str, Any], **filters: Any) -> dict[str, Any]:
        domain = str(filters.pop("domain"))
        requested_actor = filters.pop("actor_user_id", None)
        visible_kb_ids = await self._visible_kb_ids(user=user, domain=domain)
        requested_kb = filters.get("kb_id")
        if requested_kb is not None and requested_kb not in visible_kb_ids:
            raise PermissionError("knowledge base not visible")
        actor = await self._actor_scope(
            user=user, domain=domain, requested_actor=requested_actor
        )
        cursor_at, cursor_id = _parse_cursor(filters.pop("cursor", None))
        page_size = int(filters["page_size"])
        result = await self._repository.list_records(
            domain=domain,
            actor_user_id=actor,
            cursor_at=cursor_at,
            cursor_id=cursor_id,
            visible_kb_ids=visible_kb_ids,
            **filters,
        )
        items = result["items"]
        next_cursor = None
        if result["has_more"] and items:
            last = items[-1]
            next_cursor = f"{last['occurred_at'].isoformat()}|{last['id']}"
        return {
            "items": items,
            "next_cursor": next_cursor,
            "has_more": bool(result["has_more"]),
            "page_size": page_size,
        }

    async def get_record(
        self, *, record_id: str, domain: str, user: dict[str, Any]
    ) -> dict[str, Any] | None:
        actor = await self._actor_scope(user=user, domain=domain)
        visible_kb_ids = await self._visible_kb_ids(user=user, domain=domain)
        return await self._repository.get_record(
            record_id=record_id,
            domain=domain,
            actor_user_id=actor,
            visible_kb_ids=visible_kb_ids,
        )

    async def _visible_kb_ids(
        self, *, user: dict[str, Any], domain: str
    ) -> list[str]:
        return await self._kbdb.list_visible_kb_ids(
            user_id=str(user["id"]), domain=domain
        )

    async def write_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._repository.upsert(payload)
        except Exception as exc:
            _record_write_failure(
                record_id=str(payload.get("id") or ""),
                tool=str(payload.get("tool_name") or ""),
                phase="upsert",
                error=exc,
            )
            raise

    async def complete_upload_file(
        self,
        *,
        record_id: str,
        success: bool,
        error_code: str | None,
        response_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        try:
            return await self._repository.complete_upload_file(
                record_id=record_id,
                success=success,
                error_code=error_code,
                response_refs=response_refs,
            )
        except Exception as exc:
            _record_write_failure(
                record_id=record_id,
                tool="manage_files",
                phase="upload_complete",
                error=exc,
            )
            raise

    async def expire_pending_uploads(
        self,
        *,
        older_than_seconds: int,
    ) -> int:
        try:
            return await self._repository.expire_pending_uploads(
                older_than_seconds=older_than_seconds,
            )
        except Exception as exc:
            _record_write_failure(
                record_id="",
                tool="manage_files",
                phase="upload_expire",
                error=exc,
            )
            raise


def _parse_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if cursor is None:
        return None, None
    try:
        raw_at, record_id = cursor.rsplit("|", 1)
        stamp = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid cursor") from exc
    if stamp.tzinfo is None or not record_id.strip():
        raise ValueError("invalid cursor")
    return stamp, record_id


def _empty_summary(days: int) -> dict[str, Any]:
    return {
        "available": False,
        "days": days,
        "summary": {
            "total_calls": 0,
            "calls": 0,
            "no_result": 0,
            "failed": 0,
            "no_result_rate": 0.0,
            "failure_rate": 0.0,
            "p95_duration_ms": 0.0,
            "avg_duration_ms": 0.0,
            "active_paradigms": 0,
        },
        "trend": [],
        "sources": {},
        "tools": [],
        "paradigms": [],
        "no_result_queries": [],
        "top_queries": [],
    }
