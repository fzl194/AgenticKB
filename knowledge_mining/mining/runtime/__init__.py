"""Runtime tracking for v1.1 Mining pipeline.

Wraps MiningRuntimeDB to provide stage-level tracking with automatic
timing and run-level status transitions.
"""
from __future__ import annotations

import time
from typing import Any

from knowledge_mining.mining.infra.db import MiningRuntimeDB
from knowledge_mining.mining.contracts.models import (
    MiningRunData,
    MiningRunDocumentData,
    StageEvent,
    ResumePlan,
)


class RuntimeTracker:
    """High-level runtime state tracker for a single mining run."""

    def __init__(self, db: MiningRuntimeDB) -> None:
        self._db = db

    # -- Run lifecycle --

    def create_run(self, data: MiningRunData) -> str:
        self._db.insert_run(data)
        return data.id

    def complete_run(
        self, run_id: str, *, build_id: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        domain: str | None = None,
        **counters: int,
    ) -> bool:
        return self._db.update_run_status(
            run_id, "completed", finished_at=_utcnow(), build_id=build_id,
            metadata_json=metadata_json, current_stage="done", domain=domain,
            expected_statuses=("queued", "running") if domain else None,
            **counters,
        )

    def fail_run(
        self,
        run_id: str,
        error_summary: str,
        *,
        current_stage: str = "mining",
        domain: str | None = None,
        **counters: int,
    ) -> bool:
        if domain is not None:
            updated = self._db.fail_run(run_id, domain, error_summary, current_stage)
            if not counters or not updated:
                return updated
            return self._db.update_run_status(
                run_id, "failed", domain=domain, current_stage=current_stage,
                **counters,
            )
        return self._db.update_run_status(
            run_id, "failed", finished_at=_utcnow(), error_summary=error_summary, **counters,
        )

    def set_run_phase(
        self, run_id: str, domain: str, current_stage: str, *, status: str = "running",
    ) -> bool:
        return self._db.set_run_phase(run_id, domain, current_stage, status=status)

    def finish_ingest(
        self, run_id: str, domain: str, total_documents: int,
        ingest_summary: dict[str, Any],
    ) -> bool:
        return self._db.finish_ingest(run_id, domain, total_documents, ingest_summary)

    def interrupt_run(self, run_id: str, **counters: int) -> None:
        self._db.update_run_status(run_id, "interrupted", finished_at=_utcnow(), **counters)

    def pause_for_review(
        self, run_id: str, *, subloop_stage: str,
        ontology_version_id: str | None = None,
        domain: str | None = None,
        **counters: int,
    ) -> bool:
        """B6：把 run 置入人审暂停态（awaiting_review）并记下卡在哪道 Gate。

        不写 finished_at——run 还没结束，只是等人拍板后 resume 续跑。
        """
        return self._db.update_run_status(
            run_id, "awaiting_review", subloop_stage=subloop_stage,
            ontology_version_id=ontology_version_id, current_stage="review",
            domain=domain,
            expected_statuses=("queued", "running") if domain else None,
            **counters,
        )

    def resume_running(
        self,
        run_id: str,
        *,
        subloop_stage: str | None = None,
        domain: str | None = None,
        recover_workflow: bool = False,
    ) -> bool:
        """B6：人审提交后把 run 拨回 running，subloop_stage 推进到下一检查点。"""
        expected_statuses = (
            ("awaiting_review", "running", "failed", "interrupted")
            if recover_workflow and domain
            else (("awaiting_review", "running") if domain else None)
        )
        return self._db.update_run_status(
            run_id,
            "running",
            subloop_stage=subloop_stage,
            current_stage="mining",
            domain=domain,
            expected_statuses=expected_statuses,
            **(
                {
                    "clear_finished_at": True,
                    "clear_error_summary": True,
                }
                if recover_workflow
                else {}
            ),
        )

    def begin_manual_publish(self, run_id: str, *, domain: str) -> bool:
        """Claim an assets-only completed Workflow Run for build/publish."""
        return self._db.update_run_status(
            run_id,
            "running",
            current_stage="mining",
            domain=domain,
            expected_statuses=("completed",),
            clear_finished_at=True,
            clear_error_summary=True,
        )

    # -- Run documents --

    def register_document(self, data: MiningRunDocumentData) -> str:
        now = _utcnow()
        patched = MiningRunDocumentData(
            **{k: v for k, v in data.__dict__.items() if k != "started_at"},
            started_at=now,
        )
        self._db.insert_run_document(patched)
        return patched.id

    def start_document(
        self, rd_id: str, *, retry_required: bool = False,
    ) -> None:
        self._db.update_run_document(
            rd_id,
            status="processing",
            clear_error_message=True,
            clear_finished_at=True,
            metadata_patch=(
                {"retry_required": True} if retry_required else None
            ),
        )

    def commit_document(
        self,
        rd_id: str,
        document_id: str,
        document_snapshot_id: str,
    ) -> None:
        self._db.update_run_document(
            rd_id,
            status="committed",
            document_id=document_id,
            document_snapshot_id=document_snapshot_id,
            finished_at=_utcnow(),
        )

    def stage_document(
        self,
        rd_id: str,
        document_id: str,
        document_snapshot_id: str,
    ) -> None:
        """36号根因 2：asset_persist 成功后的 staged 事实——只写身份.

        status 保持 processing（crash resume 由 identity + asset_persist
        node event 判定重试边界，不再依赖 status='committed'）；
        committed 由 mining_finalize 在 Build 事务成功后回写。
        """
        self._db.update_run_document(
            rd_id,
            document_id=document_id,
            document_snapshot_id=document_snapshot_id,
        )

    def fail_document(self, rd_id: str, error_message: str) -> None:
        self._db.update_run_document(
            rd_id, status="failed", error_message=error_message, finished_at=_utcnow(),
        )

    def skip_document(
        self, rd_id: str, reason: str | None = None, detail: str | None = None,
    ) -> None:
        """标记文档被跳过。

        reason 是稳定的机器码（见 pipeline 的 _classify_parse_skip），detail 是给人看
        的明细（异常文本、file_type 等）。两者写进 metadata_json，供 UI 展示
        「为什么跳过」——否则用户只能看到一个没有信息量的"跳过"。
        """
        patch: dict[str, Any] = {}
        if reason:
            patch["skip_reason"] = reason
        if detail:
            patch["skip_reason_detail"] = detail
        self._db.update_run_document(
            rd_id,
            status="skipped",
            finished_at=_utcnow(),
            metadata_patch=patch or None,
        )

    # -- Stage events with timing --

    def start_stage(
        self,
        run_id: str,
        stage: str,
        run_document_id: str | None = None,
    ) -> str:
        """Record stage start. Returns event ID for end_stage."""
        evt_id = _new_id()
        self._db.insert_stage_event(StageEvent(
            id=evt_id,
            run_id=run_id,
            run_document_id=run_document_id,
            stage=stage,
            status="started",
            created_at=_utcnow(),
        ))
        return evt_id

    def end_stage(
        self,
        event_id: str,
        run_id: str,
        stage: str,
        status: str = "completed",
        output_summary: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Record stage completion. Re-reads the start event for duration calculation."""
        # Compute duration from start event
        duration_ms = None
        run_document_id = None
        start_evt = self._db._fetchone(
            "SELECT created_at, run_document_id FROM mining_run_stage_events WHERE id = %s", (event_id,)
        )
        if start_evt:
            if start_evt["created_at"]:
                try:
                    from datetime import datetime, timezone
                    start_time = datetime.fromisoformat(start_evt["created_at"])
                    duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
                except Exception:
                    pass
            run_document_id = start_evt["run_document_id"]

        self._db.insert_stage_event(StageEvent(
            id=_new_id(),
            run_id=run_id,
            run_document_id=run_document_id,
            stage=stage,
            status=status,
            duration_ms=duration_ms,
            output_summary=output_summary,
            error_message=error_message,
            created_at=_utcnow(),
        ))

    # -- Resume support --

    def build_resume_plan(self, run_id: str) -> ResumePlan:
        """Build a resume plan for an interrupted run."""
        committed = self._db.get_committed_document_keys(run_id)
        failed = self._db.get_failed_document_keys(run_id)

        run_docs = self._db.get_run_documents(run_id)
        pending = frozenset(
            rd["document_key"] for rd in run_docs
            if rd["status"] == "pending"
        )

        # Documents that were processing/failed need to be redone
        redo = failed - committed

        return ResumePlan(
            skip_document_keys=committed,
            pending_document_keys=pending,
            redo_document_keys=redo,
            can_resume=True,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex
