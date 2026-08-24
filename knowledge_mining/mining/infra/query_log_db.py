"""检索使用分析 —— `serving_query_logs` 上的只读聚合。

给概览页与设置页的运维区块供数：检索量 / 零结果 / 范式调用 / 延迟 / 意图与渠道分布。

⚠️ **这张表不归 mining 所有**。它由 serving（Java）的 ServingRuntimeSchemaInitializer
建表、QueryLogAspect 写入，列契约靠 serving 的 ServingQueryLogMapper.xml 维持
（那份 DDL 的注释原话：「除此之外没有任何东西校验这个契约」）。这里多一个跨语言读者
就多一份漂移风险，所以刻意约束自己：

- **只读**，不建表不改表；
- 只碰少数几列，且全部走 serving 已建好的索引
  （queried_at / (intent,queried_at) / (result_has_result,queried_at)）；
- 表不存在时不报错，而是回 available=False —— serving 从没启动过的部署里这张表
  根本不存在，那不是故障。

两处与直觉不同、但都是被数据形态逼出来的：

1. `queried_at` 是 TEXT，写入侧是 `Instant.now().toString()`（ISO-8601 UTC），
   所以前 10 位即日期、字典序即时序。和日期字符串比较时 `'2026-08-11'` 排在该日
   任何时刻之前，故 `>= since_date` 天然含当天整天。
2. `metadata_json` 是 **TEXT 不是 JSONB**（serving 的 DDL 与迁移里都是 TEXT，
   Java 侧那句「metadata_json is JSONB」的注释是过时的）。要取 paradigm_id 必须
   显式转型；写成 `::jsonb` 而不是假定列型，两种列型下都成立。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# 范式归属藏在 metadata_json 里（QueryLogAspect.recordParadigm 写的），不是独立列。
# NULLIF 兜空串：TEXT 列的默认值是 '{}'，但空串转 jsonb 会抛，而抛一次就是整个聚合失败。
_PARADIGM_ID_SQL = "(NULLIF(metadata_json, '')::jsonb ->> 'paradigm_id')"

# 单条查询原文的展示上限。这些原文会直接呈现给管理员，过长的会把表格撑破。
_QUERY_TEXT_MAX = 200


def _utc_today() -> Any:
    return datetime.now(timezone.utc).date()


def _since(days: int) -> str:
    """窗口起始日（含当天）。返回 YYYY-MM-DD，直接与 queried_at 文本比较。"""
    return (_utc_today() - timedelta(days=days - 1)).isoformat()


class QueryLogStats:
    """`serving_query_logs` 的只读聚合仓储。

    构造时传 **域池**（domain_pools.async_pool(domain)）——查询日志跟着域走，
    与 /api/runs 同一套取池方式。
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def is_available(self) -> bool:
        """表在不在。serving 从没起过的部署里它不存在，那是合法状态不是故障。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute("SELECT to_regclass('serving_query_logs') AS t")
            row = await cur.fetchone()
            return bool(row and row["t"])

    async def summary(self, *, domain: str, days: int) -> dict[str, Any]:
        """窗口内的四个关键数 + 活跃范式数。

        延迟给 **P95 而不是平均值**：平均值会被一堆快查询稀释，掩掉真正卡住人的那条尾巴。
        percentile_cont 是有序集聚合，自动忽略 NULL 的 duration_ms，不必额外 FILTER。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT
                      COUNT(*) AS queries,
                      COUNT(*) FILTER (WHERE NOT result_has_result) AS no_result,
                      COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms), 0)
                        AS p95_duration_ms,
                      COALESCE(AVG(duration_ms), 0) AS avg_duration_ms,
                      COUNT(DISTINCT {_PARADIGM_ID_SQL}) AS active_paradigms
                    FROM serving_query_logs
                    WHERE domain = %(dom)s AND queried_at >= %(since)s""",
                {"dom": domain, "since": _since(days)},
            )
            row = await cur.fetchone() or {}
            queries = int(row.get("queries") or 0)
            no_result = int(row.get("no_result") or 0)
            return {
                "queries": queries,
                "no_result": no_result,
                # 分母为 0 时给 0 而不是 None —— 前端要拿它直接渲染百分比
                "no_result_rate": round(no_result / queries, 4) if queries else 0.0,
                "p95_duration_ms": round(float(row.get("p95_duration_ms") or 0), 1),
                "avg_duration_ms": round(float(row.get("avg_duration_ms") or 0), 1),
                "active_paradigms": int(row.get("active_paradigms") or 0),
            }

    async def no_result_queries(
        self, *, domain: str, days: int, limit: int = 10,
    ) -> list[dict[str, Any]]:
        """答不上来的问题，按出现次数排。

        **这是整个端点最有用的一段**：零结果率只告诉管理员「有缺口」，原文告诉他
        「缺口在哪、该补什么」。按 query_text 分组而不是逐条列——同一个问题问了 12 次
        和 12 个不同问题，是完全不同的两件事。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT LEFT(query_text, {_QUERY_TEXT_MAX}) AS query_text,
                           COUNT(*) AS count,
                           MAX(queried_at) AS last_at
                    FROM serving_query_logs
                    WHERE domain = %(dom)s AND queried_at >= %(since)s
                      AND NOT result_has_result
                    GROUP BY 1
                    ORDER BY count DESC, last_at DESC
                    LIMIT %(lim)s""",
                {"dom": domain, "since": _since(days), "lim": limit},
            )
            return [
                {"query_text": r["query_text"], "count": int(r["count"]),
                 "last_at": r["last_at"]}
                for r in await cur.fetchall()
            ]

    async def top_queries(
        self, *, domain: str, days: int, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """热门查询。带上各自的零结果数——「问得多又答不上」的那几条优先级最高。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT LEFT(query_text, {_QUERY_TEXT_MAX}) AS query_text,
                           COUNT(*) AS count,
                           COUNT(*) FILTER (WHERE NOT result_has_result) AS no_result
                    FROM serving_query_logs
                    WHERE domain = %(dom)s AND queried_at >= %(since)s
                    GROUP BY 1
                    ORDER BY count DESC
                    LIMIT %(lim)s""",
                {"dom": domain, "since": _since(days), "lim": limit},
            )
            return [
                {"query_text": r["query_text"], "count": int(r["count"]),
                 "no_result": int(r["no_result"])}
                for r in await cur.fetchall()
            ]

    async def paradigm_usage(self, *, domain: str, days: int) -> list[dict[str, Any]]:
        """各检索范式的调用量 / 零结果率 / P95。

        `paradigm_id IS NULL` 的行是走 legacy SearchService 的流量（非范式引擎），
        归到 `__legacy__` 而不是丢掉——「有多少流量还没走范式」本身就是管理员要的信息。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT COALESCE({_PARADIGM_ID_SQL}, '__legacy__') AS paradigm_id,
                           COUNT(*) AS calls,
                           COUNT(*) FILTER (WHERE NOT result_has_result) AS no_result,
                           COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms), 0)
                             AS p95_duration_ms
                    FROM serving_query_logs
                    WHERE domain = %(dom)s AND queried_at >= %(since)s
                    GROUP BY 1
                    ORDER BY calls DESC""",
                {"dom": domain, "since": _since(days)},
            )
            return [
                {
                    "paradigm_id": r["paradigm_id"],
                    "calls": int(r["calls"]),
                    "no_result": int(r["no_result"]),
                    "p95_duration_ms": round(float(r["p95_duration_ms"] or 0), 1),
                }
                for r in await cur.fetchall()
            ]

    async def trend(self, *, domain: str, days: int) -> list[dict[str, Any]]:
        """每日检索量与零结果数。**空天补零**——折线跳过没有流量的日期，会把
        「停了一周」画成连续使用。与挖掘趋势同一处理。"""
        today = _utc_today()
        buckets = {
            (today - timedelta(days=i)).isoformat(): {"queries": 0, "no_result": 0}
            for i in range(days)
        }
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT substring(queried_at from 1 for 10) AS day,
                          COUNT(*) AS queries,
                          COUNT(*) FILTER (WHERE NOT result_has_result) AS no_result
                   FROM serving_query_logs
                   WHERE domain = %(dom)s AND queried_at >= %(since)s
                   GROUP BY 1""",
                {"dom": domain, "since": _since(days)},
            )
            for r in await cur.fetchall():
                bucket = buckets.get(r["day"])
                if bucket is not None:
                    bucket["queries"] = int(r["queries"])
                    bucket["no_result"] = int(r["no_result"])
        return [{"date": d, **buckets[d]} for d in sorted(buckets)]

    async def breakdown(self, *, domain: str, days: int, column: str) -> dict[str, int]:
        """按 intent / channel 分布。

        column 只允许白名单里的两个值 —— 它被拼进 SQL，不能来自请求参数。
        """
        if column not in ("intent", "channel"):
            raise ValueError(f"unsupported breakdown column: {column}")
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT COALESCE({column}, '(未知)') AS k, COUNT(*) AS c
                    FROM serving_query_logs
                    WHERE domain = %(dom)s AND queried_at >= %(since)s
                    GROUP BY 1
                    ORDER BY c DESC""",
                {"dom": domain, "since": _since(days)},
            )
            return {r["k"]: int(r["c"]) for r in await cur.fetchall()}
