"""Tests for POST /api/v1/tasks/batch-status (LLMService.get_tasks_batch).

Locks the contract the mining async task channel polls against: request
order, inline results for succeeded tasks, latest-attempt error for
failed/dead_letter, not_found for unknown ids, include_results=False.
"""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from llm_service.models import BatchStatusRequest
from llm_service.runtime.service import LLMService


def _task_row(task_id: str, status: str, **overrides) -> dict:
    row = {
        "id": task_id,
        "caller_service": "mining",
        "knowledge_domain": "default",
        "pipeline_stage": "embedding",
        "task_type": "embedding",
        "status": status,
        "idempotency_key": f"emb:{task_id}",
        "priority": 100,
        "attempt_count": 1,
        "max_attempts": 3,
        "metadata_json": "{}",
        "created_at": "2026-09-08T00:00:00+00:00",
        "updated_at": "2026-09-08T00:00:01+00:00",
        "started_at": "2026-09-08T00:00:00+00:00",
        "finished_at": "2026-09-08T00:00:01+00:00",
    }
    row.update(overrides)
    return row


def _result_row(task_id: str, parsed: dict | None = None, text: str | None = None) -> dict:
    import json

    return {
        "id": f"res-{task_id}",
        "task_id": task_id,
        "parse_status": "not_required",
        "parsed_output_json": json.dumps(parsed or {}),
        "text_output": text,
        "parse_error": None,
        "validation_errors_json": "[]",
        "created_at": "2026-09-08T00:00:01+00:00",
    }


def _attempt_row(task_id: str, attempt_no: int, error_type: str, error_message: str) -> dict:
    return {
        "id": f"att-{task_id}-{attempt_no}",
        "task_id": task_id,
        "attempt_no": attempt_no,
        "error_type": error_type,
        "error_message": error_message,
    }


class _FakeDB:
    """Dispatches fetchall by SQL table — enough for get_tasks_batch's queries."""

    def __init__(self, *, tasks=(), results=(), attempts=()):
        self._tasks = list(tasks)
        self._results = list(results)
        self._attempts = list(attempts)

    async def fetchone(self, sql, params):
        rows = await self.fetchall(sql, params)
        return rows[0] if rows else None

    async def fetchall(self, sql, params):
        ids = list(params)
        if "FROM agent_llm_results" in sql:
            # Return in configured order — the query contract must not rely on it.
            # JOIN 语义由 fake 合成：_attempt_no 取 attempts 行（缺省 0）。
            attempt_no = {a["id"]: a["attempt_no"] for a in self._attempts}
            rows = []
            for r in self._results:
                if r["task_id"] not in ids:
                    continue
                row = dict(r)
                row["_attempt_no"] = attempt_no.get(r.get("attempt_id"), 0)
                rows.append(row)
            return rows
        if "FROM agent_llm_attempts" in sql:
            return [a for a in self._attempts if a["task_id"] in ids]
        if "FROM agent_llm_tasks" in sql:
            by_id = {t["id"]: t for t in self._tasks}
            return [by_id[i] for i in ids if i in by_id]
        raise AssertionError(f"unexpected SQL in get_tasks_batch: {sql}")


def _chat_result_row(task_id: str, attempt_id: str, parse_status: str, parsed: dict | None) -> dict:
    import json

    return {
        "id": f"res-{attempt_id}",
        "task_id": task_id,
        "attempt_id": attempt_id,
        "parse_status": parse_status,
        "parsed_output_json": json.dumps(parsed or {}),
        "text_output": None,
        "parse_error": None if parse_status == "succeeded" else "bad json",
        "validation_errors_json": "[]",
        "created_at": "2026-09-09T00:00:01+00:00",
    }


def _svc(db, config) -> LLMService:
    # get_tasks_batch only touches _db; the rest are constructor dummies.
    return LLMService(db=db, provider=None, config=config, templates=object())


pytestmark = pytest.mark.asyncio


async def test_returns_tasks_in_request_order_with_inline_results(config):
    db = _FakeDB(
        tasks=[_task_row("t-a", "succeeded"), _task_row("t-b", "succeeded")],
        results=[
            _result_row("t-b", parsed={"model": "m", "data": [{"index": 0, "embedding": [0.1]}]}),
            _result_row("t-a", parsed={"model": "m", "data": [{"index": 0, "embedding": [0.2]}]}),
        ],
    )
    out = await _svc(db, config).get_tasks_batch(["t-b", "t-a"])
    assert [t["task_id"] for t in out["tasks"]] == ["t-b", "t-a"]
    assert out["not_found"] == []
    for entry in out["tasks"]:
        assert entry["status"] == "succeeded"
        assert entry["result"]["parsed_output"]["data"][0]["embedding"]
        assert entry["error"] is None
        # task_id alias alongside the mapped id field
        assert entry["id"] == entry["task_id"]


async def test_reports_not_found_ids_without_duplicating_known_ones(config):
    db = _FakeDB(tasks=[_task_row("t-a", "queued")])
    out = await _svc(db, config).get_tasks_batch(["t-a", "t-missing", "t-a"])
    assert [t["task_id"] for t in out["tasks"]] == ["t-a"]  # dedup + known only
    assert out["not_found"] == ["t-missing"]
    queued = out["tasks"][0]
    assert queued["status"] == "queued"
    assert queued["result"] is None  # non-succeeded never carries a result
    assert queued["error"] is None


async def test_attaches_error_from_latest_failed_attempt(config):
    db = _FakeDB(
        tasks=[_task_row("t-dl", "dead_letter", attempt_count=3)],
        attempts=[
            _attempt_row("t-dl", 1, "rate_limited", "attempt 1"),
            _attempt_row("t-dl", 3, "server_error", "attempt 3"),
            _attempt_row("t-dl", 2, "timeout", "attempt 2"),
        ],
    )
    out = await _svc(db, config).get_tasks_batch(["t-dl"])
    entry = out["tasks"][0]
    assert entry["error"] == {"error_type": "server_error", "error_message": "attempt 3"}
    assert entry["result"] is None


async def test_include_results_false_skips_results_lookup(config):
    class _NoResultDB(_FakeDB):
        async def fetchall(self, sql, params):
            assert "FROM agent_llm_results" not in sql, "results must not be queried"
            return await super().fetchall(sql, params)

    db = _NoResultDB(tasks=[_task_row("t-a", "succeeded")])
    out = await _svc(db, config).get_tasks_batch(["t-a"], include_results=False)
    assert out["tasks"][0]["result"] is None


async def test_get_tasks_batch_empty_input_raises(config):
    with pytest.raises(ValueError):
        await _svc(_FakeDB(), config).get_tasks_batch([])


def test_request_model_rejects_empty_and_over_256():
    with pytest.raises(ValidationError):
        BatchStatusRequest(task_ids=[])
    with pytest.raises(ValidationError):
        BatchStatusRequest(task_ids=[f"t-{i}" for i in range(257)])
    ok = BatchStatusRequest(task_ids=["a", "b"])
    assert ok.include_results is True


async def test_route_defensive_400_on_constructed_empty_body():
    from llm_service.api.tasks import batch_status_tasks
    from fastapi import HTTPException

    body = BatchStatusRequest.model_construct(task_ids=[], include_results=True)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llm_service=None)))
    with pytest.raises(HTTPException) as exc_info:
        await batch_status_tasks(body, request)
    assert exc_info.value.status_code == 400


async def test_route_returns_service_payload(config):
    from llm_service.api.tasks import batch_status_tasks

    db = _FakeDB(tasks=[_task_row("t-a", "succeeded")])
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(llm_service=_svc(db, config))),
    )
    body = BatchStatusRequest(task_ids=["t-a"])
    out = await batch_status_tasks(body, request)
    assert out["success"] is True
    assert out["data"]["tasks"][0]["task_id"] == "t-a"


async def test_get_tasks_batch_picks_result_of_latest_attempt_out_of_order(config):
    """chat 重试产生多条 results 行且行序不定时，必须取最新 attempt 的结果。

    attempt-1 解析失败、attempt-2 成功；结果行按【新在前】的乱序返回
    （模拟 PG 无序），若按行序 last-wins 会拿到 attempt-1 的 parse_failed。
    """
    db = _FakeDB(
        tasks=[_task_row("t-chat", "succeeded", task_type="chat", attempt_count=2)],
        results=[
            _chat_result_row("t-chat", "att-2", "succeeded", {"ok": True}),
            _chat_result_row("t-chat", "att-1", "parse_failed", None),
        ],
        attempts=[
            {"task_id": "t-chat", "attempt_no": 2, "id": "att-2"},
            {"task_id": "t-chat", "attempt_no": 1, "id": "att-1"},
        ],
    )
    out = await _svc(db, config).get_tasks_batch(["t-chat"])
    entry = out["tasks"][0]
    assert entry["result"]["parse_status"] == "succeeded"
    assert entry["result"]["parsed_output"] == {"ok": True}


async def test_get_tasks_batch_picks_latest_attempt_when_rows_returned_oldest_first(config):
    """同一契约的反向乱序：旧行在前时不得让 dict 覆盖顺序决定结果。"""
    db = _FakeDB(
        tasks=[_task_row("t-chat", "succeeded", task_type="chat", attempt_count=2)],
        results=[
            _chat_result_row("t-chat", "att-1", "parse_failed", None),
            _chat_result_row("t-chat", "att-2", "succeeded", {"ok": True}),
        ],
        attempts=[
            {"task_id": "t-chat", "attempt_no": 1, "id": "att-1"},
            {"task_id": "t-chat", "attempt_no": 2, "id": "att-2"},
        ],
    )
    out = await _svc(db, config).get_tasks_batch(["t-chat"])
    assert out["tasks"][0]["result"]["parse_status"] == "succeeded"


async def test_get_result_picks_latest_attempt_result(config):
    """单任务 get_result（GET /tasks/{id} 的 result 字段）同一契约。"""
    db = _FakeDB(
        tasks=[_task_row("t-chat", "succeeded", task_type="chat")],
        results=[
            _chat_result_row("t-chat", "att-2", "succeeded", {"final": 1}),
            _chat_result_row("t-chat", "att-1", "parse_failed", None),
        ],
        attempts=[
            {"task_id": "t-chat", "attempt_no": 2, "id": "att-2"},
            {"task_id": "t-chat", "attempt_no": 1, "id": "att-1"},
        ],
    )
    result = await _svc(db, config).get_result("t-chat")
    assert result is not None
    assert result["parse_status"] == "succeeded"
    assert result["parsed_output"] == {"final": 1}
