"""Synchronous HTTP client for llm_service — used by Mining pipeline.

This is a sync wrapper that mirrors llm_service/client.py field names exactly.
Mining pipeline is synchronous, so we use httpx sync client instead of async.

All methods return None on failure (non-blocking for pipeline).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Default port matches llm_service QUICKSTART
DEFAULT_BASE_URL = "http://localhost:8900"

# Audit constants for llm_service multi-domain routing
CALLER_SERVICE = "mining"
UNKNOWN_DOMAIN = "unknown"


class LlmClient:
    """Sync HTTP client for llm_service. Field names match llm_service/client.py.

    Reuses a single httpx.Client across calls to avoid TCP reconnection
    overhead during high-frequency polling (poll_all).
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 60) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.Client | None = None

    def _get_client(self, timeout: int | None = None) -> httpx.Client:
        """Get or create a reusable httpx.Client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=timeout or self._timeout, proxy=None, trust_env=False)
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP connection."""
        if self._client is not None and not self._client.is_closed:
            self._client.close()
            self._client = None

    def submit_task(
        self,
        template_key: str,
        input: dict[str, Any] | None = None,
        caller_domain: str | None = None,
        pipeline_stage: str = "retrieval_units",
        expected_output_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        max_attempts: int = 3,
        *,
        caller_service: str = CALLER_SERVICE,
        knowledge_domain: str | None = None,
    ) -> str | None:
        """Submit async task via POST /api/v1/tasks. Returns task_id or None."""
        payload: dict[str, Any] = {
            "caller_service": caller_service,
            "knowledge_domain": knowledge_domain or UNKNOWN_DOMAIN,
            "pipeline_stage": pipeline_stage,
            "template_key": template_key,
            "max_attempts": max_attempts,
        }
        # Backward compat: if legacy caller_domain passed, add to payload
        if caller_domain is not None:
            payload["caller_domain"] = caller_domain
        if input is not None:
            payload["input"] = input
        if expected_output_type is not None:
            payload["expected_output_type"] = expected_output_type
        if metadata is not None:
            payload["metadata"] = metadata

        try:
            client = self._get_client()
            resp = client.post(f"{self._base_url}/api/v1/tasks", json=payload)
            resp.raise_for_status()
            body = resp.json()
            # API wraps in {"success": true, "data": {"task_id": ...}}
            return body.get("data", body).get("task_id")
        except Exception as e:
            logger.warning("submit_task failed: %s", e)
            self.close()
            return None

    def poll_result(
        self, task_id: str, poll_interval: float = 1.0, timeout: int = 120,
    ) -> list[dict] | None:
        """Poll task until completed. Returns parsed_output list or None.

        Polls GET /api/v1/tasks/{id} for status, then GET /tasks/{id}/result for output.
        """
        deadline = time.time() + timeout
        client = self._get_client()
        while time.time() < deadline:
            try:
                # Check task status
                resp = client.get(f"{self._base_url}/api/v1/tasks/{task_id}")
                resp.raise_for_status()
                body = resp.json()
                task_data = body.get("data", {}).get("task", {})
                status = task_data.get("status") if task_data else body.get("status", "")

                if status == "succeeded":
                    # Fetch result
                    r_resp = client.get(f"{self._base_url}/api/v1/tasks/{task_id}/result")
                    r_resp.raise_for_status()
                    r_body = r_resp.json()
                    result = r_body.get("data", r_body)
                    parsed = result.get("parsed_output")
                    # parsed_output might already be a list/dict
                    if isinstance(parsed, list):
                        return parsed
                    if isinstance(parsed, dict):
                        return [parsed]
                    # Try text_output as fallback
                    text = result.get("text_output")
                    if text:
                        try:
                            return json.loads(text)
                        except json.JSONDecodeError:
                            return None
                    return None

                if status in ("failed", "dead_letter", "cancelled"):
                    logger.info("Task %s ended with status %s", task_id, status)
                    return None

                # Still queued/running
                time.sleep(poll_interval)
            except Exception as e:
                logger.warning("poll_result error: %s", e)
                self.close()
                return None

        logger.warning("poll_result timed out for task %s", task_id)
        return None

    def check_status(self, task_id: str) -> str | None:
        """Non-blocking status check. Returns status string or None on error."""
        try:
            client = self._get_client()
            resp = client.get(f"{self._base_url}/api/v1/tasks/{task_id}")
            resp.raise_for_status()
            body = resp.json()
            # API wraps in {"success": true, "data": {"task": {"status": ...}}}
            task = body.get("data", {}).get("task", {})
            return task.get("status") if task else body.get("status")
        except Exception as e:
            logger.warning("check_status error for %s: %s", task_id, e)
            self.close()
            return None

    def fetch_result(self, task_id: str) -> list[dict] | None:
        """Fetch result for a completed task. Returns parsed_output or None."""
        try:
            client = self._get_client()
            resp = client.get(f"{self._base_url}/api/v1/tasks/{task_id}/result")
            resp.raise_for_status()
            body = resp.json()
            result = body.get("data", body)
            parsed = result.get("parsed_output")
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
            text = result.get("text_output")
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return None
            return None
        except Exception as e:
            logger.warning("fetch_result error for %s: %s", task_id, e)
            self.close()
            return None

    def poll_all(
        self,
        task_ids: dict[str, str],
        poll_interval: float = 1.0,
    ) -> dict[str, list[dict]]:
        """Poll multiple tasks concurrently — collect results as they complete.

        No timeout. Loops until all tasks resolve (success or failure).
        Stuck workers are handled by the supervisor layer above.

        Args:
            task_ids: {key -> task_id} mapping (e.g. {"0": "task-abc", "1": "task-def"})
            poll_interval: seconds between scan rounds

        Returns:
            {key -> parsed_output} for successfully completed tasks only.
        """
        results: dict[str, list[dict]] = {}
        pending: dict[str, str] = dict(task_ids)  # key -> task_id

        while pending:
            still_pending: dict[str, str] = {}
            for key, task_id in pending.items():
                status = self.check_status(task_id)

                if status == "succeeded":
                    result = self.fetch_result(task_id)
                    if result is not None:
                        results[key] = result

                elif status in ("failed", "dead_letter", "cancelled"):
                    logger.info("Task %s (%s) ended with status %s", key, task_id, status)

                elif status is None:
                    # HTTP error (LLM service temporarily unreachable) — keep trying
                    still_pending[key] = task_id

                else:
                    # queued / running — keep waiting
                    still_pending[key] = task_id

            pending = still_pending
            if pending:
                time.sleep(poll_interval)

        return results

    def execute(
        self,
        template_key: str | None = None,
        input: dict[str, Any] | None = None,
        caller_domain: str | None = None,
        pipeline_stage: str = "retrieval_units",
        expected_output_type: str | None = None,
        *,
        caller_service: str = CALLER_SERVICE,
        knowledge_domain: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        model: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict | None:
        """Sync execute via POST /api/v1/execute. Returns full response or None.

        Either ``template_key`` (+ optional ``input``) or caller ``messages``
        must be provided. ``model`` selects a provider.models entry (e.g. vision).
        """
        payload: dict[str, Any] = {
            "caller_service": caller_service,
            "knowledge_domain": knowledge_domain or UNKNOWN_DOMAIN,
            "pipeline_stage": pipeline_stage,
        }
        # Backward compat: if legacy caller_domain passed, add to payload
        if caller_domain is not None:
            payload["caller_domain"] = caller_domain
        if template_key is not None:
            payload["template_key"] = template_key
        if input is not None:
            payload["input"] = input
        if messages is not None:
            payload["messages"] = messages
        if model is not None:
            payload["model"] = model
        if params is not None:
            payload["params"] = params
        if expected_output_type is not None:
            payload["expected_output_type"] = expected_output_type

        try:
            client = self._get_client()
            # Vision captions can exceed the default 60s client timeout.
            req_timeout = 180.0 if model else self._timeout
            resp = client.post(
                f"{self._base_url}/api/v1/execute",
                json=payload,
                timeout=req_timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("execute failed: %s", e)
            self.close()
            return None

    def register_template(self, template: dict[str, Any]) -> bool:
        """Idempotent template registration via POST /api/v1/templates."""
        try:
            client = self._get_client()
            resp = client.post(f"{self._base_url}/api/v1/templates", json=template)
            return resp.status_code in (200, 201)
        except Exception as e:
            logger.warning("register_template failed: %s", e)
            self.close()
            return False

    def health_check(self) -> bool:
        """Quick health check. Returns True if llm_service is reachable."""
        try:
            client = self._get_client(timeout=5)
            resp = client.get(f"{self._base_url}/health")
            return resp.status_code == 200
        except Exception:
            self.close()
            return False
