"""GET /api/ops/usage —— 运维使用分析端点。

用假 QueryLogStats + dependency_overrides 走真 HTTP，不需要 PostgreSQL：这里钉的是
**鉴权、口径参数传递、表缺失降级**，SQL 语义由 test_ops_usage_queries.py 覆盖。

最要紧的两条：
- 非 admin 一律 403（响应里含用户输入原文，泄露了收不回来）；
- serving 从没起过时不能 500。
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from knowledge_mining.mining.api.routes.ops import (
    DEFAULT_WINDOW_DAYS, TREND_DAYS, router as ops_router,
)
from knowledge_mining.mining.kb.auth import require_admin

PAYLOAD_KEYS = {
    "available", "days", "trend_days", "summary", "no_result_queries",
    "top_queries", "paradigms", "trend", "intents", "channels",
}


class FakeStats:
    """记录每段聚合收到的 domain/days，用来证明口径被一致地传下去。"""

    def __init__(self, *, available: bool = True):
        self.available = available
        self.calls: list[tuple[str, str, int]] = []

    async def is_available(self):
        return self.available

    def _note(self, what, domain, days):
        self.calls.append((what, domain, days))

    async def summary(self, *, domain, days):
        self._note("summary", domain, days)
        return {
            "queries": 100, "no_result": 7, "no_result_rate": 0.07,
            "p95_duration_ms": 412.0, "avg_duration_ms": 180.0, "active_paradigms": 2,
        }

    async def no_result_queries(self, *, domain, days, limit):
        self._note("no_result_queries", domain, days)
        return [{"query_text": "SMF 会话建立超时", "count": 12, "last_at": "2026-08-18T01:00:00Z"}]

    async def top_queries(self, *, domain, days, limit):
        self._note("top_queries", domain, days)
        return [{"query_text": "5GC 计费接口", "count": 30, "no_result": 2}]

    async def paradigm_usage(self, *, domain, days):
        self._note("paradigms", domain, days)
        return [{"paradigm_id": "p-1", "calls": 80, "no_result": 3, "p95_duration_ms": 300.0}]

    async def trend(self, *, domain, days):
        self._note("trend", domain, days)
        return [{"date": "2026-08-18", "queries": 10, "no_result": 1}]

    async def breakdown(self, *, domain, days, column):
        self._note(f"breakdown:{column}", domain, days)
        return {"lookup": 60, "(未知)": 40}


class _FakePools:
    async def async_pool(self, domain):
        return object()      # QueryLogStats 被整体替换，池不会被真正使用


def _client(
    stats: FakeStats, *, admin: bool = True, monkeypatch=None,
) -> TestClient:
    app = FastAPI()
    app.include_router(ops_router)
    app.state.domain_pools = _FakePools()

    if admin:
        app.dependency_overrides[require_admin] = lambda: {
            "id": "u-1", "username": "root", "site_role": "admin",
        }
    else:
        def _denied():
            raise HTTPException(403, "admin required")
        app.dependency_overrides[require_admin] = _denied

    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_deps(monkeypatch):
    """把域校验与仓储都换成可控的假件。"""
    monkeypatch.setattr(
        "knowledge_mining.mining.api.routes.ops.require_domain", lambda d: d,
    )


def _install_stats(monkeypatch, stats: FakeStats):
    monkeypatch.setattr(
        "knowledge_mining.mining.api.routes.ops.QueryLogStats", lambda _pool: stats,
    )


# ── 鉴权 ────────────────────────────────────────────────────────────────────

def test_non_admin_is_403(monkeypatch):
    """响应里含用户输入原文——泄露了收不回来，所以这条是硬约束。"""
    stats = FakeStats()
    _install_stats(monkeypatch, stats)

    resp = _client(stats, admin=False).get("/api/ops/usage", params={"domain": "d1"})

    assert resp.status_code == 403
    assert stats.calls == []      # 连查都没查


def test_admin_gets_200(monkeypatch):
    stats = FakeStats()
    _install_stats(monkeypatch, stats)

    resp = _client(stats).get("/api/ops/usage", params={"domain": "d1"})

    assert resp.status_code == 200
    assert set(resp.json()) == PAYLOAD_KEYS


def test_route_declares_require_admin():
    """反证：护栏若被摘掉，上面那条 403 用例会因 override 失去目标而假绿。

    所以直接断言签名上挂的就是 require_admin 本人，而不是"某个会拒绝的东西"。
    """
    import inspect

    from knowledge_mining.mining.api.routes.ops import usage_stats

    admin_param = inspect.signature(usage_stats).parameters["_admin"]
    assert admin_param.default.dependency is require_admin


# ── 表缺失降级 ──────────────────────────────────────────────────────────────

def test_missing_table_degrades_instead_of_500(monkeypatch):
    """serving 从没启动过 → 表不存在。那是合法状态，不是故障。"""
    stats = FakeStats(available=False)
    _install_stats(monkeypatch, stats)

    resp = _client(stats).get("/api/ops/usage", params={"domain": "d1"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert stats.calls == []      # 表不在就一段聚合都不该跑


def test_empty_payload_keeps_the_same_shape(monkeypatch):
    """空壳与正常响应形状必须一致，前端只判 available、不必逐字段判 undefined。"""
    _install_stats(monkeypatch, FakeStats(available=False))
    empty = _client(FakeStats(available=False)).get(
        "/api/ops/usage", params={"domain": "d1"}).json()

    _install_stats(monkeypatch, FakeStats(available=True))
    full = _client(FakeStats()).get("/api/ops/usage", params={"domain": "d1"}).json()

    assert set(empty) == set(full)
    assert set(empty["summary"]) == set(full["summary"])


# ── 口径传递 ────────────────────────────────────────────────────────────────

def test_window_defaults_to_seven_days(monkeypatch):
    stats = FakeStats()
    _install_stats(monkeypatch, stats)

    body = _client(stats).get("/api/ops/usage", params={"domain": "d1"}).json()

    assert body["days"] == DEFAULT_WINDOW_DAYS
    # 除趋势外，每段都用同一个窗口
    non_trend = [c for c in stats.calls if c[0] != "trend"]
    assert {c[2] for c in non_trend} == {DEFAULT_WINDOW_DAYS}


def test_trend_window_is_independent_of_days(monkeypatch):
    """折线固定 30 天：与挖掘趋势并排看时，两条线的窗口必须一致。"""
    stats = FakeStats()
    _install_stats(monkeypatch, stats)

    body = _client(stats).get(
        "/api/ops/usage", params={"domain": "d1", "days": 3}).json()

    assert body["days"] == 3
    assert body["trend_days"] == TREND_DAYS
    trend_call = next(c for c in stats.calls if c[0] == "trend")
    assert trend_call[2] == TREND_DAYS


def test_domain_is_passed_to_every_aggregation(monkeypatch):
    """漏传一段就是把别的域的查询原文混进来。"""
    stats = FakeStats()
    _install_stats(monkeypatch, stats)

    _client(stats).get("/api/ops/usage", params={"domain": "cloud_core_network"})

    assert {c[1] for c in stats.calls} == {"cloud_core_network"}
    # 六段聚合都跑到了
    assert {c[0] for c in stats.calls} == {
        "summary", "no_result_queries", "top_queries", "paradigms", "trend",
        "breakdown:intent", "breakdown:channel",
    }


def test_domain_is_required(monkeypatch):
    _install_stats(monkeypatch, FakeStats())
    assert _client(FakeStats()).get("/api/ops/usage").status_code == 422


@pytest.mark.parametrize("days", [0, 91])
def test_days_out_of_range_is_422(monkeypatch, days):
    _install_stats(monkeypatch, FakeStats())
    resp = _client(FakeStats()).get(
        "/api/ops/usage", params={"domain": "d1", "days": days})
    assert resp.status_code == 422


def test_real_app_registers_the_route():
    """确认 app.py 真的挂上了——路由写好却没注册是最容易漏的一步。"""
    from knowledge_mining.mining.api.app import create_app

    app = create_app()
    assert any(
        getattr(r, "path", None) == "/api/ops/usage" for r in app.routes
    ), "app.py 未注册 ops_router"
