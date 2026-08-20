"""概览页聚合端点 —— GET /api/kb/overview 与 GET /api/kb/stats。

- `/overview`：可见知识库全集 + 每库状态摘要 + 跨库最近挖掘 + 该域有无 active release。
  检索页也用它（要 has_active_release 才能决定范围选择器给不给「域级发布」）。
- `/stats`：概览页的数字与图表 —— 文档状态分布 / 知识资产量 / 检索单元类型 / 挖掘趋势。

**为什么拆成两个端点而不是一个**：`/overview` 是检索页的热路径，只需要 KB 列表；
`/stats` 要扫 asset_* 四张表和 30 天 run。合成一个会让检索页白付统计的钱。概览页
两个并发发起，一次往返的时间。

**为什么是聚合端点而不是几个小接口**：
- 一个授权点。拆成多个接口，只要有一个忘了按可见集收敛就是个越权口（/api/runs 就是
  这么漏的）。
- 各区块数据同源同时刻。分次请求会出现「卡片说挖掘中、下面的最近挖掘说已完成」。
- 一次往返。首页是登录后的落地页，延迟最敏感。

**⚠️ 路由注册顺序是承重的**：本 router 与 kb_router 同 prefix（/api/kb），而 kb_router
有吞噬型动态段 /{kb_id}。app.py 必须把本 router 注册在 kb_router **之前**，否则
GET /api/kb/overview 会被当成 kb_id="overview" → 404。同款事故见 62aebd1。
新增静态路径（如 /stats）加在本文件即可，注册顺序已经是对的。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.deps import get_kb_db

router = APIRouter(prefix="/api/kb", tags=["kb-overview"])

# 首页卡片只渲染前 6 张，但 kbs 不截断——检索范围要用全集（默认全选）。
RECENT_RUN_LIMIT = 5

# 挖掘趋势的窗口。30 天：短于一个月看不出「这个月比上个月挖得多」，长于一个月在
# 折线上一天就窄到 3px，读不出单日高低。
TREND_DAYS = 30

# 有写权限的有效角色。与 KbDB.can_write 的 SQL 条件（admin 全通 / owner / editor 成员）
# 逐项对应；这里从 list_visible 已返回的 my_role 推导，避免逐库再查一次。
_WRITABLE_ROLES = {"owner", "editor", "admin"}


def _sort_key(kb: dict[str, Any]) -> tuple:
    """最近挖掘时间 DESC NULLS LAST，其次创建时间 DESC。

    时间列在 schema 里是 TEXT（ISO-8601），字典序即时序——依赖写入侧统一用 _utcnow()。
    第一项把 NULL 压到最后：reverse=True 下 False < True，没挖过的库自然沉底。
    """
    last_mined = kb.get("last_mined_at")
    return (last_mined is not None, last_mined or "", kb.get("created_at") or "")


@router.get("/overview")
async def kb_overview(
    domain: str = Query(..., min_length=1),
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    """首页数据。可见集为空返回空数组而不是 404——「还没有知识库」是合法状态。"""
    visible = await kbdb.list_visible(user_id=user["id"], domain=domain)
    kb_ids = [kb["id"] for kb in visible]

    counts = await kbdb.overview_status_counts(kb_ids=kb_ids)
    rollup = await kbdb.overview_run_rollup(kb_ids=kb_ids)
    recent_runs = await kbdb.overview_recent_runs(kb_ids=kb_ids, limit=RECENT_RUN_LIMIT)
    has_release = await kbdb.has_active_release(domain=domain)

    kbs: list[dict[str, Any]] = []
    for kb in visible:
        kb_id = kb["id"]
        # 无文档 / 无 run 的库在聚合里没有行 —— 补零而不是缺键，让前端不必判 undefined。
        stat = counts.get(kb_id) or {"total": 0, "mining": 0, "failed": 0}
        runs = rollup.get(kb_id) or {}
        my_role = kb.get("my_role")
        kbs.append({
            "id": kb_id,
            "name": kb.get("name"),
            "my_role": my_role,
            "can_write": my_role in _WRITABLE_ROLES,
            "status_counts": {
                "total": stat["total"], "mining": stat["mining"], "failed": stat["failed"],
            },
            "last_mined_at": runs.get("last_mined_at"),
            "awaiting_review_run_id": runs.get("awaiting_review_run_id"),
            # 排序用，不属于对外契约的一部分，返回前删掉
            "created_at": kb.get("created_at"),
        })

    kbs.sort(key=_sort_key, reverse=True)
    for kb in kbs:
        kb.pop("created_at", None)

    return {
        "has_active_release": has_release,
        "kbs": kbs,
        "recent_runs": recent_runs,
    }


@router.get("/stats")
async def kb_stats(
    domain: str = Query(..., min_length=1),
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
) -> dict[str, Any]:
    """概览页的统计数字与图表数据。口径 = **当前用户在本域可见的全部知识库**。

    可见集为空时返回一份全零结构而不是 404 / 空对象：前端照样渲染出图表骨架和「0」，
    「一个知识库都没有」与「接口挂了」在页面上必须长得不一样。

    `has_active_release` 同时是 published/withdrawn 两档的**口径开关**：域里没有
    active release 时这两个数恒 0（KB 挖掘 publish=False 不产 release），前端据此把
    它们从图例里摘掉，而不是画两个恒零的扇区让人以为「一篇都没发布」。
    """
    kb_ids = await kbdb.list_visible_kb_ids(user_id=user["id"], domain=domain)
    has_release = await kbdb.has_active_release(domain=domain)

    return {
        "kb_count": len(kb_ids),
        "has_active_release": has_release,
        "trend_days": TREND_DAYS,
        "document_status": await kbdb.stats_document_status(
            kb_ids=kb_ids, with_release=has_release,
        ),
        "assets": await kbdb.stats_assets(kb_ids=kb_ids),
        "retrieval_unit_types": await kbdb.stats_retrieval_unit_types(kb_ids=kb_ids),
        "mining_trend": await kbdb.stats_mining_trend(kb_ids=kb_ids, days=TREND_DAYS),
    }
