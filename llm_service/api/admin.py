"""Admin endpoints — config hot-reload."""
from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter, Request

from llm_service.config import (
    dig,
    fetch_config_from_control_plane,
    resolve_active_model_config,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _build_provider(active_provider: dict) -> tuple[object, str]:
    """Build a new Provider instance based on resolved config."""
    from llm_service.providers.factory import build_chat_provider

    return build_chat_provider(active_provider)


@router.post("/reload-config")
async def reload_config(request: Request):
    """Re-fetch config from control plane and hot-swap ALL runtime components.

    Supports:
    - provider_type switch (openai_compatible <-> anthropic)
    - model/key/url/headers/timeout changes
    - embedding/rerank parameter changes
    - worker.concurrency / poll_interval
    - task.* retry / lease parameters
    - template.cache_ttl
    - task.lease_recovery_interval

    In-flight tasks complete with the old provider; new tasks use the new one.
    host/port are NOT hot-swappable (process-level, requires restart).
    """
    app = request.app

    # ── 1. Fetch fresh config ──────────────────────────────────────
    try:
        new_cfg = fetch_config_from_control_plane()
    except Exception as exc:
        logger.exception("Failed to fetch config from control plane")
        return {"ok": False, "error": str(exc)}

    active_provider = resolve_active_model_config(new_cfg)
    new_type = active_provider.get("provider_type", "openai_compatible")

    # ── 2. Provider hot-swap ───────────────────────────────────────
    svc = app.state.llm_service
    old_provider = svc._provider
    old_type = getattr(old_provider, "provider_name", "openai_compatible")
    model_name = active_provider.get("model", active_provider.get("active_model", ""))

    if old_type != new_type:
        # Cross-protocol switch: create new provider, swap references, close old
        logger.info("provider_type changed: %s -> %s, swapping provider", old_type, new_type)
        new_provider, _ = _build_provider(active_provider)
        svc._provider = new_provider
        svc._provider_name = new_provider.provider_name
        svc._default_model = model_name
        # Swap in Worker
        worker = getattr(app.state, "worker", None)
        if worker:
            worker._provider = new_provider
        # Close old provider (in-flight requests finish naturally)
        if hasattr(old_provider, "close"):
            asyncio.ensure_future(_safe_close(old_provider))
    else:
        # Same type: mutate attributes in-place
        _update_provider_attrs(old_provider, active_provider)
        svc._default_model = model_name

    # ── 3. Model provider (embedding + rerank) ─────────────────────
    model_provider = svc._model_provider
    if hasattr(model_provider, "_embedding_api_key"):
        model_provider._embedding_api_key = dig(new_cfg, "embedding", "api_key")
        model_provider._embedding_url = dig(new_cfg, "embedding", "base_url").rstrip("/")
        model_provider._embedding_model = dig(new_cfg, "embedding", "model")
        _embedding_timeout = dig(new_cfg, "embedding", "timeout")
        model_provider._embedding_timeout = _embedding_timeout
        model_provider._rerank_api_key = dig(new_cfg, "rerank", "api_key")
        model_provider._rerank_url = dig(new_cfg, "rerank", "base_url").rstrip("/")
        model_provider._rerank_model = dig(new_cfg, "rerank", "model")
        _rerank_timeout = dig(new_cfg, "rerank", "timeout")
        model_provider._rerank_timeout = _rerank_timeout

    # ── 4. TaskManager parameters ──────────────────────────────────
    mgr = svc._mgr
    mgr._default_max_attempts = dig(new_cfg, "task", "default_max_attempts")
    mgr._lease_duration = dig(new_cfg, "task", "lease_duration")
    mgr._backoff_base = dig(new_cfg, "task", "retry_backoff_base")
    mgr._backoff_max = dig(new_cfg, "task", "retry_backoff_max")

    # ── 5. Worker concurrency + poll_interval ──────────────────────
    worker = getattr(app.state, "worker", None)
    if worker:
        new_concurrency = dig(new_cfg, "worker", "concurrency")
        new_poll = dig(new_cfg, "worker", "poll_interval")
        worker._poll_interval = new_poll
        if new_concurrency != worker._concurrency:
            await _adjust_worker_concurrency(worker, new_concurrency)
        app.state.worker_concurrency = new_concurrency

    # ── 6. LeaseRecovery interval ──────────────────────────────────
    recovery = getattr(app.state, "lease_recovery", None)
    if recovery and hasattr(recovery, "_interval"):
        recovery._interval = dig(new_cfg, "task", "lease_recovery_interval")

    # ── 7. TemplateRegistry cache TTL ──────────────────────────────
    if svc._templates and hasattr(svc._templates, "_cache_ttl"):
        svc._templates._cache_ttl = dig(new_cfg, "template", "cache_ttl")

    # ── 8. ModelService defaults ───────────────────────────────────
    if hasattr(app.state, "model_service"):
        ms = app.state.model_service
        ms._default_embedding_model = dig(new_cfg, "embedding", "model")
        ms._default_rerank_model = dig(new_cfg, "rerank", "model")
        ms._default_embedding_dimensions = new_cfg.get("embedding", {}).get("dimensions")

    # ── 9. Update config references ────────────────────────────────
    app.state.config = new_cfg
    svc._config = new_cfg

    logger.info(
        "Config reloaded: provider=%s model=%s concurrency=%s",
        svc._provider_name, svc._default_model,
        dig(new_cfg, "worker", "concurrency"),
    )
    return {
        "ok": True,
        "config": {
            "active_model": new_cfg.get("provider", {}).get("active_model"),
            "provider_type": new_type,
            "provider_model": model_name,
            "provider_base_url": active_provider.get("base_url"),
            "embedding_model": dig(new_cfg, "embedding", "model"),
            "rerank_model": dig(new_cfg, "rerank", "model"),
            "worker_concurrency": dig(new_cfg, "worker", "concurrency"),
            "poll_interval": dig(new_cfg, "worker", "poll_interval"),
            "max_attempts": dig(new_cfg, "task", "default_max_attempts"),
            "lease_duration": dig(new_cfg, "task", "lease_duration"),
        },
    }


# ── Helpers ────────────────────────────────────────────────────────

def _update_provider_attrs(provider: object, cfg: dict) -> None:
    """Mutate provider internal attributes for same-type config changes."""
    if hasattr(provider, "_url"):
        provider._url = cfg["base_url"].rstrip("/")
    if hasattr(provider, "_api_key"):
        provider._api_key = cfg["api_key"]
    if hasattr(provider, "_model"):
        provider._model = cfg.get("model", cfg.get("active_model", ""))
    if hasattr(provider, "_extra_headers"):
        provider._extra_headers = cfg.get("headers") or {}
    if hasattr(provider, "_timeout"):
        provider._timeout = cfg.get("timeout", 30)
    if hasattr(provider, "_api_version"):
        provider._api_version = cfg.get("api_version", "2023-06-01")


async def _safe_close(provider: object) -> None:
    """Close a provider's httpx client, logging errors."""
    try:
        if hasattr(provider, "close"):
            await provider.close()
    except Exception:
        logger.exception("Failed to close old provider during hot-swap")


async def _adjust_worker_concurrency(worker: object, target: int) -> None:
    """Dynamically scale worker asyncio tasks to match target concurrency."""
    current = len(worker._tasks)
    if target > current:
        # Scale up: add new loops
        for i in range(current, target):
            name = f"worker-{i}"
            t = asyncio.create_task(worker._loop(name), name=name)
            worker._tasks.append(t)
        worker._concurrency = target
        logger.info("Worker scaled up: %d -> %d", current, target)
    elif target < current:
        # Scale down: cancel excess tasks
        to_cancel = worker._tasks[target:]
        worker._tasks = worker._tasks[:target]
        for t in to_cancel:
            t.cancel()
        await asyncio.gather(*to_cancel, return_exceptions=True)
        worker._concurrency = target
        logger.info("Worker scaled down: %d -> %d", current, target)


@router.get("/worker-status")
async def worker_status(request: Request):
    """Diagnostic: return actual Worker concurrency and poll interval."""
    worker = getattr(request.app.state, "worker", None)
    if not worker:
        return {"ok": True, "worker_running": False}
    return {
        "ok": True,
        "worker_running": worker._running,
        "concurrency": worker._concurrency,
        "poll_interval": worker._poll_interval,
        "active_tasks": len(worker._tasks),
    }
