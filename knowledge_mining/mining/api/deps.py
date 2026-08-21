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


def get_parse_result_service(
    request: Request, domain: str = Query(...)
) -> "ParseResultReadService":
    """M5/M6 结构化数据只读视图（按 domain 懒构造并缓存到 app.state）.

    优先消费显式组合根（app.state.parse_result_components，测试注入用）；
    缺省用 domain_pools + 对象存储配置自行装配——生产开箱可用。
    """
    from fastapi import HTTPException

    from knowledge_mining.mining.api.domain_scope import require_domain
    from knowledge_mining.mining.snapshot_store.read_service import (
        ParseResultReadService,
    )

    explicit = getattr(request.app.state, "parse_result_components", None)
    if explicit is not None:
        return ParseResultReadService(
            snapshots=explicit["snapshots"],
            storage_objects=explicit["storage_objects"],
            object_store=explicit["object_store"],
            segment_store=explicit["segment_store"],
        )
    scope = require_domain(domain or "")
    cache = getattr(request.app.state, "parse_result_services", None)
    if cache is None:
        cache = {}
        request.app.state.parse_result_services = cache
    if scope in cache:
        return cache[scope]
    try:
        pool = request.app.state.domain_pools.async_pool(scope)
    except Exception as exc:  # noqa: BLE001 —— 域未注册等配置错误
        raise HTTPException(
            status_code=503,
            detail=f"parse-result view unavailable for domain {scope!r}",
        ) from exc
    from knowledge_mining.mining.infra.object_store.config import (
        ObjectStoreConfig,
    )
    from knowledge_mining.mining.infra.object_store.factory import (
        make_object_store,
    )
    from knowledge_mining.mining.segment_compiler.repositories_pg import (
        PgSegmentStore,
    )
    from knowledge_mining.mining.file_management.repositories_pg import (
        PgStorageObjectRepository,
    )
    from knowledge_mining.mining.snapshot_store.repositories_pg import (
        PgSnapshotRepository,
    )

    service = ParseResultReadService(
        snapshots=PgSnapshotRepository(pool),
        storage_objects=PgStorageObjectRepository(pool),
        object_store=make_object_store(ObjectStoreConfig.from_control_plane()),
        segment_store=PgSegmentStore(pool),
    )
    cache[scope] = service
    return service
