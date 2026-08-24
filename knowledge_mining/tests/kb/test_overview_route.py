"""GET /api/kb/overview —— 概览页聚合端点。

用假 KbDB + dependency_overrides 走真 HTTP，不需要 PostgreSQL：这里要钉的是**装配与
组装逻辑**（路由不被 /{kb_id} 抢匹配、可见集边界、补零、排序、can_write 推导），
SQL 本身的语义由 tests/kb/test_status_derivation_scope.py 与 test_kb_db.py 覆盖。
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.deps import get_kb_db, get_kb_service
from knowledge_mining.mining.kb.routes.overview import router as overview_router
from knowledge_mining.mining.kb.routes.kbs import router as kb_router
from knowledge_mining.mining.kb.services.kb_service import NotFound


class FakeKbDB:
    """只实现 overview 用到的五个方法。"""

    def __init__(
        self, *, visible=(), counts=None, rollup=None, recent=(), active_release=False,
    ):
        self.visible = [dict(k) for k in visible]
        self.counts = counts or {}
        self.rollup = rollup or {}
        self.recent = [dict(r) for r in recent]
        self.active_release = active_release
        self.seen_kb_ids: list[list[str]] = []

    async def list_visible(self, *, user_id: str, domain: str):
        return [dict(k) for k in self.visible]

    async def overview_status_counts(self, *, kb_ids):
        self.seen_kb_ids.append(list(kb_ids))
        return self.counts

    async def overview_run_rollup(self, *, kb_ids):
        self.seen_kb_ids.append(list(kb_ids))
        return self.rollup

    async def overview_recent_runs(self, *, kb_ids, limit=5):
        self.seen_kb_ids.append(list(kb_ids))
        return self.recent[:limit]

    async def has_active_release(self, *, domain):
        return self.active_release


def _client(db: FakeKbDB, *, user: dict[str, Any] | None = None) -> TestClient:
    """按 app.py 的真实顺序装配 overview + kb_router，好让「被 /{kb_id} 抢匹配」能复现。"""
    app = FastAPI()
    app.include_router(overview_router)
    app.include_router(kb_router)
    app.dependency_overrides[current_user] = lambda: (
        user or {"id": "u-1", "username": "alice", "site_role": "member"}
    )
    app.dependency_overrides[get_kb_db] = lambda: db
    return TestClient(app)


def _kb(kb_id, name, role="owner", created_at="2026-01-01T00:00:00+00:00"):
    return {"id": kb_id, "name": name, "my_role": role, "created_at": created_at}


# ── 路由装配 ────────────────────────────────────────────────────────────────

def test_overview_is_not_shadowed_by_kb_id_route():
    """kb_router 的 /{kb_id} 会吞掉同 prefix 的静态段——必须先注册 overview。

    断言响应体形状而不只是状态码：被 /{kb_id} 抢到时返回的是 404，只测状态码的用例
    虽然也会红，但看不出根因是路由顺序。
    """
    db = FakeKbDB(visible=[_kb("kb-a", "A")])
    resp = _client(db).get("/api/kb/overview", params={"domain": "d1"})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"has_active_release", "kbs", "recent_runs"}


def test_wrong_registration_order_actually_breaks():
    """反证：顺序反了就真的坏——证明上一条测的是真约束，不是巧合。

    请求会落到 kb_router 的 get_kb（kb_id="overview"）而不是 overview 处理函数。
    这里把 KbService 也换成假的，好让结果是干净的 404「知识库不存在」——线上正是这个
    表现：接口看起来"不存在"，而真因是路由顺序。
    """
    class _NotFoundService:
        async def get_kb(self, **_kwargs):
            raise NotFound("kb not found")

    app = FastAPI()
    app.include_router(kb_router)      # 先注册动态段
    app.include_router(overview_router)
    app.dependency_overrides[current_user] = lambda: {"id": "u-1", "site_role": "member"}
    app.dependency_overrides[get_kb_db] = lambda: FakeKbDB(visible=[])
    app.dependency_overrides[get_kb_service] = lambda: _NotFoundService()

    resp = TestClient(app).get("/api/kb/overview", params={"domain": "d1"})
    assert resp.status_code == 404  # 被当成 kb_id="overview"


# ── 组装逻辑 ────────────────────────────────────────────────────────────────

def test_real_app_resolves_overview_before_kb_id():
    """上面两条测的是手工装配；这条测 app.py 里的真实注册顺序。

    不发请求（会拉起 lifespan 要数据库），直接按 Starlette 的匹配规则走一遍路由表：
    第一个匹配 /api/kb/overview 的路由必须是 overview 处理函数。
    """
    from knowledge_mining.mining.api.app import create_app

    app = create_app()
    for route in app.routes:
        regex = getattr(route, "path_regex", None)
        if regex is not None and regex.match("/api/kb/overview"):
            assert route.endpoint.__name__ == "kb_overview", (
                f"/api/kb/overview 先被 {route.path} 匹配到了——"
                "kb_overview_router 必须注册在 kb_router 之前"
            )
            break
    else:
        pytest.fail("路由表里没有能匹配 /api/kb/overview 的路由")


def test_empty_visible_set_returns_empty_arrays_not_404():
    """「还没有知识库」是合法状态，不是错误。"""
    resp = _client(FakeKbDB(visible=[])).get("/api/kb/overview", params={"domain": "d1"})

    assert resp.status_code == 200
    assert resp.json() == {"has_active_release": False, "kbs": [], "recent_runs": []}


def test_counts_default_to_zero_for_kb_without_documents():
    """无文档 / 无 run 的库在聚合里没有行——补零而不是缺键。"""
    db = FakeKbDB(visible=[_kb("kb-a", "A")])
    body = _client(db).get("/api/kb/overview", params={"domain": "d1"}).json()

    kb = body["kbs"][0]
    assert kb["status_counts"] == {"total": 0, "mining": 0, "failed": 0}
    assert kb["last_mined_at"] is None
    assert kb["awaiting_review_run_id"] is None


def test_counts_and_rollup_are_merged_per_kb():
    db = FakeKbDB(
        visible=[_kb("kb-a", "A"), _kb("kb-b", "B")],
        counts={
            "kb-a": {"total": 42, "mining": 0, "failed": 2},
            "kb-b": {"total": 18, "mining": 3, "failed": 0},
        },
        rollup={
            "kb-a": {"last_mined_at": "2026-08-11T09:12:00+00:00",
                     "awaiting_review_run_id": None},
            "kb-b": {"last_mined_at": None, "awaiting_review_run_id": "run-b"},
        },
    )
    body = _client(db).get("/api/kb/overview", params={"domain": "d1"}).json()
    by_id = {k["id"]: k for k in body["kbs"]}

    assert by_id["kb-a"]["status_counts"] == {"total": 42, "mining": 0, "failed": 2}
    assert by_id["kb-a"]["last_mined_at"] == "2026-08-11T09:12:00+00:00"
    assert by_id["kb-b"]["awaiting_review_run_id"] == "run-b"


@pytest.mark.parametrize(
    "role,writable",
    [("owner", True), ("editor", True), ("admin", True), ("viewer", False)],
)
def test_can_write_derives_from_my_role(role, writable):
    """待处理区块只列有写权限的库。判定从 my_role 推，不逐库调 can_write（那是 N 次往返）。"""
    db = FakeKbDB(visible=[_kb("kb-a", "A", role=role)])
    body = _client(db).get("/api/kb/overview", params={"domain": "d1"}).json()

    assert body["kbs"][0]["can_write"] is writable


def test_kbs_sorted_by_last_mined_then_created_with_nulls_last():
    db = FakeKbDB(
        visible=[
            _kb("never", "从没挖过", created_at="2026-03-01T00:00:00+00:00"),
            _kb("old", "挖得早", created_at="2026-01-01T00:00:00+00:00"),
            _kb("recent", "刚挖过", created_at="2026-02-01T00:00:00+00:00"),
        ],
        rollup={
            "old": {"last_mined_at": "2026-05-01T00:00:00+00:00",
                    "awaiting_review_run_id": None},
            "recent": {"last_mined_at": "2026-08-01T00:00:00+00:00",
                       "awaiting_review_run_id": None},
        },
    )
    body = _client(db).get("/api/kb/overview", params={"domain": "d1"}).json()

    # 挖过的按时间倒序在前；没挖过的沉底（即使它创建得最晚）
    assert [k["id"] for k in body["kbs"]] == ["recent", "old", "never"]


def test_created_at_is_not_leaked_into_the_contract():
    """created_at 只用于排序，不属于对外契约——留着会让前端误以为可以依赖它。"""
    db = FakeKbDB(visible=[_kb("kb-a", "A")])
    body = _client(db).get("/api/kb/overview", params={"domain": "d1"}).json()

    assert set(body["kbs"][0]) == {
        "id", "name", "my_role", "can_write",
        "status_counts", "last_mined_at", "awaiting_review_run_id",
    }


def test_all_aggregations_are_bounded_by_the_visible_set():
    """每一段聚合都必须拿同一个可见集当边界——漏一段就是个越权口。"""
    db = FakeKbDB(visible=[_kb("kb-a", "A"), _kb("kb-b", "B")])
    _client(db).get("/api/kb/overview", params={"domain": "d1"})

    assert db.seen_kb_ids == [["kb-a", "kb-b"]] * 3  # counts / rollup / recent_runs


def test_recent_runs_carry_kb_id_for_deep_link():
    """前端要用 kb_id 拼 /kb/{kbId}/run/{runId}；缺了只能拼出已删除的 /mining/{runId}。"""
    db = FakeKbDB(
        visible=[_kb("kb-a", "A")],
        recent=[{
            "id": "run-1", "kb_id": "kb-a", "kb_name": "A", "status": "completed",
            "total_documents": 12, "new_count": 3, "updated_count": 1,
            "started_at": "2026-08-11T09:12:00+00:00",
            "finished_at": "2026-08-11T09:14:14+00:00",
        }],
    )
    body = _client(db).get("/api/kb/overview", params={"domain": "d1"}).json()

    run = body["recent_runs"][0]
    assert run["kb_id"] == "kb-a" and run["kb_name"] == "A"


def test_has_active_release_is_reported_for_scope_picker():
    """纯 KB 部署恒 False → 前端不呈现「域级发布」项（那个隐式范围必撞 no_active_release）。"""
    for present in (True, False):
        db = FakeKbDB(visible=[], active_release=present)
        body = _client(db).get("/api/kb/overview", params={"domain": "d1"}).json()
        assert body["has_active_release"] is present


def test_domain_is_required():
    resp = _client(FakeKbDB(visible=[])).get("/api/kb/overview")
    assert resp.status_code == 422
