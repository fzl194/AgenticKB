"""P4.2 — /api/kb/{kb_id}/mine trigger (validation + 202 mechanics with stubbed pipeline).

完整 pipeline 端到端（真实 parse/segment/embedding）依赖 llm_service，超出单测范围；
这里 monkeypatch 掉 mining.jobs.run.run，只验证触发机制（权限/校验/run 行创建）。
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.kb.routes.documents import router as docs_router
from knowledge_mining.mining.kb.routes.kbs import router as kb_router
from knowledge_mining.mining.kb.routes.mining import router as kb_mining_router

pytestmark = pytest.mark.asyncio
DOMAIN = "cloud_core_network"


@pytest.fixture(scope="module")
def upload_root(tmp_path_factory):
    """把上传根指到 tmp 目录（覆盖控制面服务配置缓存的 upload.root）。"""
    from knowledge_mining.mining.infra.control_plane import override_upload_root
    p = tmp_path_factory.mktemp("kb_uploads_mine")
    override_upload_root(str(p))
    yield p


async def _client(async_pool):
    from types import SimpleNamespace
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig
    app = FastAPI()
    app.state.pg_pool = async_pool
    app.state.db_config = MiningDbConfig()

    # mine_kb 走 workflow 引擎，依赖 app.state.domain_pools + workflow_run_binder。
    # 单测里 stub：async_pool 返回真实 async_pool（让 insert_queued_run 真写 mining_runs），
    # binding resolve 返回带 manifest 的 fake binding（insert_queued_run 要求 binding 完整）。
    async def _async_pool(_domain):
        return async_pool

    async def _resolve_binding(**_kwargs):
        return SimpleNamespace(
            workflow_id="wf-test", workflow_version=1,
            workflow_version_id="wfv-test", graph_hash="graph-hash-test",
            manifest={"compiled_manifest": "stub"},
        )

    app.state.domain_pools = SimpleNamespace(
        async_pool=_async_pool, sync_pool=lambda _d: None,
    )
    app.state.workflow_run_binder = SimpleNamespace(resolve=_resolve_binding)

    app.include_router(kb_router)
    app.include_router(docs_router)
    app.include_router(kb_mining_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_kb_with_upload(c, headers, name="KBm"):
    kb_id = (await c.post("/api/kb", json={"domain": DOMAIN, "name": name}, headers=headers)).json()["id"]
    await c.post(f"/api/kb/{kb_id}/documents", files={"file": ("a.txt", b"hello")}, headers=headers)
    return kb_id


async def test_mine_not_found(async_pool, upload_root):
    async with await _client(async_pool) as c:
        r = await c.post("/api/kb/nope-kb/mine", headers={"X-KB-User": "alice"})
        assert r.status_code == 404


async def test_mine_other_user_private_404(async_pool, upload_root):
    async with await _client(async_pool) as c:
        h_a, h_b = {"X-KB-User": "alice"}, {"X-KB-User": "bob"}
        kb_id = await _make_kb_with_upload(c, h_a, name="priv-mine")
        r = await c.post(f"/api/kb/{kb_id}/mine", headers=h_b)
        assert r.status_code == 404  # bob 看不到 alice 的 private


async def test_mine_viewer_forbidden(async_pool, upload_root):
    async with await _client(async_pool) as c:
        h_a, h_b = {"X-KB-User": "alice"}, {"X-KB-User": "bob"}
        kb_id = await _make_kb_with_upload(c, h_a, name="shared-mine")
        await c.post("/api/kb", json={"domain": DOMAIN, "name": "tmp"}, headers=h_b)  # upsert bob
        await c.post(f"/api/kb/{kb_id}/members", json={"username": "bob", "role": "viewer"}, headers=h_a)
        r = await c.post(f"/api/kb/{kb_id}/mine", headers=h_b)
        assert r.status_code == 403  # viewer 不能触发挖掘


async def test_mine_no_uploads_400(async_pool, upload_root):
    async with await _client(async_pool) as c:
        h = {"X-KB-User": "alice"}
        kb_id = (await c.post("/api/kb", json={"domain": DOMAIN, "name": "empty-kb"}, headers=h)).json()["id"]
        r = await c.post(f"/api/kb/{kb_id}/mine", headers=h)
        assert r.status_code == 400


async def test_mine_owner_202_creates_run_row(async_pool, upload_root, monkeypatch):
    # stub 掉真实 pipeline，避免依赖 llm_service
    def _stub_run(*args, **kwargs):
        return None
    monkeypatch.setattr("knowledge_mining.mining.jobs.run.run", _stub_run)
    # mine_kb 调 resolve_domain 取 channel；stub 掉避免依赖 domain registry
    monkeypatch.setattr(
        "knowledge_mining.mining.kb.routes.mining.resolve_domain",
        lambda _d: {"default_channel": "prod"},
    )

    async with await _client(async_pool) as c:
        h = {"X-KB-User": "alice"}
        kb_id = await _make_kb_with_upload(c, h, name="ok-mine")
        # KB 必须先选挖掘范式（mine_kb 校验 mining_workflow_id 非空，否则 400）
        pr = await c.patch(f"/api/kb/{kb_id}", json={"mining_workflow_id": "wf-test"}, headers=h)
        assert pr.status_code == 200, pr.text
        r = await c.post(f"/api/kb/{kb_id}/mine", headers=h)
        assert r.status_code == 202, r.text
        body = r.json()
        run_id = body["run_id"]
        assert body["kb_id"] == kb_id
        assert body["execution_engine"] == "workflow"

        async with async_pool.connection() as conn:
            cur = await conn.execute(
                "SELECT domain, kb_id, execution_engine, metadata_json "
                "FROM mining_runs WHERE id = %s",
                [run_id],
            )
            row = await cur.fetchone()
        assert row is not None
        assert row["domain"] == DOMAIN
        assert row["kb_id"] == kb_id
        assert row["execution_engine"] == "workflow"
        meta = row["metadata_json"] or {}
        assert meta.get("kb_id") == kb_id
        assert meta.get("publish") is False  # KB 挖掘抑制 publish（B1 对冲）

        # 等 stub 线程释放域锁，避免污染后续测试
        await asyncio.sleep(0.3)


async def test_mine_force_redo_propagates_to_metadata(async_pool, upload_root, monkeypatch):
    """档1：force_redo=True 经 body → mining_runs.metadata_json.force_redo 透传给 pipeline。"""
    monkeypatch.setattr("knowledge_mining.mining.jobs.run.run", lambda *a, **k: None)
    monkeypatch.setattr(
        "knowledge_mining.mining.kb.routes.mining.resolve_domain",
        lambda _d: {"default_channel": "prod"},
    )
    async with await _client(async_pool) as c:
        h = {"X-KB-User": "alice"}
        kb_id = await _make_kb_with_upload(c, h, name="force-mine")
        await c.patch(f"/api/kb/{kb_id}", json={"mining_workflow_id": "wf-test"}, headers=h)
        r = await c.post(f"/api/kb/{kb_id}/mine", json={"force_redo": True}, headers=h)
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["force_redo"] is True
        async with async_pool.connection() as conn:
            cur = await conn.execute(
                "SELECT metadata_json FROM mining_runs WHERE id = %s", [body["run_id"]]
            )
            meta = (await cur.fetchone())["metadata_json"] or {}
        assert meta.get("force_redo") is True
        assert meta.get("signature") == "wf-test:1:graph-hash-test"
        await asyncio.sleep(0.3)


async def test_mine_auto_force_redo_on_signature_change(async_pool, upload_root, monkeypatch):
    """档2：上一条已完成 run 的范式签名与本次不同 → 自动 force_redo（无需用户显式指定）。"""
    monkeypatch.setattr("knowledge_mining.mining.jobs.run.run", lambda *a, **k: None)
    monkeypatch.setattr(
        "knowledge_mining.mining.kb.routes.mining.resolve_domain",
        lambda _d: {"default_channel": "prod"},
    )
    async with await _client(async_pool) as c:
        h = {"X-KB-User": "alice"}
        kb_id = await _make_kb_with_upload(c, h, name="sig-mine")
        await c.patch(f"/api/kb/{kb_id}", json={"mining_workflow_id": "wf-test"}, headers=h)
        # 伪造一条已完成 run，签名与本次(stub: wf-test:1:graph-hash-test)不同
        async with async_pool.connection() as conn:
            await conn.execute(
                "INSERT INTO mining_runs (id, kb_id, input_path, domain, channel, status, "
                "current_stage, started_at, finished_at, execution_engine, metadata_json) "
                "VALUES (%s, %s, %s, %s, 'prod', 'completed', 'done', %s, %s, 'workflow', %s::jsonb)",
                (
                    "run-old-sig", kb_id, f"/tmp/{kb_id}", DOMAIN,
                    "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00",
                    json.dumps({"kb_id": kb_id, "signature": "wf-test:1:OLDHASH"}),
                ),
            )
        r = await c.post(f"/api/kb/{kb_id}/mine", headers=h)
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["auto_force_redo"] is True
        assert body["force_redo"] is True
        await asyncio.sleep(0.3)
