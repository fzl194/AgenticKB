from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from psycopg.types.json import Jsonb

from ..run_binding import WorkflowRunBinding


@dataclass(frozen=True)
class NodeAttempt:
    id: str
    attempt_no: int
    started_at: datetime


class AsyncDomainRunRepository:
    """Insert Runtime rows into one already-selected Domain pool."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def insert_queued_run(
        self,
        *,
        run_id: str,
        input_path: str,
        domain: str,
        channel: str,
        execution_engine: Literal["legacy", "workflow"],
        binding: WorkflowRunBinding | None,
        started_at: str,
        preflight_manifest: dict[str, Any] | None = None,
        kb_id: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        if execution_engine == "workflow":
            if binding is None or not all((
                binding.workflow_id,
                binding.workflow_version,
                binding.workflow_version_id,
                binding.graph_hash,
                binding.manifest,
            )):
                raise ValueError("Workflow runs require a complete immutable binding")
        elif binding is not None:
            raise ValueError("Legacy runs cannot carry a Workflow binding")

        async with self._pool.connection() as conn:
            await conn.execute(
                """INSERT INTO mining_runs (
                       id, input_path, domain, status, current_stage, started_at,
                       channel, execution_engine, workflow_id, workflow_version,
                       workflow_version_id, workflow_graph_hash,
                       workflow_manifest_json, preflight_manifest_json,
                       kb_id, metadata_json
                   ) VALUES (
                       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s
                   )""",
                (
                    run_id,
                    input_path,
                    domain,
                    "queued",
                    "queued",
                    started_at,
                    channel,
                    execution_engine,
                    binding.workflow_id if binding else None,
                    binding.workflow_version if binding else None,
                    binding.workflow_version_id if binding else None,
                    binding.graph_hash if binding else None,
                    Jsonb(binding.manifest) if binding else None,
                    Jsonb(preflight_manifest) if preflight_manifest else None,
                    kb_id,
                    Jsonb(metadata_json or {}),
                ),
            )
        return run_id

    async def find_open_run_for_kb(self, kb_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                """SELECT id, status, started_at
                   FROM mining_runs
                   WHERE kb_id = %s
                     AND status IN (
                         'queued', 'running', 'awaiting_review', 'interrupted'
                     )
                   ORDER BY started_at, id
                   LIMIT 1""",
                (kb_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None


class DomainRunRepository:
    """Synchronous Domain Runtime access used by background Workflow workers."""

    _TERMINAL_STATUSES = {
        "completed",
        "failed",
        "skipped",
        "paused",
        "fallback",
        "not_applicable",
    }

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    def load_run(self, run_id: str) -> dict | None:
        with self.pool.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM mining_runs WHERE id = %s", (run_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def load_manifest(self, run_id: str) -> dict:
        with self.pool.connection() as conn:
            cursor = conn.execute(
                "SELECT workflow_manifest_json FROM mining_runs WHERE id = %s",
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise LookupError(f"Run not found: {run_id}")
        manifest = row.get("workflow_manifest_json")
        if not isinstance(manifest, dict):
            raise ValueError(f"Run {run_id} has no Workflow manifest")
        return dict(manifest)

    def document_persist_marker(
        self, run_document_id: str
    ) -> tuple[str, str] | None:
        """Return the committed asset identity used as the retry boundary."""
        with self.pool.connection() as conn:
            cursor = conn.execute(
                """SELECT document_id, document_snapshot_id
                   FROM mining_run_documents
                   WHERE id = %s
                     AND status = 'committed'
                     AND document_id IS NOT NULL
                     AND document_snapshot_id IS NOT NULL""",
                (run_document_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return str(row["document_id"]), str(row["document_snapshot_id"])

    def set_active_node(
        self,
        run_id: str,
        node_id: str | None,
        operator_type: str | None,
        pause_step: str | None = None,
    ) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """UPDATE mining_runs
                   SET active_node_id = %s,
                       active_operator_type = %s,
                       pause_step = %s
                   WHERE id = %s""",
                (node_id, operator_type, pause_step, run_id),
            )

    def start_node(
        self,
        *,
        run_id: str,
        run_document_id: str | None,
        node_id: str,
        operator_type: str,
        operator_version: str,
        input_summary: dict,
    ) -> NodeAttempt:
        event_id = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc)
        lock_key = (
            f"mining-node-attempt:{run_id}:"
            f"{run_document_id or '__global__'}:{node_id}"
        )
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (lock_key,),
                )
                cursor = conn.execute(
                    """SELECT COALESCE(MAX(attempt_no), 0) + 1 AS attempt_no
                       FROM mining_workflow_node_events
                       WHERE run_id = %s
                         AND node_id = %s
                         AND run_document_id IS NOT DISTINCT FROM %s""",
                    (run_id, node_id, run_document_id),
                )
                attempt_no = int(cursor.fetchone()["attempt_no"])
                conn.execute(
                    """INSERT INTO mining_workflow_node_events (
                           id, run_id, run_document_id, node_id, operator_type,
                           operator_version, status, attempt_no, started_at,
                           input_summary_json
                       ) VALUES (%s, %s, %s, %s, %s, %s, 'started', %s, %s, %s)""",
                    (
                        event_id,
                        run_id,
                        run_document_id,
                        node_id,
                        operator_type,
                        operator_version,
                        attempt_no,
                        started_at,
                        Jsonb(input_summary),
                    ),
                )
                conn.execute(
                    """UPDATE mining_runs
                       SET active_node_id = %s,
                           active_operator_type = %s,
                           pause_step = NULL
                       WHERE id = %s""",
                    (node_id, operator_type, run_id),
                )
        return NodeAttempt(event_id, attempt_no, started_at)

    def finish_node(
        self,
        attempt: NodeAttempt,
        *,
        status: str,
        output_summary: dict | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        if status not in self._TERMINAL_STATUSES:
            raise ValueError(f"Invalid terminal node status: {status}")
        finished_at = datetime.now(timezone.utc)
        duration_ms = max(
            0, int((finished_at - attempt.started_at).total_seconds() * 1000)
        )
        with self.pool.connection() as conn:
            conn.execute(
                """UPDATE mining_workflow_node_events
                   SET status = %s,
                       finished_at = %s,
                       duration_ms = %s,
                       output_summary_json = %s,
                       error_code = %s,
                       error_message = %s,
                       metadata_json = %s
                   WHERE id = %s AND status = 'started'""",
                (
                    status,
                    finished_at,
                    duration_ms,
                    Jsonb(output_summary or {}),
                    error_code,
                    error_message,
                    Jsonb(metadata or {}),
                    attempt.id,
                ),
            )

    def is_node_completed(
        self,
        run_id: str,
        node_id: str,
        run_document_id: str | None,
    ) -> bool:
        with self.pool.connection() as conn:
            cursor = conn.execute(
                """SELECT 1 FROM mining_workflow_node_events
                   WHERE run_id = %s
                     AND node_id = %s
                     AND run_document_id IS NOT DISTINCT FROM %s
                     AND status = 'completed'
                   LIMIT 1""",
                (run_id, node_id, run_document_id),
            )
            return cursor.fetchone() is not None

    def reusable_node_result(
        self,
        run_id: str,
        node_id: str,
        run_document_id: str | None,
    ) -> dict | None:
        """Load the latest successful terminal result for crash-safe resume."""

        with self.pool.connection() as conn:
            cursor = conn.execute(
                """SELECT status, output_summary_json
                   FROM mining_workflow_node_events
                   WHERE run_id = %s
                     AND node_id = %s
                     AND run_document_id IS NOT DISTINCT FROM %s
                     AND status IN (
                         'completed', 'skipped', 'fallback', 'not_applicable'
                     )
                   ORDER BY attempt_no DESC
                   LIMIT 1""",
                (run_id, node_id, run_document_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        summary = row.get("output_summary_json") or {}
        return {
            "status": str(row["status"]),
            "capabilities": tuple(summary.get("capabilities") or ()),
        }

    def list_node_events(self, run_id: str) -> list[dict]:
        with self.pool.connection() as conn:
            cursor = conn.execute(
                """SELECT * FROM mining_workflow_node_events
                   WHERE run_id = %s
                   ORDER BY started_at, node_id, attempt_no""",
                (run_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
