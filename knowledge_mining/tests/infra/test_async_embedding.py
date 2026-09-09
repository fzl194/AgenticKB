"""LLMServiceAsyncEmbeddingGenerator 单测（MockTransport 假 llm_service）。

锁定：32 条/任务自分批且保序、同内容批去重、失败终态（failed/dead_letter/
timeout）与畸形结果（条数不符/空向量）显式 raise——embedding_handler
FAILED 语义不变的客户端侧前提。
"""
from __future__ import annotations

import json

import httpx
import pytest

from knowledge_mining.mining.infra.embedding import LLMServiceAsyncEmbeddingGenerator


def _vectors(task_id: str, texts: list[str]) -> dict:
    return {
        "parse_status": "not_required",
        "parsed_output": {
            "model": "fake",
            "data": [
                {"index": i, "embedding": [float(len(task_id)), float(i + 1)]}
                for i in range(len(texts))
            ],
        },
    }


class _FakeLlm:
    """按内容哈希键模拟 llm_service：记录提交，按需返回终态。"""

    def __init__(self, *, final_status: str = "succeeded", results: dict[str, list] | None = None):
        self.submits: list[dict] = []  # {"path", "body"}
        self._status_by_task: dict[str, str] = {}
        self._texts_by_task: dict[str, list] = {}
        self._final_status = final_status
        self._results = results or {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.read())
        if path in ("/api/v1/tasks", "/api/v1/tasks/embed"):
            self.submits.append({"path": path, "body": body})
            task_id = f"task-{len(self.submits)}"
            texts = body.get("input") or []
            self._texts_by_task[task_id] = texts
            self._status_by_task[task_id] = "queued"
            return httpx.Response(200, json={
                "success": True,
                "data": {"task_id": task_id, "status": "queued"},
            })
        if path == "/api/v1/tasks/batch-status":
            tasks = []
            for task_id in body["task_ids"]:
                status = self._final_status
                entry = {"task_id": task_id, "status": status}
                if status == "succeeded":
                    override = self._results.get(task_id)
                    texts = self._texts_by_task.get(task_id, [])
                    entry["result"] = (
                        {"parse_status": "not_required", "parsed_output": override}
                        if override is not None else _vectors(task_id, texts)
                    )
                elif status in ("failed", "dead_letter"):
                    entry["error"] = {"error_type": "rate_limited", "error_message": "429"}
                tasks.append(entry)
            return httpx.Response(200, json={"success": True, "data": {"tasks": tasks}})
        raise AssertionError(f"unexpected path {path}")

    def client(self) -> LLMServiceAsyncEmbeddingGenerator:
        return LLMServiceAsyncEmbeddingGenerator(
            base_url="http://fake",
            poll_interval=0.01,
            wait_timeout=1.0,
            transport=httpx.MockTransport(self.handler),
        )


def test_embed_batch_splits_into_batches_of_32_and_preserves_order():
    fake = _FakeLlm()
    gen = fake.client()
    texts = [f"unit-{i}" for i in range(70)]  # 3 batches: 32 + 32 + 6
    out = gen.embed_batch(texts)
    assert len(out) == 70
    assert all(len(v) == 2 for v in out)
    embed_submits = [s for s in fake.submits if s["path"] == "/api/v1/tasks/embed"]
    assert [len(s["body"]["input"]) for s in embed_submits] == [32, 32, 6]
    # 顺序保持：批内 index 顺序展开（task-N 均为 6 字符 → 首元素恒 6.0）
    assert out[0] == [6.0, 1.0]
    assert out[69] == [6.0, 6.0]  # 第 70 条 = 批 3 的第 6 条


def test_embed_batch_dedupes_identical_content_batches():
    fake = _FakeLlm()
    gen = fake.client()
    # 两批内容完全相同（文档共享快照场景）→ 只提交一个任务
    out = gen.embed_batch(["a", "b", "a", "b"], batch_size=2)
    embed_submits = [s for s in fake.submits if s["path"] == "/api/v1/tasks/embed"]
    assert len(embed_submits) == 1
    assert embed_submits[0]["body"]["idempotency_key"].startswith("emb:")
    # 回填仍按输入条数展开（两个 batch_key 指向同一任务）
    assert len(out) == 4


@pytest.mark.parametrize("status", ["failed", "dead_letter", "timeout"])
def test_embed_batch_raises_on_failed_task(status):
    fake = _FakeLlm(final_status=status)
    gen = fake.client()
    with pytest.raises(RuntimeError, match="embedding task task-1"):
        gen.embed_batch(["x"])


def test_embed_batch_raises_on_count_mismatch():
    fake = _FakeLlm()
    # 提交 2 条文本，provider 只回 1 条向量 → 显式失败（与门面硬校验双保险）
    fake._results["task-1"] = {
        "model": "fake",
        "data": [{"index": 0, "embedding": [0.1]}],
    }
    with pytest.raises(RuntimeError, match="1 vectors"):
        fake.client().embed_batch(["x", "y"])


def test_embed_batch_raises_on_malformed_parsed_output():
    fake = _FakeLlm()
    fake._results["task-1"] = {"model": "fake", "data": "not-a-list"}
    with pytest.raises(RuntimeError, match="no data items"):
        fake.client().embed_batch(["x"])


def test_embed_batch_raises_on_empty_vector():
    fake = _FakeLlm()
    fake._results["task-1"] = {
        "model": "fake",
        "data": [{"index": 0, "embedding": []}],
    }
    with pytest.raises(RuntimeError, match="empty vector"):
        fake.client().embed_batch(["x"])


def test_embed_maps_parsed_output_sorted_by_index():
    fake = _FakeLlm()
    fake._results["task-1"] = {
        "model": "fake",
        "data": [
            {"index": 1, "embedding": [9.0, 9.0]},
            {"index": 0, "embedding": [1.0, 1.0]},
        ],
    }
    out = fake.client().embed_batch(["first", "second"])
    assert out[0] == [1.0, 1.0]
    assert out[1] == [9.0, 9.0]


def test_embed_single_call_submits_all_texts():
    fake = _FakeLlm()
    gen = fake.client()
    out = gen.embed(["a", "b"])
    assert len(out) == 2
    embed_submits = [s for s in fake.submits if s["path"] == "/api/v1/tasks/embed"]
    assert len(embed_submits) == 1
    assert embed_submits[0]["body"]["input"] == ["a", "b"]
