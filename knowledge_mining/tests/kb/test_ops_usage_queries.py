"""QueryLogStats 兼容适配器在统一记录表上的真 SQL 语义。

路由装配与鉴权在 tests/test_ops_usage_route.py（假仓储，不需要库）；这里跑真 SQL。

⚠️ 需要 PostgreSQL（`_test` 结尾的可丢弃库）。

DDL 来自 databases/kb/schemas/015_knowledge_access_records.sql；这里验证旧页面的
响应适配仍从新账本读取，不再依赖已退役的 Java 日志表。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from knowledge_mining.mining.infra.query_log_db import QueryLogStats

pytestmark = pytest.mark.asyncio

DOMAIN = "cloud_core_network"

_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_access_records (
    id TEXT PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    domain TEXT NOT NULL,
    actor_user_id TEXT,
    actor_username TEXT,
    source TEXT NOT NULL,
    operation TEXT NOT NULL,
    tool_name TEXT,
    mcp_key_id TEXT,
    kb_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    query_text TEXT,
    paradigm_id TEXT,
    paradigm_version INTEGER,
    status TEXT NOT NULL,
    result_count INTEGER,
    duration_ms INTEGER,
    error_code TEXT,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb
);
"""


# async fixture 必须交给 pytest_asyncio 接管（仓库为 strict 模式，
# 无 asyncio_mode=auto 配置——普通 @pytest.fixture 会无人处理）。
@pytest_asyncio.fixture
async def qlog(async_pool):
    """建表 + 清空 + 交出仓储；用完把表删掉，不污染同库的其余用例。"""
    async with async_pool.connection() as conn:
        await conn.execute(_DDL)
        await conn.execute("TRUNCATE TABLE knowledge_access_records")
    try:
        yield QueryLogStats(async_pool)
    finally:
        async with async_pool.connection() as conn:
            await conn.execute("TRUNCATE TABLE knowledge_access_records")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=n)).isoformat()


async def _log(
    pool, *, query="q", domain=DOMAIN, channel="mcp", intent="lookup",
    has_result=True, duration_ms=100, at=None, paradigm_id=None, day_offset=0,
):
    stamp = at or f"{_days_ago(day_offset)}T03:00:00.000Z"
    status = "success" if has_result else "no_result"
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO knowledge_access_records
               (id, query_text, domain, source, operation, status,
                duration_ms, occurred_at, paradigm_id)
               VALUES (%s, %s, %s, %s, 'search', %s, %s, %s, %s)""",
            (uuid.uuid4().hex, query, domain, channel, status,
             duration_ms, stamp, paradigm_id),
        )


# ── is_available ────────────────────────────────────────────────────────────

async def test_available_true_when_table_exists(qlog):
    assert await qlog.is_available() is True


async def test_available_false_when_table_missing(async_pool):
    """统一记录表尚未迁移时端点降级而不是 500。"""

    class _Rows:
        async def fetchone(self):
            return {"t": None}

    class _Connection:
        async def execute(self, *_args, **_kwargs):
            return _Rows()

    class _Context:
        async def __aenter__(self):
            return _Connection()

        async def __aexit__(self, *_args):
            return False

    class _Pool:
        def connection(self):
            return _Context()

    assert await QueryLogStats(_Pool()).is_available() is False


# ── summary ─────────────────────────────────────────────────────────────────

async def test_summary_counts_and_no_result_rate(qlog, async_pool):
    for _ in range(3):
        await _log(async_pool, has_result=True)
    await _log(async_pool, has_result=False)

    s = await qlog.summary(domain=DOMAIN, days=7)
    assert s["queries"] == 4
    assert s["no_result"] == 1
    assert s["no_result_rate"] == 0.25


async def test_summary_empty_window_gives_zero_not_none(qlog):
    """没有流量时每个字段都要是 0——前端直接拿去渲染百分比，None 会印出 NaN。"""
    s = await qlog.summary(domain=DOMAIN, days=7)
    assert s == {
        "queries": 0, "no_result": 0, "no_result_rate": 0.0,
        "p95_duration_ms": 0.0, "avg_duration_ms": 0.0, "active_paradigms": 0,
    }


async def test_summary_uses_p95_not_average(qlog, async_pool):
    """一堆快查询 + 一小撮慢的：平均值被稀释，P95 必须把尾巴顶起来。

    ⚠️ 慢请求占比必须**大于 5%**，否则这条用例证明不了任何事：percentile_cont 是
    插值型有序集聚合，慢的恰好占 5% 时 P95 正好落在快慢交界上被插值抹平。
    18+2 的分布（10% 慢）算下来 avg=509ms 而 P95=5000ms，差出一个数量级——
    换成 19+1（5% 慢）两个数都是 259.5ms，断言会失败。
    """
    for _ in range(18):
        await _log(async_pool, duration_ms=10)
    for _ in range(2):
        await _log(async_pool, duration_ms=5000)

    s = await qlog.summary(domain=DOMAIN, days=7)
    assert s["avg_duration_ms"] < 1000         # 平均被拉平，看着"还行"
    assert s["p95_duration_ms"] >= 4000        # P95 暴露那条尾巴


async def test_summary_respects_the_window(qlog, async_pool):
    await _log(async_pool, day_offset=0)
    await _log(async_pool, day_offset=30)      # 窗口外

    assert (await qlog.summary(domain=DOMAIN, days=7))["queries"] == 1


async def test_summary_is_scoped_by_domain(qlog, async_pool):
    await _log(async_pool, domain=DOMAIN)
    await _log(async_pool, domain="other_domain")

    assert (await qlog.summary(domain=DOMAIN, days=7))["queries"] == 1


async def test_active_paradigms_counts_distinct_ids(qlog, async_pool):
    await _log(async_pool, paradigm_id="p-1")
    await _log(async_pool, paradigm_id="p-1")
    await _log(async_pool, paradigm_id="p-2")
    await _log(async_pool, paradigm_id=None)   # legacy 引擎不算范式

    assert (await qlog.summary(domain=DOMAIN, days=7))["active_paradigms"] == 2


# ── no_result_queries ───────────────────────────────────────────────────────

async def test_no_result_queries_group_and_rank(qlog, async_pool):
    """同一个问题问了 12 次 ≠ 12 个不同问题——必须按原文聚合再排序。"""
    for _ in range(3):
        await _log(async_pool, query="SMF 超时", has_result=False)
    await _log(async_pool, query="计费字段", has_result=False)
    await _log(async_pool, query="有答案的", has_result=True)

    rows = await qlog.no_result_queries(domain=DOMAIN, days=7)
    assert [r["query_text"] for r in rows] == ["SMF 超时", "计费字段"]
    assert rows[0]["count"] == 3
    assert all(r["query_text"] != "有答案的" for r in rows)


async def test_no_result_queries_respects_limit(qlog, async_pool):
    for i in range(8):
        await _log(async_pool, query=f"q{i}", has_result=False)

    assert len(await qlog.no_result_queries(domain=DOMAIN, days=7, limit=3)) == 3


async def test_no_result_queries_empty_when_everything_answered(qlog, async_pool):
    await _log(async_pool, has_result=True)
    assert await qlog.no_result_queries(domain=DOMAIN, days=7) == []


# ── top_queries ─────────────────────────────────────────────────────────────

async def test_top_queries_carry_their_no_result_count(qlog, async_pool):
    """「问得多又答不上」是最高优先级——所以热门榜必须带各自的零结果数。"""
    for _ in range(5):
        await _log(async_pool, query="热门", has_result=False)
    await _log(async_pool, query="热门", has_result=True)
    await _log(async_pool, query="冷门", has_result=True)

    rows = await qlog.top_queries(domain=DOMAIN, days=7)
    assert rows[0] == {"query_text": "热门", "count": 6, "no_result": 5}


# ── paradigm_usage ──────────────────────────────────────────────────────────

async def test_paradigm_usage_reads_id_out_of_metadata_json(qlog, async_pool):
    """范式 id 从正式列聚合。"""
    await _log(async_pool, paradigm_id="p-1")
    await _log(async_pool, paradigm_id="p-1")
    await _log(async_pool, paradigm_id="p-2")

    rows = await qlog.paradigm_usage(domain=DOMAIN, days=7)
    by_id = {r["paradigm_id"]: r for r in rows}
    assert by_id["p-1"]["calls"] == 2
    assert by_id["p-2"]["calls"] == 1


async def test_paradigm_usage_buckets_legacy_traffic(qlog, async_pool):
    """没有 paradigm_id 的是 legacy SearchService 流量。归到 __legacy__ 而不是丢掉——
    「还有多少流量没走范式」本身就是管理员要的信息。"""
    await _log(async_pool, paradigm_id=None)
    await _log(async_pool, paradigm_id="p-1")

    by_id = {r["paradigm_id"]: r for r in await qlog.paradigm_usage(domain=DOMAIN, days=7)}
    assert by_id["(none)"]["calls"] == 1
    assert by_id["p-1"]["calls"] == 1


async def test_paradigm_usage_sorted_by_calls_desc(qlog, async_pool):
    await _log(async_pool, paradigm_id="small")
    for _ in range(3):
        await _log(async_pool, paradigm_id="big")

    rows = await qlog.paradigm_usage(domain=DOMAIN, days=7)
    assert rows[0]["paradigm_id"] == "big"


async def test_paradigm_usage_survives_missing_paradigm(qlog, async_pool):
    async with async_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO knowledge_access_records
               (id, query_text, domain, source, operation, status, duration_ms,
                occurred_at, paradigm_id)
               VALUES (%s, 'q', %s, 'mcp', 'search', 'success', 10, %s, NULL)""",
            (uuid.uuid4().hex, DOMAIN, f"{_today()}T03:00:00.000Z"),
        )

    rows = await qlog.paradigm_usage(domain=DOMAIN, days=7)
    assert {r["paradigm_id"] for r in rows} == {"(none)"}


# ── trend ───────────────────────────────────────────────────────────────────

async def test_trend_fills_empty_days(qlog):
    """折线跳过没有流量的日期，会把「停了一周」画成连续使用。"""
    trend = await qlog.trend(domain=DOMAIN, days=7)

    assert len(trend) == 7
    assert all(p["queries"] == 0 for p in trend)
    assert [p["date"] for p in trend] == sorted(p["date"] for p in trend)
    assert trend[-1]["date"] == _today()


async def test_trend_buckets_by_day(qlog, async_pool):
    await _log(async_pool, day_offset=0)
    await _log(async_pool, day_offset=0, has_result=False)
    await _log(async_pool, day_offset=2)

    trend = {p["date"]: p for p in await qlog.trend(domain=DOMAIN, days=7)}
    assert trend[_today()]["queries"] == 2
    assert trend[_today()]["no_result"] == 1
    assert trend[_days_ago(2)]["queries"] == 1


# ── breakdown ───────────────────────────────────────────────────────────────

async def test_breakdown_by_intent_and_channel(qlog, async_pool):
    await _log(async_pool, intent="lookup", channel="mcp")
    await _log(async_pool, intent="lookup", channel="api")
    await _log(async_pool, intent="howto", channel="mcp")

    assert await qlog.breakdown(domain=DOMAIN, days=7, column="intent") == {}
    assert await qlog.breakdown(domain=DOMAIN, days=7, column="channel") == {
        "mcp": 2, "api": 1,
    }


async def test_breakdown_labels_null_intent(qlog, async_pool):
    """旧 intent 维度退役后稳定返回空对象。"""
    await _log(async_pool, intent=None)

    assert await qlog.breakdown(domain=DOMAIN, days=7, column="intent") == {}


async def test_breakdown_rejects_arbitrary_column(qlog):
    """column 会被拼进 SQL —— 白名单之外一律拒，不给注入留口子。"""
    with pytest.raises(ValueError):
        await qlog.breakdown(domain=DOMAIN, days=7, column="query_text; DROP TABLE x")
