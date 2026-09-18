"""Mining API — FastAPI application factory.

Start:
    python -m knowledge_mining.mining.api
    # or
    uvicorn knowledge_mining.mining.api.app:create_app --host 0.0.0.0 --port 8901 --factory
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from knowledge_mining.mining.api.domain_pools import DomainPoolManager
from knowledge_mining.mining.infra.pg_config import MiningDbConfig
from knowledge_mining.mining.infra.pg_schema import assert_schema_contract
from knowledge_mining.mining.infra.mining_config import MiningConfig
from knowledge_mining.mining.api.routes.health import router as health_router
from knowledge_mining.mining.api.routes.runs import router as runs_router
from knowledge_mining.mining.api.routes.knowledge import router as knowledge_router
from knowledge_mining.mining.api.routes.workflows import router as workflows_router
from knowledge_mining.mining.api.routes.ops import router as ops_router
from knowledge_mining.mining.kb.routes.kbs import router as kb_router
from knowledge_mining.mining.kb.routes.documents import router as kb_documents_router
from knowledge_mining.mining.kb.routes.mining import router as kb_mining_router
from knowledge_mining.mining.kb.routes.folders import router as kb_folders_router
from knowledge_mining.mining.kb.routes.auth import router as kb_auth_router
from knowledge_mining.mining.kb.routes.mcp_keys import router as kb_mcp_keys_router
from knowledge_mining.mining.kb.routes.mcp_tools import router as kb_mcp_tools_router
from knowledge_mining.mining.kb.routes.overview import router as kb_overview_router
from knowledge_mining.mining.onenet.routes import (
    refs_router as onenet_refs_router,
    router as onenet_router,
)
from knowledge_mining.mining.workflow.repositories.global_workflow_repository import (
    GlobalWorkflowRepository,
)
from knowledge_mining.mining.workflow.service import WorkflowService
from knowledge_mining.mining.workflow.manifest import value_hash
from knowledge_mining.mining.workflow.run_binding import WorkflowRunBinder
from knowledge_mining.mining.api.startup_recovery import (
    recover_startup_runs,
    schedule_lease_expiry_sweep,
)
from knowledge_mining.mining.api.domain_run_queue import build_domain_run_dispatcher

logger = logging.getLogger(__name__)


def _cors_origins() -> list[str]:
    origins = ["http://localhost:8080", "http://127.0.0.1:8080"]
    for origin in origins:
        parsed = urlsplit(origin)
        if origin == "*":
            raise ValueError("CMKB_CORS_ORIGINS cannot contain wildcard origins")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"CMKB_CORS_ORIGINS contains invalid origin: {origin!r}")
    return origins


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize PostgreSQL pool after validating the deployed schema version."""
    # 从主控制服务拉取全部配置并缓存（仿 llm_service）：mining.yaml + database.yaml。
    # 之后 MiningDbConfig/MiningConfig/UploadConfig 无参构造直接读缓存，不再读 .env。
    from knowledge_mining.mining.infra.control_plane import (
        fetch_database_config,
        fetch_mining_service_config,
    )

    fetch_mining_service_config(force=True)
    fetch_database_config(force=True)

    cfg = MiningDbConfig()

    # Production startup is read-only. DDL belongs to deploy-sync's migration step.
    assert_schema_contract(cfg)

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

    # M1：拉 storage.yaml（对象存储配置），best-effort——控制面不可达不阻断启动。
    # mining 现有链路不依赖对象存储；ObjectStorePort 使用方经
    # ObjectStoreConfig.from_control_plane() 自行读缓存。
    from knowledge_mining.mining.infra.control_plane import fetch_storage_config
    try:
        fetch_storage_config(force=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("storage config fetch skipped: %s", exc)

    # KB uploads are object-store-only.  Build one adapter at the composition
    # root so request handlers cannot silently fall back to local disk.
    from knowledge_mining.mining.infra.object_store.config import ObjectStoreConfig
    from knowledge_mining.mining.infra.object_store.factory import make_object_store

    object_store_config = ObjectStoreConfig.from_control_plane()
    app.state.object_store = make_object_store(object_store_config)
    app.state.object_store_config = object_store_config


    workflow_repo = GlobalWorkflowRepository(pool)
    app.state.workflow_service = WorkflowService(workflow_repo)
    upgraded_workflows = await app.state.workflow_service.upgrade_active_workflows_to_v2()
    if upgraded_workflows:
        logger.info(
            "Upgraded %d persisted workflow(s) to v2: %s",
            len(upgraded_workflows), ", ".join(upgraded_workflows),
        )
    await app.state.workflow_service.ensure_workflow_library()

    # 快照废弃轨道（2026-09-16 删除体系）：每日一轮 DEPRECATED 标记 + 满期回收。
    # 首轮启动即跑（存量僵尸清理），之后每 24h 一轮；失败只记日志不影响服务。
    async def _snapshot_gc_loop() -> None:
        from knowledge_mining.mining.kb.services.purge_service import PurgeService
        while True:
            try:
                svc = PurgeService(pool, app.state.object_store)
                marked = await svc.deprecate_superseded_snapshots()
                reclaimed = await svc.reclaim_deprecated_snapshots()
                if marked["deprecated"] or reclaimed["reclaimed"]:
                    logger.info("[gc] daily: %s", {**marked, **reclaimed})
            except Exception:  # noqa: BLE001
                logger.warning("[gc] snapshot gc round failed", exc_info=True)
            await asyncio.sleep(24 * 3600)

    gc_task = asyncio.create_task(_snapshot_gc_loop())
    app.state.snapshot_gc_task = gc_task

    # 删除任务重启恢复（2026-09-16 二期）：queued/running 的后台硬删重新
    # 入队——管线幂等，半途状态重跑干净。
    try:
        from knowledge_mining.mining.kb.services.purge_tasks import (
            recover_purge_tasks,
        )
        recovered = await recover_purge_tasks(pool, app.state.object_store)
        if recovered:
            logger.info("[purge-task] recovered %s task(s) at startup",
                        recovered)
    except Exception:  # noqa: BLE001 - 恢复失败不阻断启动
        logger.warning("[purge-task] startup recovery failed", exc_info=True)

    # Domain-specific async/sync pools are opened lazily by API dependencies.
    app.state.domain_pools = DomainPoolManager(cfg)
    app.state.domain_run_dispatcher = build_domain_run_dispatcher(
        app.state.domain_pools, cfg,
    )

    from knowledge_mining.mining.infra.domain_pack import load_domain_registry
    from knowledge_mining.mining.api.routes.runs import _utcnow

    registry = load_domain_registry()
    enabled_domains = tuple(
        domain_id for domain_id, entry in (registry.get("domains") or {}).items()
        if isinstance(entry, dict) and entry.get("enabled", True)
    )

    async def _wake_domain_queue(_run_id: str, domain: str) -> None:
        app.state.domain_run_dispatcher.kick(domain)

    recovery = await recover_startup_runs(
        domain_ids=enabled_domains,
        domain_pools=app.state.domain_pools,
        now=_utcnow(),
    )
    if recovery.interrupted_run_ids:
        logger.warning("Startup recovery interrupted %d abandoned run(s)", len(recovery.interrupted_run_ids))
    # P07-S2：重启瞬间仍持有效租约的 Run，等租约到期后补一轮标记 + 恢复。
    schedule_lease_expiry_sweep(
        domain_ids=enabled_domains,
        domain_pools=app.state.domain_pools,
        resume_workflow=_wake_domain_queue,
    )

    async def _active_ontology_id(domain: str) -> str | None:
        # Ontology is retired from the production data plane.  Keep the
        # binding callback temporarily so older frozen workflow manifests can
        # still be opened without querying a table that no longer exists.
        del domain
        return None

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

    # queued rows survive restarts; interrupted rows are resumed by the same
    # one-domain-at-a-time dispatcher before ordinary FIFO work.
    for domain in enabled_domains:
        app.state.domain_run_dispatcher.kick(domain)

    logger.info("Mining API started — PostgreSQL %s:%d/%s", cfg.pg_host, cfg.pg_port, cfg.pg_dbname)

    yield

    gc_task.cancel()
    await asyncio.gather(gc_task, return_exceptions=True)

    app.state.domain_run_dispatcher.close()
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
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.include_router(health_router)
    app.include_router(runs_router)
    app.include_router(knowledge_router)
    # /api/ops/* —— 运维使用分析（admin-only）。独立 prefix，不与 /api/kb 的动态段相争。
    app.include_router(ops_router)
    # kb_auth_router / kb_overview_router 必须在 kb_router 之前注册：它们的静态路由
    # （/api/kb/users、/api/kb/auth/verify、/api/kb/overview）否则会被 kb_router 的动态
    # /api/kb/{kb_id} 抢先匹配（GET /api/kb/users 被当成 kb_id="users" → 404）。
    # 任何新增的 /api/kb/<字面量> 路由都得挂在这一段里，别追加到 kbs.py 末尾。
    app.include_router(kb_auth_router)
    app.include_router(kb_mcp_keys_router)
    app.include_router(kb_mcp_tools_router)
    app.include_router(kb_overview_router)
    # onenet：管理面（/api/onenet/*）+ KB 引用（静态前缀 /api/kb/{kb_id}/onenet，
    # 在 kb_router 之后的 documents/folders 同段注册即可——其路径含动态 kb_id，
    # 不与 /api/kb/<字面量> 静态路由冲突）。
    app.include_router(onenet_router)
    app.include_router(onenet_refs_router)
    app.include_router(kb_router)
    app.include_router(kb_documents_router)
    app.include_router(kb_mining_router)
    app.include_router(kb_folders_router)
    app.include_router(workflows_router)

    # Allow cross-origin requests from the dev server and any local UI.
    from knowledge_mining.mining.api.auth_guard import MiningApiAuthMiddleware

    app.add_middleware(MiningApiAuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
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
