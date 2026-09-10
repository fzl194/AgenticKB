"""LlmTaskClient 单测（httpx.MockTransport 假 llm_service）。

锁定：提交 payload 形状（messages 直传 / embedding 显式 max_attempts）、
提交失败上抛、批量等待收敛/超时/瞬时错误容忍/分块。
"""
from __future__ import annotations

import json

import httpx
import pytest

from knowledge_mining.mining.infra.llm_task_client import LlmTaskClient, LlmTaskError


def _response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"success": True, "data": {"task_id": "tid-1", "status": "queued"}},
    )


def test_submit_chat_payload_shape():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.read())
        return _response(request)

    client = LlmTaskClient(transport=httpx.MockTransport(handler))
    task_id = client.submit_chat(
        [{"role": "user", "content": "hi"}],
        expected_output_type="json_object",
        idempotency_key="qe:abc",
        pipeline_stage="mining_summary",
        max_attempts=3,
        knowledge_domain="default",
    )
    assert task_id == "tid-1"
    assert captured["path"] == "/api/v1/tasks"
    body = captured["body"]
    assert body["caller_service"] == "mining"
    assert body["knowledge_domain"] == "default"
    assert body["pipeline_stage"] == "mining_summary"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["expected_output_type"] == "json_object"
    assert body["idempotency_key"] == "qe:abc"
    assert body["max_attempts"] == 3


def test_submit_embedding_passes_explicit_max_attempts():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.read())
        return _response(request)

    client = LlmTaskClient(transport=httpx.MockTransport(handler))
    client.submit_embedding(["a", "b"], idempotency_key="emb:x", max_attempts=3)

    assert captured["path"] == "/api/v1/tasks/embed"
    body = captured["body"]
    assert body["input"] == ["a", "b"]
    assert body["caller_service"] == "mining"
    assert body["pipeline_stage"] == "embedding"
    assert body["max_attempts"] == 3  # 模型默认 2，必须显式覆盖
    assert body["idempotency_key"] == "emb:x"


def test_submit_failure_raises_llm_task_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = LlmTaskClient(transport=httpx.MockTransport(handler))
    with pytest.raises(LlmTaskError):
        client.submit_embedding(["a"])


def _status_handler(states: dict[str, str], results: dict[str, dict] | None = None):
    """task_id -> status，逐次推进由测试改 dict 实现。"""
    results = results or {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        tasks = []
        for task_id in body["task_ids"]:
            status = states.get(task_id, "queued")
            entry = {"task_id": task_id, "status": status}
            if status == "succeeded":
                entry["result"] = results.get(task_id)
            tasks.append(entry)
        return httpx.Response(200, json={"success": True, "data": {"tasks": tasks}})

    return handler


def test_wait_for_tasks_resolves_all_and_returns_results():
    states = {"t-1": "queued", "t-2": "succeeded"}
    client = LlmTaskClient(
        transport=httpx.MockTransport(_status_handler(states)),
        poll_interval=0.01,
        wait_timeout=2.0,
    )
    out = client.wait_for_tasks(["t-1", "t-2"])
    # first round: t-2 terminal; second round flips t-1
    states["t-1"] = "succeeded"
    out = client.wait_for_tasks(["t-1", "t-2"])
    assert set(out) == {"t-1", "t-2"}
    assert out["t-1"]["status"] == "succeeded"
    assert out["t-2"]["status"] == "succeeded"


def test_wait_for_tasks_marks_unresolved_as_timeout_after_deadline():
    client = LlmTaskClient(
        transport=httpx.MockTransport(_status_handler({"t-1": "running"})),
        poll_interval=0.01,
        wait_timeout=0.05,
    )
    out = client.wait_for_tasks(["t-1"])
    assert out["t-1"]["status"] == "timeout"
    assert out["t-1"]["error"]["error_type"] == "wait_timeout"


def test_wait_for_tasks_survives_transient_poll_error():
    calls = {"n": 0}
    states = {"t-1": "queued"}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="transient")
        return _status_handler(states)(request)

    client = LlmTaskClient(
        transport=httpx.MockTransport(handler), poll_interval=0.01, wait_timeout=2.0,
    )
    states["t-1"] = "succeeded"
    out = client.wait_for_tasks(["t-1"])
    assert out["t-1"]["status"] == "succeeded"
    assert calls["n"] >= 2


def test_wait_for_tasks_chunks_requests_over_status_chunk():
    seen_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        seen_sizes.append(len(body["task_ids"]))
        return httpx.Response(200, json={"success": True, "data": {"tasks": []}})

    client = LlmTaskClient(
        transport=httpx.MockTransport(handler),
        status_chunk=3,
        poll_interval=0.01,
        wait_timeout=0.05,  # nothing resolves -> timeout entries
    )
    ids = [f"t-{i}" for i in range(7)]
    out = client.wait_for_tasks(ids)
    assert all(entry["status"] == "timeout" for entry in out.values())
    assert seen_sizes and max(seen_sizes) <= 3


# ---------------------------------------------------------------------------
# 并发与生命周期（batch 审查问题四）
# ---------------------------------------------------------------------------

def test_submit_error_keeps_shared_client_usable():
    """单请求失败不得关闭共享连接池：同一 client 实例继续可用。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="transient")
        return httpx.Response(200, json={
            "success": True, "data": {"task_id": "tid-ok", "status": "queued"},
        })

    client = LlmTaskClient(transport=httpx.MockTransport(handler))
    first = client._get_client()
    with pytest.raises(LlmTaskError):
        client.submit_embedding(["poison"])
    # 失败后未关闭未重建：同一线程与其他线程继续复用同一连接池
    assert client._get_client() is first
    assert client.submit_embedding(["ok"]) == "tid-ok"


def test_close_is_idempotent_and_client_recreatable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "success": True, "data": {"task_id": "tid-1", "status": "queued"},
        })

    client = LlmTaskClient(transport=httpx.MockTransport(handler))
    first = client._get_client()
    client.close()
    client.close()  # 双重关闭不得抛异常
    assert first.is_closed
    # 关闭后仍可重建（迟到的调用不崩）
    recreated = client._get_client()
    assert recreated is not first
    assert client.submit_embedding(["a"]) == "tid-1"


def test_concurrent_failure_does_not_break_other_threads():
    """故障注入：一半线程持续 500，另一半的提交必须全部成功。"""
    import threading

    barrier = threading.Barrier(8)
    good_ok = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        if any("poison" in str(v) for v in body.get("input", [])):
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={
            "success": True, "data": {"task_id": "tid-x", "status": "queued"},
        })

    client = LlmTaskClient(transport=httpx.MockTransport(handler))

    def worker(name: str, poison: bool) -> None:
        barrier.wait()
        for _ in range(5):
            if poison:
                with pytest.raises(LlmTaskError):
                    client.submit_embedding(["poison"])
            else:
                good_ok.append(client.submit_embedding([f"good-{name}"]) == "tid-x")

    threads = [
        threading.Thread(target=worker, args=(f"p{i}", True)) for i in range(4)
    ] + [
        threading.Thread(target=worker, args=(f"g{i}", False)) for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(good_ok) == 20 and all(good_ok), "好请求被其他线程的失败中断"
