"""同库自动挖掘排队（010 号迁移）——真实 PG 验收。

门禁：``KB_RUN_POSTGRES_ACCEPTANCE=1`` + ``*_test`` 库（经 async_pool/db_config 夹具）。
覆盖四个 DB 层事实：
1. 唯一索引收窄后，同库允许多条 queued（旧索引会拒）；
2. 活跃状态（running）仍同库互斥；
3. 调度守卫 SQL 只认领"本库无活跃 Run"的 queued Run；
4. MCP upload 端到端：上传→自动入队→二次上传合并→手动 /mine 仍 409。
"""
from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

DOMAIN = "cloud_core_network"
INTERNAL_HEADERS = {"X-Internal-Auth": "test-ivs"}


@pytest.fixture(scope="module")
def upload_root(tmp_path_factory):
    from knowledge_mining.mining.infra.control_plane import override_upload_root

    p = tmp_path_factory.mktemp("kb_uploads_auto_queue")
    override_upload_root(str(p))
    yield p


@pytest.fixture(autouse=True)
def _patch_domain(monkeypatch):
    """两个 resolve_domain（auto_mine 服务 + 手动 mine 路由）都不依赖域注册表。"""
    monkeypatch.setattr(
        "knowledge_mining.mining.kb.services.auto_mine.resolve_domain",
        lambda _d: {"default_channel": "prod"},
    )
    monkeypatch.setattr(
        "knowledge_mining.mining.kb.routes.mining.resolve_domain",
        lambda _d: {"default_channel": "prod"},
    )


async def _client(async_pool):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig
    from knowledge_mining.mining.kb.routes.documents import router as docs_router
    from knowledge_mining.mining.kb.routes.kbs import router as kb_router
    from knowledge_mining.mining.kb.routes.mining import router as kb_mining_router
    from knowledge_mining.mining.kb.routes.mcp_tools import router as mcp_router
    from knowledge_mining.tests.kb.conftest import attach_object_store

    app = FastAPI()
    app.state.pg_pool = async_pool
    attach_object_store(app)
    app.state.db_config = MiningDbConfig()

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
    app.state.domain_run_dispatcher = SimpleNamespace(kick=lambda _domain: None)

    app.include_router(kb_router)
    app.include_router(docs_router)
    app.include_router(kb_mining_router)
    app.include_router(mcp_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _mcp_upload(c, kb_id: str, filename: str) -> dict:
    """两步直传：begin-upload 拿票据 → PUT 原始字节 → 最终结果。"""
    begin = await c.post("/api/kb/mcp-tools/begin-upload", headers=INTERNAL_HEADERS, json={
        "username": "auto-queue-owner", "kb_id": kb_id, "filename": filename,
    })
    assert begin.status_code == 200, begin.text
    ticket = begin.json()["ticket"]
    resp = await c.put(
        f"/api/kb/mcp-tools/upload-direct/{ticket}",
        headers={**INTERNAL_HEADERS, "X-MCP-Username": "auto-queue-owner"},
        content=f"# {filename}\n内容".encode("utf-8"),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _queued_runs(async_pool, kb_id: str) -> list[dict]:
    async with async_pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT id, status, metadata_json FROM mining_runs "
            "WHERE kb_id = %s ORDER BY started_at, id",
            (kb_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]


@pytest.mark.asyncio
async def test_mcp_upload_queues_then_merges_and_manual_still_409(
    async_pool, upload_root,
):
    from knowledge_mining.tests.conftest import kb_headers

    async with await _client(async_pool) as c:
        headers = kb_headers("auto-queue-owner")
        created = await c.post(
            "/api/kb", json={"domain": DOMAIN, "name": f"auto-queue-e2e-{uuid.uuid4().hex[:6]}"}, headers=headers,
        )
        assert created.status_code == 201, created.text
        kb_id = created.json()["id"]
        await c.patch(
            f"/api/kb/{kb_id}", json={"mining_workflow_id": "wf-test"}, headers=headers,
        )

        first = await _mcp_upload(c, kb_id, "first.md")
        assert first["auto_mined"] is True, first
        assert first["run_id"]

        second = await _mcp_upload(c, kb_id, "second.md")
        assert second["auto_mined"] is True
        assert second["run_id"] == first["run_id"]  # 合并：同一条排队 Run

        rows = await _queued_runs(async_pool, kb_id)
        assert [r["status"] for r in rows] == ["queued"]  # 只有一条
        assert rows[0]["metadata_json"]["triggered_by"] == "mcp_upload"
        assert "document_ids" not in rows[0]["metadata_json"]  # 整库模式

        # 手动触发语义不变：看到 queued 仍 409
        manual = await c.post(f"/api/kb/{kb_id}/mine", headers=headers)
        assert manual.status_code == 409
        assert manual.json()["detail"]["code"] == "kb_mining_busy"


@pytest.mark.asyncio
async def test_upload_without_paradigm_degrades_but_succeeds(async_pool, upload_root):
    """新建库默认绑 system-hybrid-assets（db.py create_kb）；显式清空才走降级分支。"""
    from knowledge_mining.tests.conftest import kb_headers

    async with await _client(async_pool) as c:
        headers = kb_headers("auto-queue-owner")
        created = await c.post(
            "/api/kb", json={"domain": DOMAIN, "name": f"auto-queue-noparadigm-{uuid.uuid4().hex[:6]}"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        kb_id = created.json()["id"]
        cleared = await c.patch(
            f"/api/kb/{kb_id}", json={"mining_workflow_id": None}, headers=headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["mining_workflow_id"] is None

        body = await _mcp_upload(c, kb_id, "plain.md")
        assert body["document_id"]
        assert body["auto_mined"] is False
        assert body["reason"] == "kb_no_paradigm"
        assert await _queued_runs(async_pool, kb_id) == []


def _insert_run(conn, kb_id: str, status: str) -> str:
    run_id = f"run-{status}-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "INSERT INTO mining_runs (id, kb_id, input_path, domain, channel, status, "
        "current_stage, started_at, execution_engine, metadata_json) "
        "VALUES (%s, %s, %s, %s, 'prod', %s, 'queued', NOW(), 'workflow', '{}'::jsonb)",
        (run_id, kb_id, f"/tmp/{run_id}", DOMAIN, status),
    )
    return run_id


def test_same_kb_index_and_dispatcher_guard_semantics(db_config):
    """纯 SQL 层：queued 可与 running 共存、同库第二个活跃被拒、守卫只放行空闲库。"""
    from psycopg_pool import ConnectionPool
    from knowledge_mining.mining.api.domain_run_queue import _NEXT_CANDIDATE_SQL

    kb_busy = f"kb-guard-busy-{uuid.uuid4().hex[:8]}"
    kb_free = f"kb-guard-free-{uuid.uuid4().hex[:8]}"
    pool = ConnectionPool(
        db_config.conninfo, min_size=1, max_size=1,
        kwargs={"row_factory": dict_row}, open=True,
    )
    try:
        with pool.connection() as conn:
            _insert_run(conn, kb_busy, "running")
            _insert_run(conn, kb_busy, "queued")  # 旧索引下这一步就会撞
            _insert_run(conn, kb_free, "queued")

            # 共存成立：busy 库同时有 running + queued
            count = conn.execute(
                "SELECT count(*) AS n FROM mining_runs WHERE kb_id = %s "
                "AND status = 'queued'", (kb_busy,),
            ).fetchone()["n"]
            assert count == 1

            # 同库第二个活跃被新索引拒绝（savepoint 回滚，queued 保留）
            with pytest.raises(psycopg.errors.UniqueViolation):
                with conn.transaction():
                    conn.execute(
                        "UPDATE mining_runs SET status = 'running' "
                        "WHERE kb_id = %s AND status = 'queued'", (kb_busy,),
                    )

            # 守卫：去掉 LIMIT 1 做集合断言（域内可能有其他测试遗留的排队行）
            all_candidates_sql = _NEXT_CANDIDATE_SQL.replace("LIMIT 1", "")
            rows = conn.execute(all_candidates_sql, (DOMAIN,)).fetchall()
            candidate_kbs = {row["kb_id"] for row in rows}
            assert kb_free in candidate_kbs
            assert kb_busy not in candidate_kbs
    finally:
        with pool.connection() as conn:
            conn.execute(
                "DELETE FROM mining_runs WHERE kb_id = ANY(%s)",
                ([kb_busy, kb_free],),
            )
        pool.close()
