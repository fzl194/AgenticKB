"""批次3-问题2：知识读路径分页限量 + IR 流式直解。

实测两处整包：get_document_knowledge 一次捞全部 segments/units/mentions
（无分页无上限）；ParseResultReadService._load_ir 内存里攒 chunks → join
→ json.loads → from_dict（3-4 份全量副本）。
"""
from __future__ import annotations

import json

import pytest


# ── 知识读路径分页 ─────────────────────────────────────────────────────────

class _FakeCur:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, sql, params):
        return _FakeCur(self._results.pop(0) if self._results else [])


class _FakePool:
    def __init__(self, results):
        self._conn = _FakeConn(results)

    def connection(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool._conn

            async def __aexit__(self, *a):
                return False
        return _Ctx()


def _make_kbdb(results):
    from knowledge_mining.mining.kb.db import KbDB
    db = object.__new__(KbDB)
    db._pool = _FakePool(results)
    return db


@pytest.mark.asyncio
async def test_get_document_knowledge_limits_segments():
    """知识查询限量：segments/units 默认封顶（防几千切片整包）。"""
    snapshot_row = [{"document_snapshot_id": "snap-1", "build_id": "b-1"}]
    segments = [{"segment_index": i, "raw_text": f"seg{i}"} for i in range(50)]
    units, mentions, relations = [], [], []
    db = _make_kbdb([snapshot_row, segments, units, mentions, relations])
    result = await db.get_document_knowledge("kb-1", "doc-1", max_rows=30)
    assert result["mined"] is True
    assert len(result["segments"]) == 30
    assert result.get("truncated") is True
    assert result.get("total_segments") == 50  # 告诉前端被截断了


@pytest.mark.asyncio
async def test_get_document_knowledge_default_limit_applied():
    """默认限量存在且量级合理（千级封顶，不是无界）。"""
    import inspect
    from knowledge_mining.mining.kb.db import KbDB
    sig = inspect.signature(KbDB.get_document_knowledge)
    default = sig.parameters["max_rows"].default
    assert 500 <= default <= 5000


# ── IR 流式直解 ───────────────────────────────────────────────────────────

class _FakeStore:
    """get_stream 吐原始 JSON 分块——模拟 MinIO 落盘分块回读。"""

    def __init__(self, payload: bytes, chunk: int = 256 * 1024):
        self._chunks = [payload[i:i + chunk] for i in range(0, len(payload), chunk)]
        self.stream_calls = 0

    async def get_stream(self, location):
        self.stream_calls += 1
        for c in self._chunks:
            yield c


class _FakeObjectRepo:
    async def get(self, object_id):
        return type("R", (), {"bucket": "parse", "object_key": "k",
                              "object_version_id": None})()


def _mk_svc(payload: bytes, with_store_ref: bool = False):
    from knowledge_mining.mining.snapshot_store.read_service import ParseResultReadService
    svc = ParseResultReadService.__new__(ParseResultReadService)
    store = _FakeStore(payload)
    svc._store = store
    svc._storage_objects = _FakeObjectRepo()
    return (svc, store) if with_store_ref else svc


@pytest.fixture(autouse=True)
def _clear_ir_cache():
    """类级 IR 缓存在测试间必须清空（否则用例互相污染）。"""
    from knowledge_mining.mining.snapshot_store.read_service import ParseResultReadService
    ParseResultReadService._ir_cache.clear()
    yield
    ParseResultReadService._ir_cache.clear()


@pytest.mark.asyncio
async def test_load_ir_single_memory_copy(monkeypatch):
    """流式直解：解压流式喂数据（不攒 chunks、不 join、不二次 dict）。"""
    import tracemalloc
    from knowledge_mining.mining.snapshot_store.read_service import ParseResultReadService

    # 用真实 to_dict 造标准负载（from_dict 字段完整），元素文本撑到 MB 级
    from knowledge_mining.mining.contracts.parse_ir.types import (
        ParsedDocument, ParseIdentity,
    )
    filler = "x" * 4096
    doc0 = ParsedDocument.from_dict({
        "schema_version": "0.1",
        "elements": [{"element_id": f"e{i}", "element_type": "paragraph",
                    "order_index": i, "text": filler} for i in range(2000)],
        "containers": [], "relations": [], "document_meta": {}, "assets": [],
        "source_identity": {"source_raw_hash": "a" * 64,
                            "parser_fingerprint": "p@1", "mime_type": "text/plain"},
    })
    payload = json.dumps(doc0.to_dict()).encode()

    svc = _mk_svc(payload)

    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    doc = await svc._load_ir("obj-1")
    peak = tracemalloc.get_traced_memory()[1] - before
    tracemalloc.stop()

    assert len(doc.elements) == 2000
    # 原实现峰值 ≈ 原始 bytes + join bytes + dict + 对象（4 份）；
    # 直解只留最终对象图（≈1 份）+ 解压窗口。8MB payload 峰值应 < 3.5x。
    assert peak < len(payload) * 3, (
        f"peak {peak / 1024 / 1024:.1f}MB vs payload {len(payload) / 1024 / 1024:.1f}MB")


@pytest.mark.asyncio
async def test_load_ir_caches_by_snapshot_object():
    """快照 IR 不可变——同一对象二次读取命中缓存，不再触对象存储。"""
    from knowledge_mining.mining.snapshot_store.read_service import ParseResultReadService
    from knowledge_mining.mining.contracts.parse_ir.types import ParsedDocument
    small = ParsedDocument.from_dict({
        "schema_version": "0.1",
        "elements": [{"element_id": "e1", "element_type": "paragraph",
                  "order_index": 0, "text": "hello"}],
        "containers": [], "relations": [], "document_meta": {}, "assets": [],
        "source_identity": {"source_raw_hash": "a" * 64,
                            "parser_fingerprint": "p@1", "mime_type": "text/plain"},
    })
    payload = json.dumps(small.to_dict()).encode()
    svc, store = _mk_svc(payload, with_store_ref=True)
    d1 = await svc._load_ir("obj-1")
    d2 = await svc._load_ir("obj-1")
    assert d1 is d2  # 缓存返回同一对象（不可变快照）
    assert store.stream_calls == 1  # 二次读取零触库
