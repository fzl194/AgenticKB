"""Mining API — FastAPI application factory.

Start:
    python -m knowledge_mining.mining.api
    # or
    uvicorn knowledge_mining.mining.api.app:create_app --host 0.0.0.0 --port 8901 --factory
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from knowledge_mining.mining.api.domain_pools import DomainPoolManager
from knowledge_mining.mining.infra.pg_config import MiningDbConfig
from knowledge_mining.mining.infra.pg_schema import ensure_primary_schema
from knowledge_mining.mining.infra.mining_config import MiningConfig
from knowledge_mining.mining.api.routes.health import router as health_router
from knowledge_mining.mining.api.routes.runs import router as runs_router
from knowledge_mining.mining.api.routes.knowledge import router as knowledge_router
from knowledge_mining.mining.api.routes.config import router as config_router
from knowledge_mining.mining.api.routes.builds import router as builds_router
from knowledge_mining.mining.api.routes.uploads import router as uploads_router
from knowledge_mining.mining.api.routes.ontology import router as ontology_router
from knowledge_mining.mining.api.routes.workflows import router as workflows_router
from knowledge_mining.mining.api.routes.ops import router as ops_router
from knowledge_mining.mining.api.routes.document_lifecycle import (
    router as document_lifecycle_router,
)
from knowledge_mining.mining.kb.routes.kbs import router as kb_router
from knowledge_mining.mining.kb.routes.documents import router as kb_documents_router
from knowledge_mining.mining.kb.routes.mining import router as kb_mining_router
from knowledge_mining.mining.kb.routes.folders import router as kb_folders_router
from knowledge_mining.mining.kb.routes.auth import router as kb_auth_router
from knowledge_mining.mining.kb.routes.overview import router as kb_overview_router
from knowledge_mining.mining.workflow.repositories.global_workflow_repository import (
    GlobalWorkflowRepository,
)
from knowledge_mining.mining.workflow.service import WorkflowService
from knowledge_mining.mining.workflow.manifest import value_hash
from knowledge_mining.mining.workflow.run_binding import WorkflowRunBinder

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize PostgreSQL pool and ensure schema exists."""
    # 从主控制服务拉取全部配置并缓存（仿 llm_service）：mining.yaml + database.yaml。
    # 之后 MiningDbConfig/MiningConfig/UploadConfig 无参构造直接读缓存，不再读 .env。
    from knowledge_mining.mining.infra.control_plane import (
        fetch_database_config,
        fetch_mining_service_config,
    )

    fetch_mining_service_config(force=True)
    fetch_database_config(force=True)

    cfg = MiningDbConfig()

    # Ensure database + schema (sync, runs once at startup)
    ensure_primary_schema(cfg)

    pool = AsyncConnectionPool(
        cfg.conninfo,
        min_size=cfg.pg_pool_min,
        max_size=cfg.pg_pool_max,
        open=False,
        # Validate connections before handing them out so stale/closed
        # connections (e.g. remote PG idle-timeout or restart) are discarded
        # and replaced transparently instead of surfacing as 500s.
        check=AsyncConnectionPool.check_connection,
        max_idle=300.0,
        kwargs={"row_factory": dict_row},
    )
    await pool.open()
    app.state.pg_pool = pool
    app.state.db_config = cfg

    # Phase 2：拉 auth.yaml 并幂等播种首 admin（best-effort，控制面不可达不阻断启动）。
    from knowledge_mining.mining.infra.control_plane import fetch_auth_config
    from knowledge_mining.mining.kb.bootstrap import seed_initial_admin
    try:
        _auth_cfg = fetch_auth_config(force=True)
        _admin_pw = (_auth_cfg.get("bootstrap") or {}).get("admin_password") or ""
        await seed_initial_admin(pool, admin_password=_admin_pw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth bootstrap skipped: %s", exc)

    workflow_repo = GlobalWorkflowRepository(pool)
    app.state.workflow_service = WorkflowService(workflow_repo)
    await app.state.workflow_service.ensure_workflow_library()

    # Domain-specific async/sync pools are opened lazily by API dependencies.
    app.state.domain_pools = DomainPoolManager(cfg)

    async def _active_ontology_id(domain: str) -> str | None:
        domain_pool = await app.state.domain_pools.async_pool(domain)
        async with domain_pool.connection() as conn:
            cursor = await conn.execute(
                """SELECT id FROM ontology_versions
                   WHERE domain_id = %s AND status = 'active' LIMIT 1""",
                (domain,),
            )
            row = await cursor.fetchone()
            return row["id"] if row else None

    pipeline_cfg = MiningConfig()
    llm_url = urlsplit(pipeline_cfg.llm_service_url)
    safe_config_fingerprint = value_hash({
        "maxWorkers": pipeline_cfg.max_workers,
        "llmServiceHost": llm_url.hostname,
        "llmServicePort": llm_url.port,
        "domainProfileSource": "domain-registry",
        "embeddingProvider": "llm-service",
    })
    app.state.workflow_run_binder = WorkflowRunBinder(
        app.state.workflow_service,
        _active_ontology_id,
        lambda: safe_config_fingerprint,
    )

    # 预热挖掘管线重模块（jobs.run 拉起整条 parse/segment/embedding/...，冷导入实测约 2.5s）：
    # 否则每个挖掘请求的后台线程首次冷导入会占 GIL，卡住 202 响应数秒——这正是"点挖掘要等
    # 好几秒才弹进入队列"的根因。放启动期一次性付，请求期 import 命中缓存→瞬完。
    import knowledge_mining.mining.jobs.run  # noqa: F401  (warm sys.modules cache)

    logger.info("Mining API started — PostgreSQL %s:%d/%s", cfg.pg_host, cfg.pg_port, cfg.pg_dbname)

    yield

    await app.state.domain_pools.close()
    await pool.close()
    logger.info("Mining API stopped")


def create_app() -> FastAPI:
    """Application factory for uvicorn --factory."""
    app = FastAPI(
        title="Mining API",
        version="3.0.0",
        description="Knowledge Mining Pipeline — REST API for triggering mining runs, "
                     "querying knowledge assets, and managing builds/releases.",
        lifespan=lifespan,
    )

    app.include_router(health_router)
    app.include_router(runs_router)
    app.include_router(knowledge_router)
    app.include_router(config_router)
    app.include_router(builds_router)
    app.include_router(uploads_router)
    app.include_router(ontology_router)
    app.include_router(document_lifecycle_router)
    # /api/ops/* —— 运维使用分析（admin-only）。独立 prefix，不与 /api/kb 的动态段相争。
    app.include_router(ops_router)
    # kb_auth_router / kb_overview_router 必须在 kb_router 之前注册：它们的静态路由
    # （/api/kb/users、/api/kb/auth/verify、/api/kb/overview）否则会被 kb_router 的动态
    # /api/kb/{kb_id} 抢先匹配（GET /api/kb/users 被当成 kb_id="users" → 404）。
    # 任何新增的 /api/kb/<字面量> 路由都得挂在这一段里，别追加到 kbs.py 末尾。
    app.include_router(kb_auth_router)
    app.include_router(kb_overview_router)
    app.include_router(kb_router)
    app.include_router(kb_documents_router)
    app.include_router(kb_mining_router)
    app.include_router(kb_folders_router)
    app.include_router(workflows_router)

    # Allow cross-origin requests from the dev server and any local UI.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return app


# Module-level app for `python -m knowledge_mining.mining.api`
app = create_app()


if __name__ == "__main__":
    import uvicorn
    from knowledge_mining.mining.infra.control_plane import fetch_mining_service_config
    from knowledge_mining.mining.infra.mining_config import MiningConfig

    fetch_mining_service_config()
    port = MiningConfig().port
    uvicorn.run(
        "knowledge_mining.mining.api.app:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
