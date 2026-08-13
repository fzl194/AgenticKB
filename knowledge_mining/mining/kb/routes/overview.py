"""概览页聚合端点 —— GET /api/kb/overview。

首页（检索入口 + 我的知识库）一次调用取齐所需：可见知识库全集 + 每库状态摘要 +
跨库最近挖掘 + 该域有无 active release。

**为什么是聚合端点而不是几个小接口**：
- 一个授权点。拆成多个接口，只要有一个忘了按可见集收敛就是个越权口（/api/runs 就是
  这么漏的）。
- 各区块数据同源同时刻。分次请求会出现「卡片说挖掘中、下面的最近挖掘说已完成」。
- 一次往返。首页是登录后的落地页，延迟最敏感。

**⚠️ 路由注册顺序是承重的**：本 router 与 kb_router 同 prefix（/api/kb），而 kb_router
有吞噬型动态段 /{kb_id}。app.py 必须把本 router 注册在 kb_router **之前**，否则
GET /api/kb/overview 会被当成 kb_id="overview" → 404。同款事故见 62aebd1。
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
