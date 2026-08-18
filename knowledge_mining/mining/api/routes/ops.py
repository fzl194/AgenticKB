"""运维使用分析 —— GET /api/ops/usage。

概览页的运维区块（摘要）与 设置→系统状态（明细）共用这一个端点。回答的是**使用**
问题而不是基础设施问题：有没有人在用、用户问了什么、系统答不上来哪些、哪个检索范式
在真正承载流量。

**admin-only（require_admin，现查库）**。两个理由，缺一不可：
- 响应里含 `no_result_queries` / `top_queries` 的**用户输入原文**。那是别人打进检索框的
  东西，不该对普通成员敞开。
- 服务级运维数据对无权处理的人只是噪声。

数据来自 serving 拥有的 `serving_query_logs`（跨服务只读，取舍见 query_log_db 模块
docstring）。表不存在时回 available=False 而不是 500 —— serving 从没启动过的部署里
它本来就不存在。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from knowledge_mining.mining.api.domain_scope import require_domain
from knowledge_mining.mining.infra.query_log_db import QueryLogStats
from knowledge_mining.mining.kb.auth import require_admin

router = APIRouter(prefix="/api/ops", tags=["ops"])

# 摘要与明细列表的窗口（天）。7 天：短到能反映"最近怎么样"，长到能盖住一个工作周的
# 周末低谷——3 天的窗口每逢周一都会显示成断崖。
DEFAULT_WINDOW_DAYS = 7

# 趋势折线固定 30 天，与挖掘趋势对齐，不随 days 变：两条折线并排看时窗口必须一致。
TREND_DAYS = 30

NO_RESULT_LIMIT = 10
TOP_QUERY_LIMIT = 20


def _empty_payload(days: int) -> dict[str, Any]:
    """serving 没产出过日志时的空壳。

    保持与正常响应**完全相同的形状**，让前端只判 available、不必对每个字段判
    undefined —— 少一个分支就少一处 "reading 'queries' of undefined"。
    """
    return {
        "available": False,
        "days": days,
        "trend_days": TREND_DAYS,
        "summary": {
            "queries": 0, "no_result": 0, "no_result_rate": 0.0,
            "p95_duration_ms": 0.0, "avg_duration_ms": 0.0, "active_paradigms": 0,
        },
        "no_result_queries": [],
        "top_queries": [],
        "paradigms": [],
        "trend": [],
        "intents": {},
        "channels": {},
    }


@router.get("/usage")
async def usage_stats(
    request: Request,
    domain: str = Query(..., min_length=1),
    days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=90),
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """检索使用分析。口径 = 该域全部检索流量（不按 KB 收敛——这是域级运维视角）。

    与 /api/kb/stats 的可见集收敛不同：那个是「我的知识库」，这个是「这个域被怎么用」。
    admin 本来就能看全域，再按 KB 收一遍既无意义又会让「有多少流量没走范式」失真。
    """
    resolved = require_domain(domain)
    # 查询日志跟着域走，用域池而不是默认池（与 /api/runs 同一套取池方式）
    pool = await request.app.state.domain_pools.async_pool(resolved)
    stats = QueryLogStats(pool)

    if not await stats.is_available():
        return _empty_payload(days)

    return {
        "available": True,
        "days": days,
        "trend_days": TREND_DAYS,
        "summary": await stats.summary(domain=resolved, days=days),
        "no_result_queries": await stats.no_result_queries(
            domain=resolved, days=days, limit=NO_RESULT_LIMIT,
        ),
        "top_queries": await stats.top_queries(
            domain=resolved, days=days, limit=TOP_QUERY_LIMIT,
        ),
        "paradigms": await stats.paradigm_usage(domain=resolved, days=days),
        "trend": await stats.trend(domain=resolved, days=TREND_DAYS),
        "intents": await stats.breakdown(domain=resolved, days=days, column="intent"),
        "channels": await stats.breakdown(domain=resolved, days=days, column="channel"),
    }
