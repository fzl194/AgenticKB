"""Compatibility adapter for the legacy ``/api/ops/usage`` response.

The active data source is the unified knowledge access ledger. New code should
use :mod:`retrieval_records_db` directly; this adapter exists only while the
dashboard migrates to ``/api/retrieval-records/summary``. The ops route creates
one adapter per request, so its small ``(domain, days)`` cache is request-scoped
and bounded by the fixed aggregate calls made by that route. The retired legacy
``intent`` dimension intentionally returns an empty object to preserve the old
response shape; the unified ledger does not synthesize intent values.
"""
from __future__ import annotations

from typing import Any

from knowledge_mining.mining.infra.retrieval_records_db import RetrievalRecordRepository


class QueryLogStats:
    """Preserve the old aggregate method names over the new repository."""

    def __init__(self, pool: Any) -> None:
        self._repository = RetrievalRecordRepository(pool)
        self._cache: dict[tuple[str, int], dict[str, Any]] = {}

    async def is_available(self) -> bool:
        return await self._repository.is_available()

    async def _payload(self, *, domain: str, days: int) -> dict[str, Any]:
        key = (domain, days)
        if key not in self._cache:
            self._cache[key] = await self._repository.summary(
                domain=domain,
                days=days,
                actor_user_id=None,
                kb_id=None,
                visible_kb_ids=None,
            )
        return self._cache[key]

    async def summary(self, *, domain: str, days: int) -> dict[str, Any]:
        summary = (await self._payload(domain=domain, days=days))["summary"]
        return {
            "queries": summary["calls"],
            "no_result": summary["no_result"],
            "no_result_rate": summary["no_result_rate"],
            "p95_duration_ms": summary["p95_duration_ms"],
            "avg_duration_ms": summary["avg_duration_ms"],
            "active_paradigms": summary["active_paradigms"],
        }

    async def no_result_queries(
        self, *, domain: str, days: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        return (await self._payload(domain=domain, days=days))["no_result_queries"][:limit]

    async def top_queries(
        self, *, domain: str, days: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = (await self._payload(domain=domain, days=days))["top_queries"][:limit]
        return [
            {"query_text": row["query_text"], "count": row["count"], "no_result": row["no_result"]}
            for row in rows
        ]

    async def paradigm_usage(
        self, *, domain: str, days: int
    ) -> list[dict[str, Any]]:
        return (await self._payload(domain=domain, days=days))["paradigms"]

    async def trend(self, *, domain: str, days: int) -> list[dict[str, Any]]:
        rows = (await self._payload(domain=domain, days=days))["trend"]
        return [
            {"date": row["date"], "queries": row["calls"], "no_result": row["no_result"]}
            for row in rows
        ]

    async def breakdown(
        self, *, domain: str, days: int, column: str
    ) -> dict[str, int]:
        if column == "channel":
            return (await self._payload(domain=domain, days=days))["sources"]
        if column == "intent":
            return {}
        raise ValueError(f"unsupported breakdown column: {column}")
