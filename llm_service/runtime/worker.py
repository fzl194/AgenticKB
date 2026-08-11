"""Background worker that claims and executes queued tasks."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from llm_service.db import LlmRuntimeDB
from llm_service.providers.base import ProviderProtocol
from llm_service.providers.model_base import ModelProviderProtocol
from llm_service.providers.utils import extract_doc_text
from llm_service.runtime.event_bus import EventBus
from llm_service.runtime.task_manager import TaskManager
from llm_service.runtime.template_registry import TemplateRegistry

if TYPE_CHECKING:
    from llm_service.runtime.service import LLMService

logger = logging.getLogger(__name__)


def _ensure_dict(value):
    """Ensure value is a dict — psycopg JSONB columns are already parsed."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _ensure_list(value):
    """Ensure value is a list — psycopg JSONB columns are already parsed."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


class Worker:
    """Background worker loop: continuously claim -> execute -> complete/fail."""

    def __init__(
        self,
        db: LlmRuntimeDB,
        task_manager: TaskManager,
        event_bus: EventBus,
        provider: ProviderProtocol,
        model_provider: ModelProviderProtocol | None = None,
        templates: TemplateRegistry | None = None,
        concurrency: int = 2,
        poll_interval: float = 1.0,
        llm_service: LLMService | None = None,
    ):
        self._db = db
        self._mgr = task_manager
        self._bus = event_bus
        self._provider = provider
        self._model_provider = model_provider
        self._templates = templates
        self._concurrency = concurrency
        self._poll_interval = poll_interval
        self._svc = llm_service
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._active_task_ids: set[str] = set()

    async def start(self) -> None:
        self._running = True
        for i in range(self._concurrency):
            name = f"worker-{i}"
            t = asyncio.create_task(self._loop(name), name=name)
            self._tasks.append(t)
        logger.info("Worker started with %d concurrency", self._concurrency)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Worker stopped")

    async def _loop(self, name: str) -> None:
        while self._running:
            try:
                task_id = await self._mgr.claim()
                if task_id:
                    self._active_task_ids.add(task_id)
                    try:
                        await self._execute_task(task_id)
                    finally:
                        self._active_task_ids.discard(task_id)
                else:
                    await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.exception("%s error: %s", name, e)
                await asyncio.sleep(self._poll_interval)

    async def _execute_task(self, task_id: str) -> None:
        """Dispatch to the correct executor based on task_type.

        Safety net: if the executor fails without calling _mgr.complete/fail,
        we ensure the task is transitioned out of 'running' so it doesn't block
        the Worker from claiming new tasks.
        """
        try:
            row = await self._db.fetchone(
                "SELECT task_type FROM agent_llm_tasks WHERE id = %s",
                (task_id,),
            )
            if not row:
                await self._mgr.fail(task_id, "missing_task", "task row not found")
                return
            task_type = row["task_type"]

            if task_type == "chat":
                await self._execute_chat(task_id)
            elif task_type == "embedding":
                await self._execute_embedding(task_id)
            elif task_type == "rerank":
                await self._execute_rerank(task_id)
            else:
                await self._mgr.fail(task_id, "unknown_task_type", f"unsupported task_type: {task_type}")
        except Exception:
            # Safety net: if executor failed without completing/failing the task,
            # ensure it transitions out of 'running' so the Worker can proceed.
            logger.exception("Safety-net fail for task %s", task_id[:8])
            try:
                await self._mgr.fail(task_id, "worker_error", "executor failed without completing task")
            except Exception:
                logger.exception("Safety-net fail ALSO failed for task %s", task_id[:8])

    # ------------------------------------------------------------------
    # Chat execution — delegates to LLMService.execute_chat_attempt()
    # ------------------------------------------------------------------

    async def _execute_chat(self, task_id: str) -> None:
        """Execute a chat task: read request -> delegate to shared execution."""
        req = await self._db.fetchone("SELECT * FROM agent_llm_requests WHERE task_id = %s", (task_id,))
        if not req:
            await self._mgr.fail(task_id, "missing_request", "no request row found")
            return

        messages = _ensure_list(req["messages_json"])
        params = _ensure_dict(req["params_json"])
        expected_type = req["expected_output_type"]
        schema = _ensure_dict(req["output_schema_json"]) or None
        request_id = req["id"]

        try:
            await self._svc.execute_chat_attempt(
                task_id, request_id, messages, params,
                expected_type, schema,
                model=req.get("model"),
            )
        except Exception as e:
            # execute_chat_attempt no longer re-raises — it handles _mgr.fail() internally.
            # If we reach here, something truly unexpected happened (e.g. DB query for request failed).
            # Let the safety net in _execute_task handle it.
            logger.warning("Chat execution failed for task %s: %s", task_id[:8], e)
            raise

    # ------------------------------------------------------------------
    # Embedding execution
    # ------------------------------------------------------------------

    async def _execute_embedding(self, task_id: str) -> None:
        """Execute an embedding task: read input -> call model provider -> store result."""
        if not self._model_provider:
            await self._mgr.fail(task_id, "no_model_provider", "model_provider not configured")
            return

        req = await self._db.fetchone("SELECT * FROM agent_llm_requests WHERE task_id = %s", (task_id,))
        if not req:
            await self._mgr.fail(task_id, "missing_request", "no request row found")
            return

        input_data = _ensure_dict(req["input_json"])
        texts = input_data.get("texts", [])
        model = input_data.get("model")
        dimensions = input_data.get("dimensions")
        request_id = req["id"]

        task_row = await self._db.fetchone("SELECT attempt_count, max_attempts FROM agent_llm_tasks WHERE id = %s", (task_id,))
        if not task_row:
            return
        attempt_no = task_row["attempt_count"] + 1

        attempt_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO agent_llm_attempts
               (id, task_id, request_id, attempt_no, status, started_at)
               VALUES (%s, %s, %s, %s, 'running', %s)""",
            (attempt_id, task_id, request_id, attempt_no, now),
        )

        start = time.monotonic()
        try:
            result = await self._model_provider.embed(texts, model=model, dimensions=dimensions)
            latency = int((time.monotonic() - start) * 1000)
            finished = datetime.now(timezone.utc).isoformat()

            token_usage = 0
            if result.get("usage"):
                token_usage = result["usage"].get("total_tokens", 0)

            await self._db.execute(
                """UPDATE agent_llm_attempts
                   SET status = 'succeeded', total_tokens = %s, latency_ms = %s, finished_at = %s,
                       raw_response_json = %s
                   WHERE id = %s""",
                (token_usage, latency, finished, json.dumps(result), attempt_id),
            )

            text_output = "\n".join(texts)

            result_id = str(uuid.uuid4())
            await self._db.execute(
                """INSERT INTO agent_llm_results
                   (id, task_id, attempt_id, parse_status, parsed_output_json, text_output,
                    parse_error, validation_errors_json, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    result_id, task_id, attempt_id, "not_required",
                    json.dumps(result), text_output, None, "[]",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

            await self._mgr.complete(task_id)

        except Exception as e:
            latency = int((time.monotonic() - start) * 1000)
            finished = datetime.now(timezone.utc).isoformat()
            error_type = getattr(e, "error_type", "unexpected_error")
            error_msg = str(e)

            await self._db.execute(
                """UPDATE agent_llm_attempts
                   SET status = 'failed', error_type = %s, error_message = %s, latency_ms = %s, finished_at = %s
                   WHERE id = %s""",
                (error_type, error_msg, latency, finished, attempt_id),
            )

            await self._mgr.fail(task_id, error_type, error_msg)

    # ------------------------------------------------------------------
    # Rerank execution
    # ------------------------------------------------------------------

    async def _execute_rerank(self, task_id: str) -> None:
        """Execute a rerank task: read input -> call model provider -> store result."""
        if not self._model_provider:
            await self._mgr.fail(task_id, "no_model_provider", "model_provider not configured")
            return

        req = await self._db.fetchone("SELECT * FROM agent_llm_requests WHERE task_id = %s", (task_id,))
        if not req:
            await self._mgr.fail(task_id, "missing_request", "no request row found")
            return

        input_data = _ensure_dict(req["input_json"])
        query = input_data.get("query", "")
        documents = input_data.get("documents", [])
        model = input_data.get("model")
        top_n = input_data.get("top_n")
        request_id = req["id"]

        task_row = await self._db.fetchone("SELECT attempt_count, max_attempts FROM agent_llm_tasks WHERE id = %s", (task_id,))
        if not task_row:
            return
        attempt_no = task_row["attempt_count"] + 1

        attempt_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO agent_llm_attempts
               (id, task_id, request_id, attempt_no, status, started_at)
               VALUES (%s, %s, %s, %s, 'running', %s)""",
            (attempt_id, task_id, request_id, attempt_no, now),
        )

        start = time.monotonic()
        try:
            result = await self._model_provider.rerank(query, documents, model=model, top_n=top_n)
            latency = int((time.monotonic() - start) * 1000)
            finished = datetime.now(timezone.utc).isoformat()

            token_usage = result.get("usage", {}).get("total_tokens", 0)

            await self._db.execute(
                """UPDATE agent_llm_attempts
                   SET status = 'succeeded', total_tokens = %s, latency_ms = %s, finished_at = %s,
                       raw_response_json = %s
                   WHERE id = %s""",
                (token_usage, latency, finished, json.dumps(result), attempt_id),
            )

            lines = [f"Query: {query}"]
            for r in result.get("results", []):
                score = r.get("relevance_score", 0)
                doc = extract_doc_text(r.get("document"))[:100]
                lines.append(f"  [{r.get('index', '?')}] score={score:.4f}: {doc}")
            text_output = "\n".join(lines)

            result_id = str(uuid.uuid4())
            await self._db.execute(
                """INSERT INTO agent_llm_results
                   (id, task_id, attempt_id, parse_status, parsed_output_json, text_output,
                    parse_error, validation_errors_json, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    result_id, task_id, attempt_id, "not_required",
                    json.dumps(result), text_output, None, "[]",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

            await self._mgr.complete(task_id)

        except Exception as e:
            latency = int((time.monotonic() - start) * 1000)
            finished = datetime.now(timezone.utc).isoformat()
            error_type = getattr(e, "error_type", "unexpected_error")
            error_msg = str(e)

            await self._db.execute(
                """UPDATE agent_llm_attempts
                   SET status = 'failed', error_type = %s, error_message = %s, latency_ms = %s, finished_at = %s
                   WHERE id = %s""",
                (error_type, error_msg, latency, finished, attempt_id),
            )

            await self._mgr.fail(task_id, error_type, error_msg)


class LeaseRecovery:
    """Periodically scans for running tasks whose lease has expired and re-queues them."""

    def __init__(
        self,
        db: LlmRuntimeDB,
        task_manager: TaskManager,
        event_bus: EventBus,
        interval: float = 30.0,
    ):
        self._db = db
        self._mgr = task_manager
        self._bus = event_bus
        self._interval = interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("LeaseRecovery started (interval=%.1fs)", self._interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        logger.info("LeaseRecovery stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._recover()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.exception("LeaseRecovery error: %s", e)
            await asyncio.sleep(self._interval)

    async def _recover(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        expired = await self._db.fetchall(
            """SELECT id, attempt_count, max_attempts FROM agent_llm_tasks
               WHERE status = 'running' AND lease_expires_at < %s""",
            (now,),
        )
        if not expired:
            return

        for row in expired:
            task_id = row["id"]
            attempt_count = row["attempt_count"]
            max_attempts = row["max_attempts"]

            # Mark the hanging attempt as failed (if any)
            await self._db.execute(
                """UPDATE agent_llm_attempts SET status = 'failed', error_type = 'lease_expired',
                   error_message = 'lease expired, recovered by lease recovery'
                   WHERE task_id = %s AND status = 'running'""",
                (task_id,),
            )

            if attempt_count < max_attempts:
                await self._mgr.fail(task_id, "lease_expired", "lease expired, re-queued")
                logger.info("LeaseRecovery: re-queued task %s", task_id[:8])
            else:
                await self._mgr.fail(task_id, "lease_expired", "lease expired, exhausted")
                logger.info("LeaseRecovery: dead_lettered task %s", task_id[:8])
