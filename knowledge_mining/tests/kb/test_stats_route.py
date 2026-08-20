"""GET /api/kb/stats —— 概览页统计端点。

用假 KbDB + dependency_overrides 走真 HTTP，不需要 PostgreSQL：这里钉的是**装配与
边界**（不被 /{kb_id} 抢匹配、每段统计都受可见集约束、口径开关的传递），
SQL 语义由 test_stats_queries.py 覆盖。写法与 test_overview_route.py 一致。
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.deps import get_kb_db
from knowledge_mining.mining.kb.routes.overview import router as overview_router
from knowledge_mining.mining.kb.routes.kbs import router as kb_router

ZERO_STATUS = {
    "uploaded": 0, "mining": 0, "mined": 0,
    "published": 0, "withdrawn": 0, "failed": 0,
}
ZERO_ASSETS = {
    "snapshots": 0, "segments": 0, "retrieval_units": 0,
    "entity_mentions": 0, "relations": 0,
}


class FakeKbDB:
    """只实现 /stats 用到的六个方法。"""

    def __init__(
        self, *, visible_ids=(), active_release=False, status=None, assets=None,
        unit_types=None, trend=(),
    ):
        self.visible_ids = list(visible_ids)
        self.active_release = active_release
        self.status = status or dict(ZERO_STATUS)
        self.assets = assets or dict(ZERO_ASSETS)
        self.unit_types = unit_types or {}
        self.trend = list(trend)
        # 每段统计实际收到的 kb_ids，用来证明它们都受同一个可见集约束
        self.seen_kb_ids: list[list[str]] = []
        self.seen_with_release: list[bool] = []
        self.seen_trend_days: list[int] = []

    async def list_visible_kb_ids(self, *, user_id: str, domain: str):
        return list(self.visible_ids)

    async def has_active_release(self, *, domain):
        return self.active_release

    async def stats_document_status(self, *, kb_ids, with_release=False):
        self.seen_kb_ids.append(list(kb_ids))
        self.seen_with_release.append(with_release)
        return self.status

    async def stats_assets(self, *, kb_ids):
        self.seen_kb_ids.append(list(kb_ids))
        return self.assets

    async def stats_retrieval_unit_types(self, *, kb_ids):
        self.seen_kb_ids.append(list(kb_ids))
        return self.unit_types

    async def stats_mining_trend(self, *, kb_ids, days=30):
        self.seen_kb_ids.append(list(kb_ids))
        self.seen_trend_days.append(days)
        return self.trend


def _client(db: FakeKbDB, *, user: dict[str, Any] | None = None) -> TestClient:
    """按 app.py 的真实顺序装配，好让「被 /{kb_id} 抢匹配」能复现。"""
    app = FastAPI()
    app.include_router(overview_router)
    app.include_router(kb_router)
    app.dependency_overrides[current_user] = lambda: (
        user or {"id": "u-1", "username": "alice", "site_role": "member"}
    )
    app.dependency_overrides[get_kb_db] = lambda: db
    return TestClient(app)


# ── 路由装配 ────────────────────────────────────────────────────────────────

def test_stats_is_not_shadowed_by_kb_id_route():
    """与 /overview 同一个坑：kb_router 的 /{kb_id} 会把 /stats 当成 kb_id="stats"。"""
    resp = _client(FakeKbDB()).get("/api/kb/stats", params={"domain": "d1"})

    assert resp.status_code == 200
    assert set(resp.json()) == {
        "kb_count", "has_active_release", "trend_days",
        "document_status", "assets", "retrieval_unit_types", "mining_trend",
    }


def test_real_app_resolves_stats_before_kb_id():
    """测 app.py 里的真实注册顺序（不发请求——那会拉起 lifespan 要数据库）。"""
    from knowledge_mining.mining.api.app import create_app

    app = create_app()
    for route in app.routes:
        regex = getattr(route, "path_regex", None)
        if regex is not None and regex.match("/api/kb/stats"):
            assert route.endpoint.__name__ == "kb_stats", (
                f"/api/kb/stats 先被 {route.path} 匹配到了——"
                "kb_overview_router 必须注册在 kb_router 之前"
            )
            break
    else:
        pytest.fail("路由表里没有能匹配 /api/kb/stats 的路由")


def test_domain_is_required():
    assert _client(FakeKbDB()).get("/api/kb/stats").status_code == 422


# ── 边界与口径 ──────────────────────────────────────────────────────────────

def test_every_aggregation_is_bounded_by_the_visible_set():
    """四段统计都必须拿同一个可见集当边界——漏一段就是个越权口。"""
    db = FakeKbDB(visible_ids=["kb-a", "kb-b"])
    _client(db).get("/api/kb/stats", params={"domain": "d1"})

    # status / assets / unit_types / trend
    assert db.seen_kb_ids == [["kb-a", "kb-b"]] * 4


def test_empty_visible_set_returns_zeros_not_404():
    """「一个知识库都没有」是合法状态。前端要渲染出骨架和 0，与"接口挂了"区分开。"""
    resp = _client(FakeKbDB(visible_ids=[])).get("/api/kb/stats", params={"domain": "d1"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["kb_count"] == 0
    assert body["document_status"] == ZERO_STATUS
    assert body["assets"] == ZERO_ASSETS
    assert body["retrieval_unit_types"] == {}


def test_kb_count_reflects_the_visible_scope():
    db = FakeKbDB(visible_ids=["a", "b", "c"])
    body = _client(db).get("/api/kb/stats", params={"domain": "d1"}).json()

    assert body["kb_count"] == 3


@pytest.mark.parametrize("present", [True, False])
def test_active_release_switches_the_document_status_scope(present):
    """published/withdrawn 两档要不要算，由域里有没有 active release 决定。

    没有 release 时开完整派生纯属白付钱（那两档恒 0），而且会让前端把两个恒零扇区
    画进图里——读起来像「一篇都没发布」，真相是这个口径不适用。
    """
    db = FakeKbDB(visible_ids=["kb-a"], active_release=present)
    body = _client(db).get("/api/kb/stats", params={"domain": "d1"}).json()

    assert db.seen_with_release == [present]
    assert body["has_active_release"] is present


def test_trend_days_is_reported_so_frontend_need_not_hardcode_30():
    """窗口天数只在后端定义一次；前端拿它写标题，避免两边各写一个 30。"""
    from knowledge_mining.mining.kb.routes.overview import TREND_DAYS

    db = FakeKbDB(visible_ids=["kb-a"])
    body = _client(db).get("/api/kb/stats", params={"domain": "d1"}).json()

    assert body["trend_days"] == TREND_DAYS
    assert db.seen_trend_days == [TREND_DAYS]


def test_payload_passes_through_unchanged():
    """路由只做装配，不改数字——重排/改名会让前端与 SQL 的口径悄悄分叉。"""
    db = FakeKbDB(
        visible_ids=["kb-a"],
        status={**ZERO_STATUS, "mined": 7, "failed": 1},
        assets={**ZERO_ASSETS, "retrieval_units": 120, "relations": 65},
        unit_types={"raw_text": 80, "summary": 40},
        trend=[{"date": "2026-08-18", "runs": 1, "completed": 1, "documents": 3}],
    )
    body = _client(db).get("/api/kb/stats", params={"domain": "d1"}).json()

    assert body["document_status"]["mined"] == 7
    assert body["assets"]["retrieval_units"] == 120
    assert body["retrieval_unit_types"] == {"raw_text": 80, "summary": 40}
    assert body["mining_trend"] == [
        {"date": "2026-08-18", "runs": 1, "completed": 1, "documents": 3}
    ]
