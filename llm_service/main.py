from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from llm_service.config import load_llm_config, dig, resolve_active_model_config
from llm_service.db import LlmRuntimeDB
from llm_service.pg_config import load_db_config
from llm_service.pg_schema import ensure_schema
from llm_service.providers.bigmodel_models import BigModelProvider
from llm_service.providers.model_base import ModelProviderProtocol
from llm_service.providers.base import ProviderProtocol
from llm_service.providers.anthropic import AnthropicProvider
from llm_service.providers.openai_compatible import OpenAICompatibleProvider
from llm_service.runtime.model_service import ModelService
from llm_service.runtime.persist_writer import PersistWriter
from llm_service.runtime.service import LLMService
from llm_service.runtime.worker import LeaseRecovery, Worker

logger = logging.getLogger(__name__)

# Module-level config set by __main__ before uvicorn forks workers.
_STARTUP_CONFIG: dict | None = None


def set_startup_config(cfg: dict) -> None:
    """Store config from __main__ so the uvicorn factory can reuse it."""
    global _STARTUP_CONFIG
    _STARTUP_CONFIG = cfg


def create_app_from_startup_config() -> FastAPI:
    """Uvicorn factory — reuses config loaded by __main__ (no double-fetch)."""
    if _STARTUP_CONFIG is None:
        raise RuntimeError("set_startup_config() must be called before create_app_from_startup_config()")
    return create_app(config=_STARTUP_CONFIG)


def create_app(
    config: dict | None = None,
    provider_factory: Callable[[], ProviderProtocol] | None = None,
    model_provider_factory: Callable[[], ModelProviderProtocol] | None = None,
    *,
    start_worker: bool = True,
) -> FastAPI:
    cfg = config or load_llm_config()
    _factory = provider_factory

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(
            "llm_lifespan_start pid=%s app_id=%s config_id=%s start_worker=%s",
            os.getpid(),
            id(app),
            id(cfg),
            start_worker,
        )
        # PostgreSQL — all params from control plane database.yaml
        pg_cfg = load_db_config()
        logger.info("Ensuring database schema for %s @ %s:%s", pg_cfg.dbname, pg_cfg.host, pg_cfg.port)
        ensure_schema(pg_cfg)

        db = LlmRuntimeDB.from_conninfo(
            pg_cfg.conninfo,
            pool_min=pg_cfg.pool_min,
            pool_max=dig(cfg, "pool_max") or pg_cfg.pool_max,
        )
        await db.open()

        health = await db.health_check()
        if not health.get("connected"):
            await db.close()
            raise RuntimeError(f"Database health check failed: {health.get('error', 'cannot connect')}")
        if not health.get("tables_ok"):
            logger.warning("Database tables check: %s", health)
        logger.info(
            "Database health check passed (connected=%s, tables=%s, tasks=%s)",
            health["connected"], health["tables_ok"], health.get("task_count", "?"),
        )

        # Startup cleanup: re-queue stale "running" tasks from previous crash
        try:
            row = await db.fetchone(
                "SELECT COUNT(*) AS cnt FROM agent_llm_tasks WHERE status = 'running'"
            )
            stale = row["cnt"] if row else 0
            if stale > 0:
                await db.execute(
                    "UPDATE agent_llm_tasks SET status = 'queued', lease_expires_at = NULL "
                    "WHERE status = 'running'"
                )
                logger.warning("Re-queued %d stale 'running' tasks on startup", stale)
        except Exception:
            logger.exception("Failed to re-queue stale running tasks on startup")

        # Providers — all params from config dict, no defaults
        # Resolve multi-model: if provider.models exists, merge active_model overrides
        active_provider = resolve_active_model_config(cfg)
        # Select provider implementation based on provider_type
        provider_type = active_provider.get("provider_type", "openai_compatible")
        if _factory:
            provider = _factory()
        elif provider_type == "anthropic":
            provider = AnthropicProvider(
                api_key=active_provider["api_key"],
                model=active_provider.get("model", active_provider.get("active_model", "")),
                base_url=active_provider.get("base_url", "https://api.anthropic.com/v1/messages"),
                api_version=active_provider.get("api_version", "2023-06-01"),
                headers=active_provider.get("headers") or {},
                timeout=active_provider.get("timeout", 60),
                bypass_proxy=active_provider.get("bypass_proxy", False),
            )
        else:
            provider = OpenAICompatibleProvider(
                base_url=active_provider["base_url"],
                api_key=active_provider["api_key"],
                model=active_provider.get("model", active_provider.get("active_model", "")),
                headers=active_provider.get("headers") or {},
                timeout=active_provider.get("timeout", 30),
                bypass_proxy=active_provider.get("bypass_proxy", False),
            )
        model_provider = (
            model_provider_factory()
            if model_provider_factory
            else BigModelProvider(
                embedding_api_key=dig(cfg, "embedding", "api_key"),
                embedding_url=dig(cfg, "embedding", "base_url"),
                embedding_model=dig(cfg, "embedding", "model"),
                embedding_timeout=dig(cfg, "embedding", "timeout"),
                embedding_bypass_proxy=dig(cfg, "embedding", "bypass_proxy"),
                embedding_headers=dig(cfg, "embedding", "headers") or {},
                rerank_api_key=dig(cfg, "rerank", "api_key"),
                rerank_url=dig(cfg, "rerank", "base_url"),
                rerank_model=dig(cfg, "rerank", "model"),
                rerank_timeout=dig(cfg, "rerank", "timeout"),
                rerank_bypass_proxy=dig(cfg, "rerank", "bypass_proxy"),
                rerank_headers=dig(cfg, "rerank", "headers") or {},
            )
        )

        # Template registry with configurable cache TTL
        from llm_service.runtime.template_registry import TemplateRegistry
        templates = TemplateRegistry(db=db, cache_ttl=dig(cfg, "template", "cache_ttl"))

        # Embedding dimensions: optional in YAML, None means don't pass to provider
        _embedding_dimensions = cfg.get("embedding", {}).get("dimensions")

        # PersistWriter — decouples sync-call DB writes from request hot path
        pw_cfg = dig(cfg, "persist_writer") or {}
        persist_writer = PersistWriter(
            db=db,
            queue_size=pw_cfg.get("queue_size", 10000),
            batch_size=pw_cfg.get("batch_size", 20),
            flush_interval=pw_cfg.get("flush_interval", 0.5),
            writer_count=pw_cfg.get("writer_count", 1),
        )
        await persist_writer.start()

        svc = LLMService(db=db, provider=provider, config=cfg, model_provider=model_provider, templates=templates, persist_writer=persist_writer)
        model_svc = ModelService(
            model_provider, db=db,
            persist_writer=persist_writer,
            default_embedding_model=dig(cfg, "embedding", "model"),
            default_rerank_model=dig(cfg, "rerank", "model"),
            default_embedding_dimensions=_embedding_dimensions,
        )
        app.state.llm_service = svc
        app.state.model_service = model_svc
        app.state.db = db

        worker = None
        recovery = None
        try:
            if start_worker:
                worker_concurrency = dig(cfg, "worker", "concurrency")
                worker = Worker(
                    db=db,
                    task_manager=svc._mgr,
                    event_bus=svc._bus,
                    provider=provider,
                    model_provider=model_provider,
                    templates=templates,
                    concurrency=worker_concurrency,
                    poll_interval=dig(cfg, "worker", "poll_interval"),
                    llm_service=svc,
                )
                await worker.start()
                logger.info(
                    "llm_worker_attached pid=%s app_id=%s worker_id=%s concurrency=%s active_tasks=%s",
                    os.getpid(),
                    id(app),
                    id(worker),
                    worker_concurrency,
                    len(worker._tasks),
                )

                recovery = LeaseRecovery(
                    db=db,
                    task_manager=svc._mgr,
                    event_bus=svc._bus,
                    interval=dig(cfg, "task", "lease_recovery_interval"),
                )
                await recovery.start()
                app.state.worker = worker
                app.state.worker_concurrency = worker_concurrency
                app.state.lease_recovery = recovery
        except Exception:
            if recovery:
                await recovery.stop()
            if worker:
                await worker.stop()
            await db.close()
            raise

        yield

        logger.info(
            "llm_lifespan_shutdown pid=%s app_id=%s worker_id=%s",
            os.getpid(),
            id(app),
            id(worker) if worker else None,
        )
        if recovery:
            await recovery.stop()
        if worker:
            await worker.stop()
            try:
                await db.execute(
                    "UPDATE agent_llm_tasks SET status = 'queued', lease_expires_at = NULL "
                    "WHERE status = 'running'"
                )
                logger.info("Re-queued in-flight tasks on shutdown")
            except Exception:
                logger.exception("Failed to re-queue in-flight tasks")
        # Flush remaining sync-call records before closing DB
        await persist_writer.stop()
        if hasattr(provider, 'close'):
            await provider.close()
        if hasattr(model_provider, 'close'):
            await model_provider.close()
        await db.close()

    app = FastAPI(title="LLM Service", version="0.1.0", lifespan=lifespan)
    app.state.config = cfg

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip()
            for origin in os.getenv("CMKB_CORS_ORIGINS", "").split(",")
            if origin.strip()
        ] or ["http://localhost:8080", "http://127.0.0.1:8080"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from llm_service.api.admin import router as admin_router
    from llm_service.api.health import router as health_router
    from llm_service.api.model_api import router as model_api_router
    from llm_service.api.results import router as results_router
    from llm_service.api.stats import router as stats_router
    from llm_service.api.tasks import router as tasks_router
    from llm_service.api.templates import router as templates_router

    app.include_router(admin_router)
    app.include_router(health_router)
    app.include_router(model_api_router)
    app.include_router(tasks_router)
    app.include_router(results_router)
    app.include_router(templates_router)
    app.include_router(stats_router)

    return app
