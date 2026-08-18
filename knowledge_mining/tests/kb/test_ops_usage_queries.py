"""QueryLogStats 的 SQL 语义（`serving_query_logs` 只读聚合）。

路由装配与鉴权在 tests/test_ops_usage_route.py（假仓储，不需要库）；这里跑真 SQL。

⚠️ 需要 PostgreSQL（`_test` 结尾的可丢弃库）。

**本文件顺带是一份契约声明**：`serving_query_logs` 由 serving（Java）建表，mining 的
测试 schema 不会创建它，所以这里按 serving 的 DDL 自建。这意味着 serving 单方面改列
时本文件**不会变红**——它钉住的是「mining 依赖哪些列、依赖它们是什么形状」，不是
「serving 现在是什么形状」。真正的漂移只能靠联调发现，这一点是这套跨服务读取的固有
代价，写在这里以免后人误以为有自动防线。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from knowledge_mining.mining.infra.query_log_db import QueryLogStats

pytestmark = pytest.mark.asyncio

DOMAIN = "cloud_core_network"

# 按 serving 的 db/serving/001_serving_query_logs.sql 抄写，只保留 NOT NULL 与本模块
# 用到的列（metadata_json 是 TEXT —— 取 paradigm_id 必须转型，这正是要钉的点之一）。
_DDL = """
CREATE TABLE IF NOT EXISTS serving_query_logs (
    id                  TEXT    NOT NULL,
    query_text          TEXT    NOT NULL,
    domain              TEXT    NOT NULL DEFAULT 'default',
    channel             TEXT    NOT NULL,
    intent              TEXT,
    normalizer_source   TEXT,
    keywords_json       TEXT    NOT NULL DEFAULT '[]',
    entities_json       TEXT    NOT NULL DEFAULT '[]',
    scope_json          TEXT    NOT NULL DEFAULT '{}',
    release_id          TEXT,
    build_id            TEXT,
    snapshot_count      INTEGER,
    result_item_count   INTEGER,
    result_seed_count   INTEGER,
    result_has_result   BOOLEAN NOT NULL DEFAULT TRUE,
    result_issues_json  TEXT    NOT NULL DEFAULT '[]',
    result_items_json   TEXT    NOT NULL DEFAULT '[]',
    result_sources_json TEXT    NOT NULL DEFAULT '[]',
    result_relations_json TEXT  NOT NULL DEFAULT '[]',
    duration_ms         INTEGER,
    queried_at          TEXT    NOT NULL,
    metadata_json       TEXT    NOT NULL DEFAULT '{}',
    CONSTRAINT pk_serving_query_logs PRIMARY KEY (id)
);
"""


@pytest.fixture
async def qlog(async_pool):
    """建表 + 清空 + 交出仓储；用完把表删掉，不污染同库的其余用例。"""
    async with async_pool.connection() as conn:
        await conn.execute(_DDL)
        await conn.execute("TRUNCATE TABLE serving_query_logs")
    try:
        yield QueryLogStats(async_pool)
    finally:
        async with async_pool.connection() as conn:
            await conn.execute("DROP TABLE IF EXISTS serving_query_logs")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=n)).isoformat()


async def _log(
    pool, *, query="q", domain=DOMAIN, channel="mcp", intent="lookup",
    has_result=True, duration_ms=100, at=None, paradigm_id=None, day_offset=0,
):
    stamp = at or f"{_days_ago(day_offset)}T03:00:00.000Z"
    metadata = "{}"
    if paradigm_id is not None:
        metadata = f'{{"engine":"paradigm","paradigm_id":"{paradigm_id}"}}'
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO serving_query_logs
               (id, query_text, domain, channel, intent, result_has_result,
                duration_ms, queried_at, metadata_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (uuid.uuid4().hex, query, domain, channel, intent, has_result,
             duration_ms, stamp, metadata),
        )


# ── is_available ────────────────────────────────────────────────────────────

async def test_available_true_when_table_exists(qlog):
    assert await qlog.is_available() is True


async def test_available_false_when_table_missing(async_pool):
    """serving 从没启动过的部署——表根本不存在，端点要据此降级而不是 500。"""
    async with async_pool.connection() as conn:
        await conn.execute("DROP TABLE IF EXISTS serving_query_logs")
    assert await QueryLogStats(async_pool).is_available() is False


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
    """一堆快查询 + 一条慢的：平均值会被稀释，P95 必须把尾巴顶起来。"""
    for _ in range(19):
        await _log(async_pool, duration_ms=10)
    await _log(async_pool, duration_ms=5000)

    s = await qlog.summary(domain=DOMAIN, days=7)
    assert s["avg_duration_ms"] < 500          # 平均被拉平
    assert s["p95_duration_ms"] > 1000         # P95 看得见那条尾巴


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
    """metadata_json 是 TEXT 不是 JSONB——转型写错这里就会整段报错。"""
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
    assert by_id["__legacy__"]["calls"] == 1
    assert by_id["p-1"]["calls"] == 1


async def test_paradigm_usage_sorted_by_calls_desc(qlog, async_pool):
    await _log(async_pool, paradigm_id="small")
    for _ in range(3):
        await _log(async_pool, paradigm_id="big")

    rows = await qlog.paradigm_usage(domain=DOMAIN, days=7)
    assert rows[0]["paradigm_id"] == "big"


async def test_paradigm_usage_survives_empty_metadata(qlog, async_pool):
    """metadata_json 的列默认值是 '{}'，但空串会让 ::jsonb 抛——抛一次整段就废了。"""
    async with async_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO serving_query_logs
               (id, query_text, domain, channel, result_has_result, duration_ms,
                queried_at, metadata_json)
               VALUES (%s, 'q', %s, 'mcp', TRUE, 10, %s, '')""",
            (uuid.uuid4().hex, DOMAIN, f"{_today()}T03:00:00.000Z"),
        )

    rows = await qlog.paradigm_usage(domain=DOMAIN, days=7)
    assert {r["paradigm_id"] for r in rows} == {"__legacy__"}


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

    assert await qlog.breakdown(domain=DOMAIN, days=7, column="intent") == {
        "lookup": 2, "howto": 1,
    }
    assert await qlog.breakdown(domain=DOMAIN, days=7, column="channel") == {
        "mcp": 2, "api": 1,
    }


async def test_breakdown_labels_null_intent(qlog, async_pool):
    """intent 可空。NULL 键在 JSON 里会变成 null，前端拿它当 key 会炸。"""
    await _log(async_pool, intent=None)

    assert await qlog.breakdown(domain=DOMAIN, days=7, column="intent") == {"(未知)": 1}


async def test_breakdown_rejects_arbitrary_column(qlog):
    """column 会被拼进 SQL —— 白名单之外一律拒，不给注入留口子。"""
    with pytest.raises(ValueError):
        await qlog.breakdown(domain=DOMAIN, days=7, column="query_text; DROP TABLE x")
