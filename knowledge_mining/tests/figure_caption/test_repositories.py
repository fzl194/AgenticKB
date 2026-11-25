"""B2 存储层单元测试（fake 连接，无 PG 依赖）。

PG 真库行为由既有 PG 测试基建覆盖（需要数据库环境变量时跳过）。
"""
import json

import pytest

from knowledge_mining.mining.contracts.segment_compiler import (
    CompiledSegment,
    SegmentElementLink,
)
from knowledge_mining.mining.figure_caption.repositories_pg import (
    PgFigureCaptionCache,
)
from knowledge_mining.mining.segment_compiler.repositories_pg import PgSegmentStore

pytestmark = pytest.mark.asyncio


class _FakeCursor:
    def __init__(self, rows: list | None = None) -> None:
        self._rows = rows or []
        self.executed: list[tuple[str, list]] = []

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, existing_keys: set[tuple[str, str]] | None = None) -> None:
        self.existing = existing_keys or set()
        self.statements: list[tuple[str, list]] = []

    async def execute(self, sql: str, params: list | None = None):
        self.statements.append((sql, params or []))
        if "SELECT 1 FROM asset_raw_segments" in sql:
            snap, key = params[0], params[1]
            return _FakeCursor([{"?column?": 1}] if (snap, key) in self.existing else [])
        return _FakeCursor()

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def connection(self):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _figure_segment(order: int = 0) -> CompiledSegment:
    return CompiledSegment(
        segment_index=1_000_000 + order,
        block_type="figure",
        raw_text="VLM 图注文本",
        heading_chain=((1, "系统架构"),),
        element_ids=("fig1",),
        links=(SegmentElementLink(element_id="fig1"),),
        metadata={"caption_source": "vlm", "figure_element_id": "fig1"},
    )


async def test_append_inserts_new_and_skips_existing():
    conn = _FakeConn(existing_keys={("snap-1", "doc-1#1000000")})
    store = PgSegmentStore(_FakePool(conn))
    count = await store.append_figures_for_snapshot(
        "snap-1", "doc-1", (_figure_segment(0), _figure_segment(1))
    )
    assert count == 1  # 第一条已存在跳过，第二条新插入
    inserts = [s for s in conn.statements if "INSERT INTO asset_raw_segments" in s[0]]
    assert len(inserts) == 1
    params = inserts[0][1]
    assert params[2] == "doc-1#1000001"  # segment_key 与 replace 同格式
    assert params[3] == 1_000_001
    assert params[-1] is None  # compiler_fingerprint 置 NULL（不参与幂等短路）
    structure = json.loads(params[13])
    assert structure["source_block_type"] == "figure"
    assert structure["caption_source"] == "vlm"
    assert params[4] == "image"  # figure 投影为 legacy 白名单 image


async def test_cache_put_upserts_and_get_reads():
    conn = _FakeConn()
    cache = PgFigureCaptionCache(_FakePool(conn))
    await cache.put(
        "k1",
        "图注",
        model="glm-4.5v",
        domain="default",
        source_raw_hash="h1",
        parser_fingerprint="fp1",
        figure_element_id="fig1",
        prompt_version="vlm-figure-caption-1",
    )
    insert = [s for s in conn.statements if "INSERT INTO asset_figure_caption_cache" in s[0]]
    assert insert and "ON CONFLICT (cache_key) DO UPDATE" in insert[0][0]
    assert insert[0][1][0] == "k1"

    conn2 = _FakeConn()
    conn2.statements.clear()
    cache2 = PgFigureCaptionCache(_FakePool(conn2))
    # get 走 SELECT；命中行为由真实 PG 测试覆盖，这里验证语句形状
    await cache2.get("k1")
    assert any(
        "SELECT caption FROM asset_figure_caption_cache" in s[0]
        for s in conn2.statements
    )
