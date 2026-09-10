"""Tests for runtime/idempotency.py::find_existing_task.

These tests lock down the status-priority semantics so that refactors of
LLMService._submit_with_idempotency (which should delegate here) cannot
silently change behavior.
"""
import pytest

from llm_service.runtime.idempotency import find_existing_task


class _FakeDB:
    """Minimal stub: maps (idempotency_key, status) -> ordered list of task_ids.

    `fetchone` returns the first row configured for that (key, status) pair,
    or None.
    """

    def __init__(self, rows: dict[tuple[str, str], list[str]]):
        self._rows = rows
        self.queries: list[tuple[str, str]] = []

    async def fetchone(self, sql, params):
        key, status = params
        self.queries.append((key, status))
        ids = self._rows.get((key, status), [])
        return {"id": ids[0]} if ids else None


pytestmark = pytest.mark.asyncio


async def test_find_existing_task_prefers_succeeded_over_running_and_queued():
    """All three statuses exist → succeeded wins."""
    db = _FakeDB({
        ("k1", "succeeded"): ["t-succ"],
        ("k1", "running"): ["t-run"],
        ("k1", "queued"): ["t-q"],
    })
    result = await find_existing_task(db, "k1")
    assert result == "t-succ"
    # Must short-circuit on first hit — should not query running/queued
    assert db.queries == [("k1", "succeeded")]


async def test_find_existing_task_falls_back_to_running():
    db = _FakeDB({
        ("k1", "running"): ["t-run"],
        ("k1", "queued"): ["t-q"],
    })
    result = await find_existing_task(db, "k1")
    assert result == "t-run"
    assert db.queries == [("k1", "succeeded"), ("k1", "running")]


async def test_find_existing_task_falls_back_to_queued():
    db = _FakeDB({("k1", "queued"): ["t-q"]})
    result = await find_existing_task(db, "k1")
    assert result == "t-q"
    assert db.queries == [("k1", "succeeded"), ("k1", "running"), ("k1", "queued")]


async def test_find_existing_task_returns_none_when_only_terminal():
    """failed / dead_letter / cancelled are never queried → returns None."""
    db = _FakeDB({})  # no active rows; only terminal states exist in real DB
    result = await find_existing_task(db, "k1")
    assert result is None
    # The function never queries terminal states
    assert ("k1", "failed") not in db.queries
    assert ("k1", "dead_letter") not in db.queries
    assert ("k1", "cancelled") not in db.queries


async def test_find_existing_task_returns_none_for_unknown_key():
    db = _FakeDB({})
    result = await find_existing_task(db, "never-seen")
    assert result is None
    assert len(db.queries) == 3  # all three active statuses checked


async def test_find_existing_task_picks_latest_within_status():
    """ORDER BY created_at DESC means the first row returned is the latest."""
    db = _FakeDB({("k1", "succeeded"): ["latest-id", "older-id"]})
    result = await find_existing_task(db, "k1")
    assert result == "latest-id"


# ---------------------------------------------------------------------------
# request fingerprint（batch 审查问题二）：key 命中还必须请求契约一致
# ---------------------------------------------------------------------------

import json as _json


class _FingerprintDB:
    """按 (key, status) 配任务 + task_id -> request 行的指纹查询 stub。"""

    def __init__(
        self,
        tasks: dict[tuple[str, str], list[str]],
        requests: dict[str, dict],
    ):
        self._tasks = tasks
        self._requests = requests

    async def fetchone(self, sql, params):
        if "FROM agent_llm_requests" in sql:
            return self._requests.get(params[0])
        if "FROM agent_llm_tasks" in sql:
            key, status = params
            ids = self._tasks.get((key, status), [])
            return {"id": ids[0]} if ids else None
        raise AssertionError(f"unexpected sql: {sql}")

    async def fetchall(self, sql, params):
        if "FROM agent_llm_tasks" in sql:
            key, status = params[:2]
            ids = self._tasks.get((key, status), [])
            return [{"id": i} for i in ids]
        raise AssertionError(f"unexpected fetchall sql: {sql}")


def _request_row(input_dict: dict | None = None, messages: list | None = None) -> dict:
    return {
        "input_json": _json.dumps(input_dict if input_dict is not None else {}),
        "messages_json": _json.dumps(messages if messages is not None else []),
        "params_json": "{}",
        "expected_output_type": "embedding",
    }


def _fp(input_dict: dict | None = None, messages: list | None = None) -> str:
    from llm_service.runtime.idempotency import request_fingerprint

    row = _request_row(input_dict, messages)
    return request_fingerprint(
        messages_json=row["messages_json"], input_json=row["input_json"],
        params_json=row["params_json"], expected_output_type=row["expected_output_type"],
    )


async def test_fingerprint_match_returns_task():
    db = _FingerprintDB(
        tasks={("emb:k", "succeeded"): ["t-old"]},
        requests={"t-old": _request_row(input_dict={"texts": ["a"], "model": "m1"})},
    )
    out = await find_existing_task(
        db,
        "emb:k",
        request_fingerprint=_fp(input_dict={"texts": ["a"], "model": "m1"}),
    )
    assert out == "t-old"


async def test_fingerprint_mismatch_model_changed_returns_none():
    """服务端模型变化 → input_json 不同 → 同 key 不复用旧任务。"""
    db = _FingerprintDB(
        tasks={("emb:k", "succeeded"): ["t-old"]},
        requests={"t-old": _request_row(input_dict={"texts": ["a"], "model": "old-model"})},
    )
    out = await find_existing_task(
        db,
        "emb:k",
        request_fingerprint=_fp(input_dict={"texts": ["a"], "model": "new-model"}),
    )
    assert out is None


async def test_fingerprint_mismatch_texts_changed_returns_none():
    db = _FingerprintDB(
        tasks={("emb:k", "succeeded"): ["t-old"]},
        requests={"t-old": _request_row(input_dict={"texts": ["a"], "model": "m"})},
    )
    out = await find_existing_task(
        db,
        "emb:k",
        request_fingerprint=_fp(input_dict={"texts": ["a", "b"], "model": "m"}),
    )
    assert out is None


async def test_fingerprint_mismatch_falls_through_to_older_candidate():
    """最新候选不匹配但更早的候选匹配时，返回匹配的那个（同状态内遍历）。"""
    db = _FingerprintDB(
        tasks={("emb:k", "succeeded"): ["t-new", "t-old"]},
        requests={
            "t-new": _request_row(input_dict={"texts": ["a"], "model": "m2"}),
            "t-old": _request_row(input_dict={"texts": ["a"], "model": "m1"}),
        },
    )
    out = await find_existing_task(
        db,
        "emb:k",
        request_fingerprint=_fp(input_dict={"texts": ["a"], "model": "m1"}),
    )
    assert out == "t-old"


async def test_no_fingerprint_keeps_legacy_behavior():
    """不传指纹 = 旧行为：key 命中即复用（向后兼容）。"""
    db = _FingerprintDB(
        tasks={("emb:k", "succeeded"): ["t-old"]},
        requests={"t-old": _request_row(input_dict={"texts": ["whatever"]})},
    )
    assert await find_existing_task(db, "emb:k") == "t-old"
    assert await find_existing_task(db, "emb:k", request_fingerprint=None) == "t-old"


async def test_fingerprint_matches_jsonb_round_trip_key_order():
    """JSONB 回读：提交侧字符串（插入序）vs 回读侧 dict（PG 规范化键序）必须匹配。"""
    from llm_service.runtime.idempotency import request_fingerprint

    submit_side = request_fingerprint(
        messages_json='[]',
        input_json='{"texts": ["a"], "model": "m1", "dimensions": null}',
        params_json="{}",
        expected_output_type="embedding",
    )
    read_back_side = request_fingerprint(
        messages_json=[],
        input_json={"model": "m1", "texts": ["a"], "dimensions": None},  # PG 键序
        params_json={},
        expected_output_type="embedding",
    )
    assert submit_side == read_back_side


async def test_fingerprint_jsonb_round_trip_model_change_still_differs():
    from llm_service.runtime.idempotency import request_fingerprint

    submit_side = request_fingerprint(
        messages_json="[]",
        input_json='{"texts": ["a"], "model": "m1", "dimensions": null}',
        params_json="{}", expected_output_type="embedding",
    )
    read_back_side = request_fingerprint(
        messages_json=[],
        input_json={"texts": ["a"], "model": "m2", "dimensions": None},
        params_json={}, expected_output_type="embedding",
    )
    assert submit_side != read_back_side
