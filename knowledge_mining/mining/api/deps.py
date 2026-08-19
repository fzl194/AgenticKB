"""Dependency injection for Mining API."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import Query, Request

from knowledge_mining.mining.api.domain_scope import require_domain
from knowledge_mining.mining.document_lifecycle import DocumentLifecycleService
from knowledge_mining.mining.infra.db import AssetCoreDB, MiningRuntimeDB
from knowledge_mining.mining.infra.domain_pack import resolve_domain
from knowledge_mining.mining.infra.pg_config import MiningDbConfig
from knowledge_mining.mining.infra.upload_config import UploadConfig


def get_pool(request: Request) -> Any:
    """Get PostgreSQL connection pool from app state."""
    return request.app.state.pg_pool


def get_config(request: Request) -> MiningDbConfig:
    """Get current MiningDbConfig from app state."""
    return request.app.state.db_config


def get_asset_db(request: Request) -> AssetCoreDB:
    """Create a read-only AssetCoreDB adapter from the shared pool."""
    return AssetCoreDB(request.app.state.pg_pool)


def get_runtime_db(request: Request) -> MiningRuntimeDB:
    """Create a read-only MiningRuntimeDB adapter from the shared pool."""
    return MiningRuntimeDB(request.app.state.pg_pool)


async def get_domain_async_pool(request: Request, domain: str | None):
    """Get the async pool for a validated domain."""
    return await request.app.state.domain_pools.async_pool(
        require_domain(domain or "")
    )


def get_domain_asset_db(request: Request, domain: str | None) -> AssetCoreDB:
    """Create an AssetCoreDB backed by the validated domain's sync pool."""
    return AssetCoreDB(
        request.app.state.domain_pools.sync_pool(require_domain(domain or ""))
    )


@lru_cache(maxsize=1)
def _upload_config() -> UploadConfig:
    return UploadConfig()


def get_document_lifecycle_service(
    request: Request,
    domain: str = Query(...),
) -> DocumentLifecycleService:
    """Bind lifecycle operations to the requested domain and registry channel."""
    domain = require_domain(domain)
    entry = resolve_domain(domain)
    channel = str(entry.get("default_channel") or "prod").strip() or "prod"
    return DocumentLifecycleService(
        get_domain_asset_db(request, domain),
        upload_root=_upload_config().upload_root_path,
        channel=channel,
    )


def get_parse_result_service(request: Request) -> "ParseResultReadService":
    """M5 结构化数据只读视图（组件由组合根放 app.state，未接线时 503）."""
    from knowledge_mining.mining.snapshot_store.read_service import (
        ParseResultReadService,
    )
    from fastapi import HTTPException

    state = getattr(request.app.state, "parse_result_components", None)
    if state is None:
        raise HTTPException(
            status_code=503,
            detail="parse-result view not configured on this deployment",
        )
    return ParseResultReadService(
        snapshots=state["snapshots"],
        storage_objects=state["storage_objects"],
        object_store=state["object_store"],
        segment_store=state["segment_store"],
    )
