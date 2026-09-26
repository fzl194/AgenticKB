"""Admin endpoints — worker diagnostics and retention cleanup."""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, HTTPException, Request

from llm_service.models import CleanupRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/worker-status")
async def worker_status(request: Request):
    """Diagnostic: return actual Worker concurrency and poll interval."""
    worker = getattr(request.app.state, "worker", None)
    if not worker:
        return {"ok": True, "worker_running": False}
    return {
        "ok": True,
        "worker_running": worker._running,
        "concurrency": worker._concurrency,
        "poll_interval": worker._poll_interval,
        "active_tasks": len(worker._tasks),
    }


@router.post("/cleanup")
async def cleanup_tasks(body: CleanupRequest, request: Request):
    """Batched retention cleanup for terminal agent_llm_* audit rows.

    Deletes tasks with terminal status (succeeded/failed/dead_letter/
    cancelled) finished before the retention cutoff, their child rows
    (requests/attempts/results/events), and stale agent_llm_model_calls.
    Optionally scoped to one knowledge_domain. Never touches queued/running
    tasks, prompt templates, or kb_* tables. dry_run=true (the default) only
    returns row-count estimates. Time-budgeted; returns truncated=true +
    remaining counts when the budget runs out — re-invoke to continue.

    Fail-closed auth: requires BOTH a valid X-Internal-Auth (the control
    plane strips client-forged copies and injects the real secret only for
    authenticated users) and X-KB-Role=admin. Direct calls missing either
    are rejected — this endpoint permanently deletes data.
    """
    expected = getattr(request.app.state, "internal_verify_secret", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="internal verify secret not configured — cleanup refuses to run",
        )
    provided = request.headers.get("x-internal-auth", "")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="valid internal auth required")
    if request.headers.get("x-kb-role") != "admin":
        raise HTTPException(status_code=403, detail="admin role required")

    from llm_service.runtime.cleanup import run_cleanup

    result = await run_cleanup(
        request.app.state.db,
        retention_days=body.retention_days,
        dry_run=body.dry_run,
        knowledge_domain=body.knowledge_domain,
    )
    deleted_or_estimates = result.get("deleted") or result.get("estimates") or {}
    logger.info(
        "cleanup retention_days=%s domain=%s dry_run=%s -> tasks=%s truncated=%s",
        body.retention_days,
        body.knowledge_domain or "ALL",
        body.dry_run,
        deleted_or_estimates.get("tasks"),
        result.get("truncated"),
    )
    return {"success": True, "data": result}
