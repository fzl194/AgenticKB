"""PostgreSQL repository for the unified knowledge access ledger."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any


def _since(days: int) -> datetime:
    today = datetime.now(timezone.utc).date()
    return datetime.combine(
        today - timedelta(days=days - 1), datetime.min.time(), tzinfo=timezone.utc
    )


def _record(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["kb_ids"] = list(result.get("kb_ids") or [])
    result["details_json"] = dict(result.get("details_json") or {})
    return result


def _payload_record(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["response_refs_json"] = list(result.get("response_refs_json") or [])
    result["redactions_json"] = list(result.get("redactions_json") or [])
    return result


class RetrievalRecordRepository:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def is_available(self) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT to_regclass('public.knowledge_access_records') AS t"
            )
            row = await cur.fetchone()
            return bool(row and row["t"])

    async def upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        main_payload = dict(payload)
        payload_snapshot = main_payload.pop("payload", None)
        incoming_source = str(main_payload.get("source") or "")
        incoming_details = dict(main_payload.get("details_json") or {})
        incoming_terminal_owner = str(
            incoming_details.get("terminal_owner") or ""
        )
        finalize_existing_mcp = (
            incoming_source == "mcp"
            and incoming_terminal_owner == "mcp"
            and main_payload.get("status") != "pending"
        )
        values = {
            **main_payload,
            "kb_ids": json.dumps(
                main_payload.get("kb_ids") or [], ensure_ascii=False
            ),
            "details_json": json.dumps(
                main_payload.get("details_json") or {}, ensure_ascii=False
            ),
            "finalize_existing_mcp": finalize_existing_mcp,
        }
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO knowledge_access_records (
                       id, occurred_at, completed_at, domain, actor_user_id,
                       actor_username, source, operation, tool_name, mcp_key_id,
                       kb_ids, query_text, paradigm_id, paradigm_version, status,
                       result_count, duration_ms, error_code, details_json
                   ) VALUES (
                       %(id)s, COALESCE(%(occurred_at)s, now()), %(completed_at)s,
                       %(domain)s, %(actor_user_id)s, %(actor_username)s,
                       %(source)s, %(operation)s, %(tool_name)s, %(mcp_key_id)s,
                       %(kb_ids)s::jsonb, %(query_text)s, %(paradigm_id)s,
                       %(paradigm_version)s, %(status)s, %(result_count)s,
                       %(duration_ms)s, %(error_code)s, %(details_json)s::jsonb
                   ) ON CONFLICT (id) DO UPDATE SET
                       completed_at = COALESCE(EXCLUDED.completed_at, knowledge_access_records.completed_at),
                       status = EXCLUDED.status,
                       result_count = COALESCE(EXCLUDED.result_count, knowledge_access_records.result_count),
                       duration_ms = COALESCE(EXCLUDED.duration_ms, knowledge_access_records.duration_ms),
                       paradigm_id = COALESCE(EXCLUDED.paradigm_id, knowledge_access_records.paradigm_id),
                       paradigm_version = COALESCE(EXCLUDED.paradigm_version, knowledge_access_records.paradigm_version),
                       error_code = COALESCE(EXCLUDED.error_code, knowledge_access_records.error_code),
                       details_json = knowledge_access_records.details_json || EXCLUDED.details_json
                   WHERE (
                           knowledge_access_records.status = 'pending'
                           AND EXCLUDED.status <> 'pending'
                       ) OR (
                           %(finalize_existing_mcp)s
                           AND knowledge_access_records.source = 'mcp'
                           AND EXCLUDED.source = 'mcp'
                           AND knowledge_access_records.status <> 'pending'
                           AND knowledge_access_records.details_json->>'terminal_owner' = 'serving'
                           AND EXCLUDED.details_json->>'terminal_owner' = 'mcp'
                           AND EXCLUDED.status <> 'pending'
                       )
                   RETURNING *""",
                values,
            )
            row = await cur.fetchone()
            main_changed = row is not None
            if row is None:
                # Duplicate pending writes and all terminal replays are no-ops.
                # Return the immutable stored row so retries remain idempotent.
                cur = await conn.execute(
                    "SELECT * FROM knowledge_access_records WHERE id = %(id)s",
                    {"id": main_payload["id"]},
                )
                row = await cur.fetchone()
            if row is None:
                raise RuntimeError("knowledge access record upsert returned no row")
            if payload_snapshot is not None and main_changed:
                await self._upsert_payload(
                    conn,
                    record_id=main_payload["id"],
                    payload=payload_snapshot,
                    replace_response=incoming_source == "mcp",
                )
            return _record(row)

    @staticmethod
    async def _upsert_payload(
        conn: Any,
        *,
        record_id: str,
        payload: dict[str, Any],
        replace_response: bool,
    ) -> None:
        def encoded(name: str) -> str | None:
            value = payload.get(name)
            if value is None:
                return None
            return json.dumps(value, ensure_ascii=False)

        values = {
            "record_id": record_id,
            "request_json": encoded("request_json"),
            "effective_context_json": encoded("effective_context_json"),
            "response_mode": payload["response_mode"],
            "response_json": encoded("response_json"),
            "response_refs_json": encoded("response_refs_json") or "[]",
            "request_bytes": payload.get("request_bytes"),
            "response_bytes": payload.get("response_bytes"),
            "response_truncated": bool(payload.get("response_truncated", False)),
            "response_original_bytes": payload.get("response_original_bytes"),
            "response_omitted_count": int(
                payload.get("response_omitted_count") or 0
            ),
            "response_sha256": payload.get("response_sha256"),
            "redactions_json": encoded("redactions_json") or "[]",
            "payload_schema_version": int(
                payload.get("payload_schema_version") or 1
            ),
            "replace_response": replace_response,
        }
        await conn.execute(
            """INSERT INTO knowledge_access_record_payloads (
                   record_id, request_json, effective_context_json, response_mode,
                   response_json, response_refs_json, request_bytes,
                   response_bytes, response_truncated, response_original_bytes,
                   response_omitted_count, response_sha256, redactions_json,
                   payload_schema_version
               ) VALUES (
                   %(record_id)s, %(request_json)s::jsonb,
                   %(effective_context_json)s::jsonb, %(response_mode)s,
                   %(response_json)s::jsonb, %(response_refs_json)s::jsonb,
                   %(request_bytes)s, %(response_bytes)s,
                   %(response_truncated)s, %(response_original_bytes)s,
                   %(response_omitted_count)s, %(response_sha256)s,
                   %(redactions_json)s::jsonb, %(payload_schema_version)s
               ) ON CONFLICT (record_id) DO UPDATE SET
                   request_json = COALESCE(knowledge_access_record_payloads.request_json, EXCLUDED.request_json),
                   effective_context_json =
                       COALESCE(knowledge_access_record_payloads.effective_context_json, '{}'::jsonb)
                       || COALESCE(EXCLUDED.effective_context_json, '{}'::jsonb),
                   response_mode = CASE WHEN %(replace_response)s
                       THEN EXCLUDED.response_mode
                       ELSE knowledge_access_record_payloads.response_mode END,
                   response_json = CASE WHEN %(replace_response)s
                       THEN EXCLUDED.response_json
                       ELSE COALESCE(EXCLUDED.response_json, knowledge_access_record_payloads.response_json) END,
                   response_refs_json = CASE WHEN %(replace_response)s
                       THEN EXCLUDED.response_refs_json
                       ELSE knowledge_access_record_payloads.response_refs_json || EXCLUDED.response_refs_json END,
                   request_bytes = COALESCE(EXCLUDED.request_bytes, knowledge_access_record_payloads.request_bytes),
                   response_bytes = COALESCE(EXCLUDED.response_bytes, knowledge_access_record_payloads.response_bytes),
                   response_truncated = EXCLUDED.response_truncated,
                   response_original_bytes = COALESCE(EXCLUDED.response_original_bytes, knowledge_access_record_payloads.response_original_bytes),
                   response_omitted_count = EXCLUDED.response_omitted_count,
                   response_sha256 = COALESCE(EXCLUDED.response_sha256, knowledge_access_record_payloads.response_sha256),
                   redactions_json = EXCLUDED.redactions_json,
                   payload_schema_version = EXCLUDED.payload_schema_version,
                   updated_at = now()""",
            values,
        )
    async def complete_upload_file(
        self,
        *,
        record_id: str,
        success: bool,
        error_code: str | None,
        response_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Atomically apply one already-redeemed upload ticket outcome."""

        params = {
            "record_id": record_id,
            "uploaded_delta": 1 if success else 0,
            "failed_delta": 0 if success else 1,
            "error_code": error_code or "upload_failed",
            "response_refs_json": json.dumps(
                response_refs or [], ensure_ascii=False
            ),
        }
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """WITH locked AS (
                       SELECT id, status,
                              GREATEST(1, COALESCE(NULLIF(details_json->>'file_count', '')::integer, 1)) AS file_count,
                              COALESCE(NULLIF(details_json->>'completed_count', '')::integer, 0) AS completed_count,
                              COALESCE(NULLIF(details_json->>'uploaded_count', '')::integer, 0) AS uploaded_count,
                              COALESCE(NULLIF(details_json->>'failed_count', '')::integer, 0) AS failed_count
                         FROM knowledge_access_records
                        WHERE id = %(record_id)s
                          AND operation = 'upload'
                        FOR UPDATE
                   ), updated AS (
                       UPDATE knowledge_access_records AS target
                          SET details_json = jsonb_set(
                                  jsonb_set(
                                      jsonb_set(
                                          jsonb_set(target.details_json, '{completed_count}', to_jsonb(locked.completed_count + 1)),
                                          '{uploaded_count}', to_jsonb(locked.uploaded_count + %(uploaded_delta)s)
                                      ),
                                      '{failed_count}', to_jsonb(locked.failed_count + %(failed_delta)s)
                                  ),
                                  '{terminal_owner}', to_jsonb('mining'::text)
                              ),
                              status = CASE
                                  WHEN locked.completed_count + 1 >= locked.file_count
                                  THEN CASE
                                      WHEN locked.failed_count + %(failed_delta)s > 0 THEN 'failed'
                                      ELSE 'success'
                                  END
                                  ELSE 'pending'
                              END,
                              completed_at = CASE
                                  WHEN locked.completed_count + 1 >= locked.file_count THEN now()
                                  ELSE NULL
                              END,
                              error_code = CASE
                                  WHEN %(failed_delta)s > 0
                                  THEN COALESCE(target.error_code, %(error_code)s)
                                  ELSE target.error_code
                              END
                         FROM locked
                        WHERE target.id = locked.id
                          AND locked.status = 'pending'
                       RETURNING target.*
                   ), payload_updated AS (
                       INSERT INTO knowledge_access_record_payloads (
                           record_id, response_mode, response_refs_json,
                           response_truncated, response_omitted_count,
                           payload_schema_version
                       )
                       SELECT id, 'reference', %(response_refs_json)s::jsonb,
                              false, 0, 1
                         FROM updated
                        WHERE %(response_refs_json)s::jsonb <> '[]'::jsonb
                       ON CONFLICT (record_id) DO UPDATE SET
                           response_mode = 'reference',
                           response_json = NULL,
                           response_refs_json =
                               knowledge_access_record_payloads.response_refs_json
                               || EXCLUDED.response_refs_json,
                           response_bytes = octet_length((
                               knowledge_access_record_payloads.response_refs_json
                               || EXCLUDED.response_refs_json
                           )::text),
                           response_original_bytes = octet_length((
                               knowledge_access_record_payloads.response_refs_json
                               || EXCLUDED.response_refs_json
                           )::text),
                           response_truncated = false,
                           response_omitted_count = 0,
                           response_sha256 = NULL,
                           updated_at = now()
                       RETURNING record_id
                   )
                   SELECT * FROM updated
                   UNION ALL
                   SELECT existing.*
                     FROM knowledge_access_records AS existing
                    WHERE existing.id = %(record_id)s
                      AND NOT EXISTS (SELECT 1 FROM updated)
                   LIMIT 1""",
                params,
            )
            row = await cur.fetchone()
            return _record(row) if row is not None else None

    async def expire_pending_uploads(
        self,
        *,
        older_than_seconds: int,
    ) -> int:
        """Terminalize stale pending upload groups; repeat calls are no-ops."""

        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """WITH candidates AS (
                       SELECT id
                         FROM knowledge_access_records
                        WHERE status = 'pending'
                          AND operation = 'upload'
                          AND occurred_at <= now() - make_interval(secs => %(older_than_seconds)s)
                        ORDER BY occurred_at, id
                        FOR UPDATE SKIP LOCKED
                   ), updated AS (
                       UPDATE knowledge_access_records AS target
                          SET status = 'failed',
                              completed_at = now(),
                              error_code = 'upload_expired',
                              details_json = jsonb_set(
                                  jsonb_set(
                                      jsonb_set(
                                          target.details_json,
                                          '{completed_count}',
                                          to_jsonb(GREATEST(1, COALESCE(NULLIF(target.details_json->>'file_count', '')::integer, 1)))
                                      ),
                                      '{failed_count}',
                                      to_jsonb(
                                          COALESCE(NULLIF(target.details_json->>'failed_count', '')::integer, 0)
                                          + GREATEST(
                                              0,
                                              GREATEST(1, COALESCE(NULLIF(target.details_json->>'file_count', '')::integer, 1))
                                              - COALESCE(NULLIF(target.details_json->>'completed_count', '')::integer, 0)
                                          )
                                      )
                                  ),
                                  '{terminal_owner}', to_jsonb('mining'::text)
                              )
                         FROM candidates
                        WHERE target.id = candidates.id
                          AND target.status = 'pending'
                       RETURNING 1
                   )
                   SELECT COUNT(*) AS expired_count FROM updated""",
                {"older_than_seconds": max(1, int(older_than_seconds))},
            )
            row = await cur.fetchone()
            return int((row or {}).get("expired_count") or 0)
    @staticmethod
    def _where(
        *,
        domain: str,
        actor_user_id: str | None = None,
        source: str | None = None,
        tool_name: str | None = None,
        operation: str | None = None,
        kb_id: str | None = None,
        mcp_key_id: str | None = None,
        status: str | None = None,
        paradigm_id: str | None = None,
        visible_kb_ids: list[str] | None = None,
    ) -> tuple[list[str], dict[str, Any]]:
        clauses = ["domain = %(domain)s"]
        params: dict[str, Any] = {"domain": domain}
        for column, value in (
            ("actor_user_id", actor_user_id),
            ("source", source),
            ("tool_name", tool_name),
            ("operation", operation),
            ("mcp_key_id", mcp_key_id),
            ("status", status),
            ("paradigm_id", paradigm_id),
        ):
            if value is not None:
                clauses.append(f"{column} = %({column})s")
                params[column] = value
        if kb_id is not None:
            clauses.append("kb_ids @> jsonb_build_array(%(kb_id)s::text)")
            params["kb_id"] = kb_id
        if visible_kb_ids is not None:
            clauses.append(
                "(kb_ids = '[]'::jsonb OR kb_ids ?| %(visible_kb_ids)s)"
            )
            params["visible_kb_ids"] = visible_kb_ids
        return clauses, params

    async def list_records(
        self,
        *,
        domain: str,
        days: int,
        actor_user_id: str | None,
        source: str | None,
        tool_name: str | None,
        operation: str | None,
        kb_id: str | None,
        mcp_key_id: str | None,
        status: str | None,
        paradigm_id: str | None,
        visible_kb_ids: list[str] | None,
        cursor_at: datetime | None,
        cursor_id: str | None,
        page_size: int,
    ) -> dict[str, Any]:
        clauses, params = self._where(
            domain=domain,
            actor_user_id=actor_user_id,
            source=source,
            tool_name=tool_name,
            operation=operation,
            kb_id=kb_id,
            mcp_key_id=mcp_key_id,
            status=status,
            paradigm_id=paradigm_id,
            visible_kb_ids=visible_kb_ids,
        )
        clauses.append("occurred_at >= %(since)s")
        params["since"] = _since(days)
        if cursor_at is not None and cursor_id is not None:
            clauses.append("(occurred_at, id) < (%(cursor_at)s, %(cursor_id)s)")
            params.update(cursor_at=cursor_at, cursor_id=cursor_id)
        params["limit"] = page_size + 1
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM knowledge_access_records WHERE "
                + " AND ".join(clauses)
                + " ORDER BY occurred_at DESC, id DESC LIMIT %(limit)s",
                params,
            )
            rows = [_record(row) for row in await cur.fetchall()]
        has_more = len(rows) > page_size
        return {"items": rows[:page_size], "has_more": has_more}

    async def get_record(
        self,
        *,
        record_id: str,
        domain: str,
        actor_user_id: str | None,
        visible_kb_ids: list[str] | None,
    ) -> dict[str, Any] | None:
        clauses = ["id = %(id)s", "domain = %(domain)s"]
        params: dict[str, Any] = {"id": record_id, "domain": domain}
        if actor_user_id is not None:
            clauses.append("actor_user_id = %(actor_user_id)s")
            params["actor_user_id"] = actor_user_id
        if visible_kb_ids is not None:
            clauses.append(
                "(kb_ids = '[]'::jsonb OR kb_ids ?| %(visible_kb_ids)s)"
            )
            params["visible_kb_ids"] = visible_kb_ids
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM knowledge_access_records WHERE "
                + " AND ".join(clauses),
                params,
            )
            row = await cur.fetchone()
            if row is None:
                return None
            result = _record(row)
            payload_cur = await conn.execute(
                """SELECT * FROM knowledge_access_record_payloads
                    WHERE record_id = %(id)s""",
                {"id": record_id},
            )
            payload_row = await payload_cur.fetchone()
            result["payload"] = (
                _payload_record(payload_row) if payload_row is not None else None
            )
            return result
    async def summary(
        self,
        *,
        domain: str,
        days: int,
        actor_user_id: str | None,
        kb_id: str | None,
        visible_kb_ids: list[str] | None,
        source: str | None = None,
        tool_name: str | None = None,
        operation: str | None = None,
        mcp_key_id: str | None = None,
        status: str | None = None,
        paradigm_id: str | None = None,
    ) -> dict[str, Any]:
        clauses, params = self._where(
            domain=domain,
            actor_user_id=actor_user_id,
            kb_id=kb_id,
            visible_kb_ids=visible_kb_ids,
            source=source,
            tool_name=tool_name,
            operation=operation,
            mcp_key_id=mcp_key_id,
            status=status,
            paradigm_id=paradigm_id,
        )
        clauses.append("occurred_at >= %(since)s")
        params["since"] = _since(days)
        where = " AND ".join(clauses)
        failed_sql = "status IN ('denied','invalid','timeout','failed')"
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT COUNT(*) AS total_calls,
                           COUNT(*) FILTER (WHERE operation = 'search') AS calls,
                           COUNT(*) FILTER (WHERE operation = 'search' AND status = 'no_result') AS no_result,
                           COUNT(*) FILTER (WHERE {failed_sql}) AS failed,
                           COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms), 0) AS p95_duration_ms,
                           COALESCE(AVG(duration_ms), 0) AS avg_duration_ms,
                           COUNT(DISTINCT paradigm_id) FILTER (WHERE operation = 'search') AS active_paradigms
                    FROM knowledge_access_records WHERE {where}""",
                params,
            )
            row = await cur.fetchone() or {}
            total_calls = int(row.get("total_calls") or 0)
            calls = int(row.get("calls") or 0)
            no_result = int(row.get("no_result") or 0)
            failed = int(row.get("failed") or 0)

            trend_cur = await conn.execute(
                f"""SELECT occurred_at::date AS day, COUNT(*) AS total_calls,
                            COUNT(*) FILTER (WHERE operation = 'search') AS calls,
                            COUNT(*) FILTER (WHERE operation = 'search' AND status = 'no_result') AS no_result,
                            COUNT(*) FILTER (WHERE {failed_sql}) AS failed
                     FROM knowledge_access_records WHERE {where}
                     GROUP BY 1 ORDER BY 1""",
                params,
            )
            trend_rows = {str(r["day"]): r for r in await trend_cur.fetchall()}
            today = datetime.now(timezone.utc).date()
            trend = []
            for offset in range(days - 1, -1, -1):
                day = today - timedelta(days=offset)
                item = trend_rows.get(day.isoformat(), {})
                trend.append({
                    "date": day.isoformat(),
                    "total_calls": int(item.get("total_calls") or 0),
                    "calls": int(item.get("calls") or 0),
                    "no_result": int(item.get("no_result") or 0),
                    "failed": int(item.get("failed") or 0),
                })

            sources = await self._breakdown_on_conn(conn, where, params, "source")
            tools = await self._group_usage_on_conn(conn, where, params, "tool_name")
            paradigms = await self._group_usage_on_conn(
                conn, where + " AND operation = 'search'", params, "paradigm_id"
            )
            no_result_queries = await self._query_usage_on_conn(
                conn,
                where + " AND operation = 'search' AND status = 'no_result'",
                params,
                10,
            )
            top_queries = await self._query_usage_on_conn(
                conn, where + " AND operation = 'search'", params, 20
            )
        return {
            "available": True,
            "days": days,
            "summary": {
                "total_calls": total_calls,
                "calls": calls,
                "no_result": no_result,
                "failed": failed,
                "no_result_rate": round(no_result / calls, 4) if calls else 0.0,
                "failure_rate": round(failed / total_calls, 4) if total_calls else 0.0,
                "p95_duration_ms": round(float(row.get("p95_duration_ms") or 0), 1),
                "avg_duration_ms": round(float(row.get("avg_duration_ms") or 0), 1),
                "active_paradigms": int(row.get("active_paradigms") or 0),
            },
            "trend": trend,
            "sources": sources,
            "tools": tools,
            "paradigms": paradigms,
            "no_result_queries": no_result_queries,
            "top_queries": top_queries,
        }

    async def _breakdown_on_conn(
        self, conn: Any, where: str, params: dict[str, Any], column: str
    ) -> dict[str, int]:
        if column != "source":
            raise ValueError("unsupported breakdown")
        cur = await conn.execute(
            f"SELECT source AS key, COUNT(*) AS calls FROM knowledge_access_records WHERE {where} GROUP BY 1 ORDER BY calls DESC",
            params,
        )
        return {str(r["key"]): int(r["calls"]) for r in await cur.fetchall()}

    async def _group_usage_on_conn(
        self, conn: Any, where: str, params: dict[str, Any], column: str
    ) -> list[dict[str, Any]]:
        if column not in {"tool_name", "paradigm_id"}:
            raise ValueError("unsupported usage column")
        cur = await conn.execute(
            f"""SELECT COALESCE({column}, '(none)') AS key, COUNT(*) AS calls,
                       COUNT(*) FILTER (WHERE status = 'no_result') AS no_result
                FROM knowledge_access_records WHERE {where}
                GROUP BY 1 ORDER BY calls DESC""",
            params,
        )
        return [
            {column: r["key"], "calls": int(r["calls"]), "no_result": int(r["no_result"])}
            for r in await cur.fetchall()
        ]

    async def _query_usage_on_conn(
        self, conn: Any, where: str, params: dict[str, Any], limit: int
    ) -> list[dict[str, Any]]:
        query_params = {**params, "query_limit": limit}
        cur = await conn.execute(
            f"""SELECT LEFT(query_text, 200) AS query_text, COUNT(*) AS count,
                       COUNT(*) FILTER (WHERE status = 'no_result') AS no_result,
                       MAX(occurred_at) AS last_at
                FROM knowledge_access_records
                WHERE {where} AND query_text IS NOT NULL
                GROUP BY 1 ORDER BY count DESC, last_at DESC LIMIT %(query_limit)s""",
            query_params,
        )
        return [
            {
                "query_text": r["query_text"],
                "count": int(r["count"]),
                "no_result": int(r["no_result"]),
                "last_at": r["last_at"],
            }
            for r in await cur.fetchall()
        ]
