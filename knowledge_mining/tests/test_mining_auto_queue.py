"""MCP 上传自动挖掘（排队语义）——迁移 010 + auto_mine 服务 + upload 端点。

不依赖真实 PostgreSQL：仓储/绑定/调度全部以假件注入，与
``test_mining_queue_32fix.py`` 同一风格。真实 PG 的排队行为由
``tests/kb/test_mining.py`` 的 acceptance 用例覆盖。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


# ── 迁移 010：唯一索引收窄到活跃状态 ──────────────────────────────────────


def test_auto_queue_migration_is_registered_and_narrows_unique_to_active() -> None:
    from knowledge_mining.mining.infra.pg_schema import domain_schema_paths

    paths = domain_schema_paths()
    migration = next(
        path for path in paths if path.name == "010_mining_run_kb_auto_queue.sql"
    )
    ddl = migration.read_text(encoding="utf-8").lower()

    assert "drop index if exists uq_mining_runs_one_open_per_kb" in ddl
    assert "uq_mining_runs_one_active_per_kb" in ddl
    # 新索引谓词只含活跃状态——queued 不再占唯一性槽位
    predicate = ddl.split("uq_mining_runs_one_active_per_kb", 1)[1]
    for status in ("running", "awaiting_review", "interrupted"):
        assert f"'{status}'" in predicate
    assert "'queued'" not in predicate


def test_auto_queue_migration_runs_transactionally() -> None:
    """DROP+CREATE 必须原子：常量须同时出现在 DDL 链与事务清单（带逗号两处引用）。"""
    import inspect

    from knowledge_mining.mining.infra import pg_schema

    source = inspect.getsource(pg_schema)
    assert source.count("_MINING_RUN_KB_AUTO_QUEUE_DDL,") == 2


# ── 仓储：整库排队 Run 查询 ────────────────────────────────────────────────


class _Cursor:
    def __init__(self, row=None):
        self.row = row

    async def fetchone(self):
        return self.row


class _AsyncConnection:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    async def execute(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))
        return _Cursor(self.row)


class _AsyncPool:
    def __init__(self, row=None):
        self.conn = _AsyncConnection(row)

    @asynccontextmanager
    async def connection(self):
        yield self.conn


@pytest.mark.asyncio
async def test_find_queued_whole_kb_run_filters_selective_runs() -> None:
    from knowledge_mining.mining.workflow.repositories.domain_run_repository import (
        AsyncDomainRunRepository,
    )

    pool = _AsyncPool({"id": "r1", "status": "queued"})
    row = await AsyncDomainRunRepository(pool).find_queued_whole_kb_run("kb-1")

    assert row == {"id": "r1", "status": "queued"}
    sql, params = pool.conn.calls[0]
    assert "kb_id = %s" in sql
    assert "status = 'queued'" in sql
    # 选择性 Run（document_ids 非空数组）不作合并目标
    assert "jsonb_typeof" in sql and "document_ids" in sql
    assert params == ("kb-1",)


# ── 调度守卫：queued Run 只在本库无活跃 Run 时被认领 ──────────────────────


def test_next_candidate_sql_guards_against_active_same_kb_run(monkeypatch) -> None:
    from knowledge_mining.mining.api.domain_run_queue import build_domain_run_dispatcher
    from knowledge_mining.mining.jobs import run as run_job

    captured: dict[str, str] = {}

    class Cursor:
        def fetchone(self):
            return None

    class Connection:
        def execute(self, sql, params):
            captured["sql"] = " ".join(sql.split())
            return Cursor()

    class SyncConnectionContext:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_args):
            return None

    class SyncPool:
        def connection(self):
            return SyncConnectionContext()

    monkeypatch.setattr(run_job, "run", lambda *_args, **_kwargs: {"status": "completed"})
    dispatcher = build_domain_run_dispatcher(
        SimpleNamespace(sync_pool=lambda _domain: SyncPool()), object(),
    )
    assert dispatcher.drain("odn") == 0

    sql = captured["sql"].lower()
    assert "not exists" in sql
    for status in ("running", "awaiting_review", "interrupted"):
        assert f"'{status}'" in sql
    assert "active.kb_id = r.kb_id" in sql


# ── auto_mine 服务 ─────────────────────────────────────────────────────────


class _KbDb:
    def __init__(self, kb=None):
        self.kb = kb or {
            "id": "kb-1", "domain": "odn", "mining_workflow_id": "wf",
        }

    async def get_kb(self, _kb_id):
        return self.kb

    async def list_documents_in_kb(self, **_kwargs):
        return [{"id": "doc-1"}]


def _binding():
    return SimpleNamespace(
        workflow_id="wf", workflow_version=1,
        workflow_version_id="wfv-1", graph_hash="hash",
        manifest={"workflowId": "wf"},
    )


class _Repo:
    """可编程假仓储：queued_whole / open / insert 行为按用例注入。"""

    def __init__(self, queued_whole=None, insert_error=None):
        self.queued_whole = queued_whole
        self.insert_error = insert_error
        self.inserted: list[dict] = []

    async def find_queued_whole_kb_run(self, _kb_id):
        row = self.queued_whole.pop(0) if isinstance(self.queued_whole, list) else self.queued_whole
        return row

    async def insert_queued_run(self, **kwargs):
        if self.insert_error is not None:
            raise self.insert_error
        self.inserted.append(kwargs)


def _app_state(repo, resolve=None, resolve_error=None):
    async def _resolve(**_kwargs):
        if resolve_error is not None:
            raise resolve_error
        return _binding()

    kicked: list[str] = []
    async def _pool(_domain):
        return object()

    state = SimpleNamespace(
        domain_pools=SimpleNamespace(async_pool=_pool),
        workflow_run_binder=SimpleNamespace(resolve=resolve or _resolve),
        domain_run_dispatcher=SimpleNamespace(kick=lambda domain: kicked.append(domain)),
    )
    return state, kicked


def _patch(monkeypatch, repo_factory):
    from knowledge_mining.mining.kb.services import auto_mine

    monkeypatch.setattr(auto_mine, "AsyncDomainRunRepository", repo_factory)
    monkeypatch.setattr(auto_mine, "resolve_domain", lambda _domain: {"default_channel": "prod"})
    monkeypatch.setattr(
        auto_mine, "UploadConfig",
        lambda: SimpleNamespace(upload_root_path=Path("/uploads")),
    )
    return auto_mine


@pytest.mark.asyncio
async def test_auto_mine_inserts_queued_run_and_kicks_dispatcher(monkeypatch) -> None:
    repo = _Repo(queued_whole=None)
    auto_mine = _patch(monkeypatch, lambda _pool: repo)
    state, kicked = _app_state(repo)

    out = await auto_mine.enqueue_auto_mining(
        app_state=state, kbdb=_KbDb(), kb=_KbDb().kb,
        user_id="u1", username="alice",
    )

    assert out["auto_mined"] is True
    assert out["merged"] is False
    assert kicked == ["odn"]
    (kwargs,) = repo.inserted
    assert kwargs["kb_id"] == "kb-1"
    assert kwargs["execution_engine"] == "workflow"
    meta = kwargs["metadata_json"]
    assert meta["triggered_by"] == "mcp_upload"
    assert meta["force_redo"] is False
    assert meta["submitted_by_user_id"] == "u1"
    assert "document_ids" not in meta  # 整库模式：认领时才枚举文档


@pytest.mark.asyncio
async def test_auto_mine_merges_into_existing_queued_whole_kb_run(monkeypatch) -> None:
    repo = _Repo(queued_whole={"id": "existing", "status": "queued"})
    auto_mine = _patch(monkeypatch, lambda _pool: repo)
    state, kicked = _app_state(repo)

    out = await auto_mine.enqueue_auto_mining(
        app_state=state, kbdb=_KbDb(), kb=_KbDb().kb,
        user_id="u1", username="alice",
    )

    assert out == {
        "auto_mined": True, "run_id": "existing", "merged": True,
        "detail": "已并入该库排队中的挖掘任务",
    }
    assert repo.inserted == []  # 不重复入队
    assert kicked == ["odn"]  # 但仍 kick（重启后 queued 可能无人排水）


@pytest.mark.asyncio
async def test_auto_mine_degrades_without_paradigm(monkeypatch) -> None:
    repo = _Repo(queued_whole=None)
    auto_mine = _patch(monkeypatch, lambda _pool: repo)
    state, kicked = _app_state(repo)
    kb = {"id": "kb-1", "domain": "odn", "mining_workflow_id": None}

    out = await auto_mine.enqueue_auto_mining(
        app_state=state, kbdb=_KbDb(kb), kb=kb, user_id="u1", username="alice",
    )

    assert out == {"auto_mined": False, "reason": "kb_no_paradigm"}
    assert repo.inserted == [] and kicked == []


@pytest.mark.asyncio
async def test_auto_mine_degrades_when_binding_resolve_fails(monkeypatch) -> None:
    repo = _Repo(queued_whole=None)
    auto_mine = _patch(monkeypatch, lambda _pool: repo)
    state, kicked = _app_state(repo, resolve_error=RuntimeError("store down"))

    out = await auto_mine.enqueue_auto_mining(
        app_state=state, kbdb=_KbDb(), kb=_KbDb().kb,
        user_id="u1", username="alice",
    )

    assert out == {"auto_mined": False, "reason": "paradigm_unavailable"}
    assert repo.inserted == [] and kicked == []


@pytest.mark.asyncio
async def test_auto_mine_never_raises_unexpected_errors(monkeypatch) -> None:
    """仓储炸了也只降级 internal——上传结果不能被自动触发拖垮。"""
    class _BrokenRepo:
        def __init__(self, _pool):
            pass

        async def find_queued_whole_kb_run(self, _kb_id):
            raise RuntimeError("pool exhausted")

    auto_mine = _patch(monkeypatch, _BrokenRepo)
    state, _ = _app_state(None)

    out = await auto_mine.enqueue_auto_mining(
        app_state=state, kbdb=_KbDb(), kb=_KbDb().kb,
        user_id="u1", username="alice",
    )
    assert out == {"auto_mined": False, "reason": "internal"}


@pytest.mark.asyncio
async def test_auto_mine_unique_violation_falls_back_to_merge(monkeypatch) -> None:
    """未迁移 DB（旧索引仍约束 queued）的部署窗口兜底。"""
    from psycopg.errors import UniqueViolation

    repo = _Repo(
        queued_whole=[None, {"id": "latecomer", "status": "queued"}],
        insert_error=UniqueViolation(),
    )
    auto_mine = _patch(monkeypatch, lambda _pool: repo)
    state, kicked = _app_state(repo)

    out = await auto_mine.enqueue_auto_mining(
        app_state=state, kbdb=_KbDb(), kb=_KbDb().kb,
        user_id="u1", username="alice",
    )

    assert out["auto_mined"] is True
    assert out["run_id"] == "latecomer"
    assert kicked == ["odn"]


# ── MCP upload 端点：上传成功 + 自动入队 ───────────────────────────────────


class _McpKbDb:
    def __init__(self, kb):
        self.kb = kb

    async def get_user_by_username(self, _username):
        return {"id": "u1", "username": "alice"}

    async def is_visible(self, **_kwargs):
        return True

    async def can_write(self, **_kwargs):
        return True

    async def get_kb(self, _kb_id):
        return self.kb

    async def list_documents_in_kb(self, **_kwargs):
        return [{"id": "doc-1"}]


class _DocSvc:
    async def upload_stream(self, **_kwargs):
        return {"id": "doc-new", "document_name": "手册.md"}


def _mcp_app(monkeypatch, repo, kb):
    from fastapi import FastAPI
    from knowledge_mining.mining.kb.routes import mcp_tools

    _patch(monkeypatch, lambda _pool: repo)
    state, kicked = _app_state(repo)

    app = FastAPI()
    app.state.domain_pools = state.domain_pools
    app.state.workflow_run_binder = state.workflow_run_binder
    app.state.domain_run_dispatcher = state.domain_run_dispatcher
    app.dependency_overrides[mcp_tools.get_kb_db] = lambda: _McpKbDb(kb)
    app.dependency_overrides[mcp_tools.get_document_service] = lambda: _DocSvc()
    app.include_router(mcp_tools.router)
    return app, kicked


def _upload_body() -> dict:
    import base64
    return {
        "username": "alice", "kb_id": "kb-1",
        "filename": "手册.md",
        "content_b64": base64.b64encode("# 标题\n".encode("utf-8")).decode("ascii"),
    }


_HEADERS = {"X-Internal-Auth": "test-ivs"}


@pytest.mark.asyncio
async def test_mcp_upload_auto_enqueues_mining(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    repo = _Repo(queued_whole=None)
    app, kicked = _mcp_app(monkeypatch, repo, _KbDb().kb)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.post("/api/kb/mcp-tools/upload",
                                 json=_upload_body(), headers=_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["document_id"] == "doc-new"
    assert body["auto_mined"] is True
    assert body["run_id"]
    assert "已并入" not in body["message"] and "入队" in body["message"]
    assert len(repo.inserted) == 1
    assert repo.inserted[0]["kb_id"] == "kb-1"
    assert kicked == ["odn"]


@pytest.mark.asyncio
async def test_mcp_upload_survives_auto_mine_degradation(monkeypatch) -> None:
    """库没绑范式：上传仍 200，auto_mined=false + 原因透传。"""
    from httpx import ASGITransport, AsyncClient

    repo = _Repo(queued_whole=None)
    kb = {"id": "kb-1", "domain": "odn", "mining_workflow_id": None}
    app, kicked = _mcp_app(monkeypatch, repo, kb)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.post("/api/kb/mcp-tools/upload",
                                 json=_upload_body(), headers=_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["document_id"] == "doc-new"
    assert body["auto_mined"] is False
    assert body["reason"] == "kb_no_paradigm"
    assert "挖掘范式" in body["message"]
    assert repo.inserted == [] and kicked == []
