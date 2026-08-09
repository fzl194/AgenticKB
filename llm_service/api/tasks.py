from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from llm_service.models import EmbeddingTaskRequest, RerankTaskRequest, TaskSubmitRequest

router = APIRouter(prefix="/api/v1")


@router.post("/tasks")
async def submit_task(body: TaskSubmitRequest, request: Request):
    svc = request.app.state.llm_service
    task_id = await svc.submit(
        body.caller_service,
        body.knowledge_domain,
        body.pipeline_stage,
        template_key=body.template_key,
        input=body.input,
        messages=body.messages,
        params=body.params,
        expected_output_type=body.expected_output_type,
        output_schema=body.output_schema,
        idempotency_key=body.idempotency_key,
        metadata=body.metadata,
        max_attempts=body.max_attempts,
        priority=body.priority,
        model=body.model,
    )
    task = await svc.get_task(task_id)
    return {
        "success": True,
        "data": {
            "task_id": task_id,
            "status": task["status"],
            "idempotency_key": task.get("idempotency_key"),
            "created_at": task["created_at"],
        },
    }


@router.post("/tasks/embed")
async def submit_embedding_task(body: EmbeddingTaskRequest, request: Request):
    svc = request.app.state.llm_service
    texts = body.input if isinstance(body.input, list) else [body.input]
    task_id = await svc.submit_embedding(
        texts,
        model=body.model,
        dimensions=body.dimensions,
        caller_service=body.caller_service,
        knowledge_domain=body.knowledge_domain,
        pipeline_stage=body.pipeline_stage,
        idempotency_key=body.idempotency_key,
        max_attempts=body.max_attempts,
        priority=body.priority,
    )
    task = await svc.get_task(task_id)
    return {
        "success": True,
        "data": {
            "task_id": task_id,
            "status": task["status"],
            "created_at": task["created_at"],
        },
    }


@router.post("/tasks/rerank")
async def submit_rerank_task(body: RerankTaskRequest, request: Request):
    svc = request.app.state.llm_service
    task_id = await svc.submit_rerank(
        body.query,
        body.documents,
        model=body.model,
        top_n=body.top_n,
        caller_service=body.caller_service,
        knowledge_domain=body.knowledge_domain,
        pipeline_stage=body.pipeline_stage,
        idempotency_key=body.idempotency_key,
        max_attempts=body.max_attempts,
        priority=body.priority,
    )
    task = await svc.get_task(task_id)
    return {
        "success": True,
        "data": {
            "task_id": task_id,
            "status": task["status"],
            "created_at": task["created_at"],
        },
    }


@router.post("/execute")
async def execute_task(body: TaskSubmitRequest, request: Request):
    svc = request.app.state.llm_service
    result = await svc.execute(
        body.caller_service,
        body.knowledge_domain,
        body.pipeline_stage,
        template_key=body.template_key,
        input=body.input,
        messages=body.messages,
        params=body.params,
        expected_output_type=body.expected_output_type,
        output_schema=body.output_schema,
        idempotency_key=body.idempotency_key,
        metadata=body.metadata,
        max_attempts=body.max_attempts,
        priority=body.priority,
        model=body.model,
    )
    return {"success": True, "data": result}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    svc = request.app.state.llm_service
    task = await svc.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    # Include related data for task detail view
    db = request.app.state.db
    result = await svc.get_result(task_id)
    attempts = await svc.get_attempts(task_id)
    events = await svc.get_events(task_id)
    req_row = await db.fetchone("SELECT * FROM agent_llm_requests WHERE task_id = %s", (task_id,))
    return {
        "success": True,
        "data": {
            "task": dict(task),
            "request": dict(req_row) if req_row else None,
            "result": result,
            "attempts": attempts,
            "events": events,
        },
    }


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request):
    svc = request.app.state.llm_service
    await svc.cancel(task_id)
    return {"success": True, "data": {"task_id": task_id, "status": "cancelled"}}


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str, request: Request):
    """Retry a failed/dead_letter task by re-queuing it."""
    db = request.app.state.db
    row = await db.fetchone(
        "SELECT id, status, attempt_count, max_attempts FROM agent_llm_tasks WHERE id = %s",
        (task_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="task not found")
    if row["status"] not in ("failed", "dead_letter", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Cannot retry task in '{row['status']}' status")

    import json
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """UPDATE agent_llm_tasks
           SET status = 'queued', attempt_count = 0, available_at = %s,
               started_at = NULL, finished_at = NULL, lease_expires_at = NULL, updated_at = %s
           WHERE id = %s""",
        (now, now, task_id),
    )
    svc = request.app.state.llm_service
    await svc._bus.emit(task_id, "submitted", "task retried")
    return {"success": True, "data": {"task_id": task_id, "status": "queued"}}


class BatchCancelRequest(BaseModel):
    task_ids: list[str]


@router.post("/tasks/batch-cancel")
async def batch_cancel_tasks(body: BatchCancelRequest, request: Request):
    """Cancel multiple queued tasks at once."""
    if not body.task_ids:
        raise HTTPException(status_code=400, detail="task_ids cannot be empty")
    db = request.app.state.db
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    placeholders = ", ".join(["%s"] * len(body.task_ids))
    async with db.pool.connection() as conn:
        cur = await conn.execute(
            f"UPDATE agent_llm_tasks SET status = 'cancelled', finished_at = %s, updated_at = %s "
            f"WHERE id IN ({placeholders}) AND status = 'queued'",
            (now, now, *body.task_ids),
        )
        cancelled_count = cur.rowcount
    return {"success": True, "data": {"cancelled_count": cancelled_count}}
