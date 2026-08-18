"""`/api/runs/*` 身份护栏。

这一族端点历史上完全不校验身份。堵掉 list 只关闭了批量枚举，拿到单个 runId 仍能读别人
私有库的段落/单元/关系、并 cancel/publish/resume 别人的挖掘——所以护栏是整族的。

用假连接池做单元测试，不需要 PostgreSQL：这里要钉住的是**判定逻辑与路由装配**，不是 SQL。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException

from knowledge_mining.mining.api.routes import runs


# ── 假件 ────────────────────────────────────────────────────────────────────

class _Cursor:
    def __init__(self, row=None, rows=None):
        self._row, self._rows = row, rows or []

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, run: dict | None, listed: list[dict] | None = None):
        self.run = run
        self.listed = listed or []
        self.seen_sql: list[str] = []
        self.seen_params: list[list] = []

    async def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.seen_sql.append(normalized)
        self.seen_params.append(list(params or []))
        if "SELECT id, domain, status, kb_id FROM mining_runs" in normalized:
            return _Cursor(row=dict(self.run) if self.run else None)
        if "COUNT(*) as c" in normalized:
            return _Cursor(row={"c": len(self.listed)})
        if "ORDER BY started_at DESC" in normalized:
            return _Cursor(rows=[dict(r) for r in self.listed])
        raise AssertionError(f"unexpected SQL: {normalized}")


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self.conn


class _FakeKbDB:
    """KbDB 替身：只实现护栏用到的三个方法。"""

    def __init__(self, *, visible=(), writable=(), scope=()):
        self.visible, self.writable, self.scope = set(visible), set(writable), list(scope)

    async def is_visible(self, *, kb_id, user_id):
        return kb_id in self.visible

    async def can_write(self, *, kb_id, user_id):
        return kb_id in self.writable

    async def list_visible_kb_ids(self, *, user_id, domain):
        return list(self.scope)


def _request(conn) -> SimpleNamespace:
    async def get_pool(domain):
        return _Pool(conn)

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                domain_pools=SimpleNamespace(async_pool=get_pool),
                pg_pool=object(),
            )
        )
    )


def _run_row(*, kb_id=None, run_id="run-1", domain="plant-a"):
    return {"id": run_id, "domain": domain, "status": "completed", "kb_id": kb_id}


ADMIN = {"id": "u-admin", "site_role": "admin"}
MEMBER = {"id": "u-member", "site_role": "member"}


@pytest.fixture(autouse=True)
def _passthrough_domain(monkeypatch):
    monkeypatch.setattr(runs, "require_domain", lambda value: value)


def _patch_kbdb(monkeypatch, fake: _FakeKbDB):
    monkeypatch.setattr(runs, "KbDB", lambda pool: fake)


# ── 路由装配：漏挂护栏要能被测出来 ──────────────────────────────────────────

def test_every_run_route_carries_a_guard():
    """新增路由忘挂护栏 = 又开一个越权口，所以结构性地钉住，而不是逐路由列举。

    router 级 Depends(current_user) 保证没有匿名端点；每个具体路由还必须有一个更细
    的授权依赖（可见性 / 写权限 / admin）——只挂 current_user 意味着"登录即可访问"。
    """
    app = FastAPI()
    app.include_router(runs.router)

    fine_grained = {"require_run_read", "require_run_write", "require_admin"}
    unguarded = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/runs"):
            continue
        names = {
            d.call.__name__ for d in route.dependant.dependencies if getattr(d, "call", None)
        }
        assert "current_user" in names, f"{path} 缺少 router 级身份依赖"
        if path == "/api/runs" and "GET" in route.methods:
            continue  # list 的收窄在处理函数内部（要按可见集过滤，不是二元放行）
        if not names & fine_grained:
            unguarded.append(f"{sorted(route.methods)} {path}")

    assert not unguarded, f"以下路由只有身份、没有授权：{unguarded}"


def test_write_routes_require_write_guard():
    """cancel / publish / resume 是跨 KB 变更，读权限不够。"""
    app = FastAPI()
    app.include_router(runs.router)

    expected = {"/api/runs/{run_id}/cancel", "/api/runs/{run_id}/publish",
                "/api/runs/{run_id}/resume"}
    seen = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if path not in expected:
            continue
        names = {
            d.call.__name__ for d in route.dependant.dependencies if getattr(d, "call", None)
        }
        assert "require_run_write" in names, f"{path} 用的不是写护栏"
        seen.add(path)
    assert seen == expected


def test_domain_level_create_is_admin_only():
    """域级挖掘入口写 kb_id=NULL 且默认 publish=true，影响整个域——普通成员走 /api/kb/{id}/mine。"""
    app = FastAPI()
    app.include_router(runs.router)

    for route in app.routes:
        if getattr(route, "path", "") in ("/api/runs", "/api/runs/preflight") and (
            "POST" in route.methods
        ):
            names = {
                d.call.__name__
                for d in route.dependant.dependencies
                if getattr(d, "call", None)
            }
            assert "require_admin" in names, f"{route.path} 不是 admin-only"


# ── 单 run 授权判定 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_legacy_run_is_invisible_to_non_admin(monkeypatch):
    """kb_id 为 NULL 的域级 run 不属于任何库，非 admin 一律 404（不是 403——不泄露存在性）。"""
    conn = _Connection(_run_row(kb_id=None))
    _patch_kbdb(monkeypatch, _FakeKbDB())

    with pytest.raises(HTTPException) as exc:
        await runs._authorize_run(
            _request(conn), "run-1", "plant-a", user=MEMBER, write=False,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_legacy_run_stays_readable_for_admin(monkeypatch):
    conn = _Connection(_run_row(kb_id=None))
    _patch_kbdb(monkeypatch, _FakeKbDB())

    run = await runs._authorize_run(
        _request(conn), "run-1", "plant-a", user=ADMIN, write=False,
    )
    assert run["id"] == "run-1"


@pytest.mark.asyncio
async def test_run_of_invisible_kb_is_404(monkeypatch):
    """越权与不存在共用同一响应。"""
    conn = _Connection(_run_row(kb_id="kb-other"))
    _patch_kbdb(monkeypatch, _FakeKbDB(visible=(), writable=()))

    with pytest.raises(HTTPException) as exc:
        await runs._authorize_run(
            _request(conn), "run-1", "plant-a", user=MEMBER, write=False,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_visible_kb_grants_read(monkeypatch):
    conn = _Connection(_run_row(kb_id="kb-a"))
    _patch_kbdb(monkeypatch, _FakeKbDB(visible={"kb-a"}))

    run = await runs._authorize_run(
        _request(conn), "run-1", "plant-a", user=MEMBER, write=False,
    )
    assert run["kb_id"] == "kb-a"


@pytest.mark.asyncio
async def test_viewer_cannot_write(monkeypatch):
    """可见但只读 → 403。存在性此时已不是秘密，再报 404 只会让人以为链接坏了。"""
    conn = _Connection(_run_row(kb_id="kb-a"))
    _patch_kbdb(monkeypatch, _FakeKbDB(visible={"kb-a"}, writable=set()))

    with pytest.raises(HTTPException) as exc:
        await runs._authorize_run(
            _request(conn), "run-1", "plant-a", user=MEMBER, write=True,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_editor_can_write(monkeypatch):
    conn = _Connection(_run_row(kb_id="kb-a"))
    _patch_kbdb(monkeypatch, _FakeKbDB(visible={"kb-a"}, writable={"kb-a"}))

    run = await runs._authorize_run(
        _request(conn), "run-1", "plant-a", user=MEMBER, write=True,
    )
    assert run["kb_id"] == "kb-a"


@pytest.mark.asyncio
async def test_wrong_domain_is_404_before_any_kb_lookup(monkeypatch):
    """域不匹配时连 KB 都不该去查——run 在本域根本不存在。"""
    conn = _Connection(None)

    def _boom(pool):
        raise AssertionError("不该在域校验失败后还去查 KB")

    monkeypatch.setattr(runs, "KbDB", _boom)

    with pytest.raises(HTTPException) as exc:
        await runs._authorize_run(
            _request(conn), "run-1", "plant-a", user=ADMIN, write=False,
        )
    assert exc.value.status_code == 404


# ── list 的可见集收窄 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_runs_narrows_to_visible_kbs_for_member(monkeypatch):
    conn = _Connection(None, listed=[
        {"id": "r1", "status": "completed", "execution_engine": "legacy",
         "workflow_manifest_json": None},
    ])
    _patch_kbdb(monkeypatch, _FakeKbDB(scope=["kb-a", "kb-b"]))

    result = await runs.list_runs(_request(conn), domain="plant-a", user=MEMBER)

    assert result["total"] == 1
    joined = " | ".join(conn.seen_sql)
    assert "kb_id = ANY(%s)" in joined
    # 可见集必须真的作为参数下推，而不是只出现在 SQL 文本里
    assert any(["kb-a", "kb-b"] in params for params in conn.seen_params)


@pytest.mark.asyncio
async def test_list_runs_unfiltered_for_admin(monkeypatch):
    conn = _Connection(None, listed=[
        {"id": "r1", "status": "completed", "execution_engine": "legacy",
         "workflow_manifest_json": None},
    ])

    def _boom(pool):
        raise AssertionError("admin 不该去解析可见集")

    monkeypatch.setattr(runs, "KbDB", _boom)

    result = await runs.list_runs(_request(conn), domain="plant-a", user=ADMIN)

    assert result["total"] == 1
    assert all("kb_id = ANY(%s)" not in sql for sql in conn.seen_sql)


@pytest.mark.asyncio
async def test_list_runs_returns_empty_page_when_nothing_visible(monkeypatch):
    """一个可见库都没有时必须早退：空数组参数在 psycopg 里推断不出元素类型，会直接报错。"""
    conn = _Connection(None)
    _patch_kbdb(monkeypatch, _FakeKbDB(scope=[]))

    # 直接调处理函数时 limit/offset 不会被 FastAPI 解析成默认值，显式传。
    result = await runs.list_runs(
        _request(conn), domain="plant-a", limit=20, offset=0, user=MEMBER,
    )

    assert result == {"total": 0, "limit": 20, "offset": 0, "items": []}
    assert conn.seen_sql == []  # 连库都没查
