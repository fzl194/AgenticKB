# -*- coding: utf-8 -*-
"""一张网管理面 API（47 号 §四-6 / Chunk3 引用路由）.

- ``/api/onenet/*``：管理员（search/probe/toc/imports/resync/selection）；
- ``/api/kb/{kb_id}/onenet/refs``：库主/成员（引用建/删/列）。

凭据未配置 → 503（onenet_not_configured），不伪装成空结果。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from knowledge_mining.mining.api.domain_scope import require_domain
from knowledge_mining.mining.kb.auth import current_user, require_admin
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.deps import get_kb_db
from knowledge_mining.mining.onenet.config import resolve_config
from knowledge_mining.mining.onenet.fetch import Selection
from knowledge_mining.mining.onenet.import_service import (
    OnenetImportError, OnenetImportService, OnenetRepo,
)
from knowledge_mining.mining.onenet.probe import probe_source, search_documents
from knowledge_mining.mining.onenet.toc_scan import scan_toc

logger = logging.getLogger(__name__)

#: 导入工作区（拉取段文件/批次 JSONL 落盘根；进程 cwd = 仓库根）。
DEFAULT_WORKSPACE_ROOT = Path("runtime") / "onenet"

router = APIRouter(prefix="/api/onenet", tags=["onenet"])
refs_router = APIRouter(prefix="/api/kb/{kb_id}/onenet", tags=["onenet"])

_SEARCH_FIELDS = ("doc_name", "file_name", "doc_type", "language")


def _client_factory(request: Request):
    cfg = resolve_config()
    if cfg is None:
        raise HTTPException(503, "onenet_not_configured")
    from knowledge_mining.mining.onenet.client import OnenetClient
    return lambda: OnenetClient.from_config(cfg)


def _repo(request: Request) -> OnenetRepo:
    return OnenetRepo(request.app.state.pg_pool)


def _import_service(request: Request) -> OnenetImportService:
    from knowledge_mining.mining.kb.deps import (
        get_document_service, get_folder_service, get_kb_service,
    )
    from knowledge_mining.mining.kb.services.folder_service import FolderService
    from knowledge_mining.mining.kb.services.kb_service import KbService

    kbdb = KbDB(request.app.state.pg_pool)
    return OnenetImportService(
        repo=_repo(request),
        kbdb=kbdb,
        kb_service=KbService(kbdb),
        doc_service=get_document_service(request),
        folder_service=FolderService(kbdb),
        client_factory=_client_factory(request),
        workspace_root=DEFAULT_WORKSPACE_ROOT,
        auto_miner=_make_auto_miner(request),
    )


def _make_auto_miner(request: Request):
    from knowledge_mining.mining.kb.services import auto_mine

    async def _auto(*, kb, user_id, **kw):
        return await auto_mine.enqueue_auto_mining(
            app_state=request.app.state,
            kbdb=KbDB(request.app.state.pg_pool),
            kb=kb, user_id=user_id,
            username=str(user_id),
        )

    return _auto


# ------------------------------------------------------------ 查询 / 摸底


@router.post("/search")
async def onenet_search(
    body: dict[str, Any],
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    filters = {k: body.get(k) for k in _SEARCH_FIELDS if body.get(k)}
    if not filters:
        raise HTTPException(422, "filters_required: 至少一个查询字段")
    client = _client_factory(request)()
    rows = search_documents(client, filters)
    capped = any(r.get("capped") for r in rows)
    return {"documents": rows, "capped": capped,
            "notice": "fuzzy 查询命中封顶 10000，结果可能不全，只作发现手段"}


@router.post("/probe")
async def onenet_probe(
    body: dict[str, Any],
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    source_id = str(body.get("source_id") or "").strip()
    if not source_id:
        raise HTTPException(422, "source_id required")
    client = _client_factory(request)()
    return probe_source(client, source_id)


# ------------------------------------------------------------ TOC（含缓存）


@router.post("/toc")
async def onenet_toc(
    body: dict[str, Any],
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    domain = require_domain(str(body.get("domain") or ""))
    source_id = str(body.get("source_id") or "").strip()
    if not source_id:
        raise HTTPException(422, "source_id required")
    max_part_id = body.get("max_part_id")

    repo = _repo(request)
    # parsed_version 未变 → 复用缓存（预览不重复打 ~160 请求）
    client_factory = _client_factory(request)
    cached = await repo.get_toc_cache(domain, source_id)
    if cached is not None and not body.get("refresh"):
        return {"cached": True, **_toc_payload(cached)}

    try:
        toc = scan_toc(
            client_factory(), source_id,
            max_part_id=int(max_part_id) if max_part_id else None)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    await repo.put_toc_cache(
        domain=domain, source_id=source_id,
        parsed_version=toc.get("parsed_version"), toc=toc)
    return {"cached": False, **toc}


@router.get("/toc/{domain}/{source_id}")
async def onenet_toc_cached(
    domain: str, source_id: str,
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    repo = _repo(request)
    cached = await repo.get_toc_cache(require_domain(domain), source_id)
    if cached is None:
        raise HTTPException(404, "toc not scanned")
    return {"cached": True, **_toc_payload(cached)}


def _toc_payload(cached: dict[str, Any]) -> dict[str, Any]:
    import json as _json

    toc = cached.get("toc_json")
    if isinstance(toc, str):
        toc = _json.loads(toc)
    return {
        "source_id": cached.get("source_id"),
        "parsed_version": cached.get("parsed_version_seen"),
        "scanned_at": cached.get("scanned_at"),
        **(toc or {}),
    }


# ------------------------------------------------------------ 导入记录


@router.post("/imports", status_code=202)
async def onenet_start_import(
    body: dict[str, Any],
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    domain = require_domain(str(body.get("domain") or ""))
    source_id = str(body.get("source_id") or "").strip()
    if not source_id:
        raise HTTPException(422, "source_id required")
    selection = Selection.from_dict(body.get("selection") or {})
    svc = _import_service(request)
    try:
        record = await svc.start_import(
            domain=domain, source_id=source_id, selection=selection,
            actor_id=str(user["id"]), username=str(user.get("username") or user["id"]),
            doc_name=body.get("doc_name"),
            parsed_version=body.get("parsed_version"),
            total_slices=body.get("total_slices"),
        )
    except OnenetImportError as e:
        raise HTTPException(409, str(e)) from e
    return record


@router.get("/imports")
async def onenet_list_imports(
    domain: str,
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    resolved = require_domain(domain)
    return {"imports": await _repo(request).list_imports(domain=resolved)}


@router.get("/imports/{import_id}")
async def onenet_get_import(
    import_id: str,
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    record = await _repo(request).get_import(import_id)
    if record is None:
        raise HTTPException(404, "import not found")
    kbdb = KbDB(request.app.state.pg_pool)
    docs = await kbdb.list_documents_by_key_prefix(
        record["kb_id"], f"onenet:{record['source_id']}:")
    record["documents"] = [
        {"id": d["id"], "document_name": d.get("document_name"),
         "directory_path": d.get("directory_path"), "status": d.get("status"),
         "file_size": d.get("file_size")}
        for d in docs
    ]
    return record


# ------------------------------------------------------------ KB 引用（47 号 §四-8）


def _refs_service(request: Request):
    from knowledge_mining.mining.onenet.refs_service import RefsService
    return RefsService(request.app.state.pg_pool)


@refs_router.post("/refs")
async def kb_add_refs(
    kb_id: str,
    body: dict[str, Any],
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
    request: Request = None,  # type: ignore[assignment]
):
    """批量建立引用（库主/editor：can_write 守卫；跨域/非公共库文档服务端拒绝）。"""
    document_ids = [str(d) for d in (body.get("document_ids") or []) if str(d).strip()]
    if not document_ids:
        raise HTTPException(422, "document_ids required")
    if not await kbdb.can_write(kb_id=kb_id, user_id=str(user["id"])):
        raise HTTPException(403, "kb_write_required")
    svc = _refs_service(request)
    from knowledge_mining.mining.onenet.refs_service import RefsError
    try:
        return await svc.add_refs(
            kb_id=kb_id, document_ids=document_ids, actor_id=str(user["id"]))
    except RefsError as e:
        msg = str(e)
        code = msg.split(":", 1)[0]
        status = 403 if code in (
            "cross_domain_reference", "not_onenet_document") else 422
        raise HTTPException(status, msg) from e


@refs_router.delete("/refs")
async def kb_remove_refs(
    kb_id: str,
    body: dict[str, Any],
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
    request: Request = None,  # type: ignore[assignment]
):
    document_ids = [str(d) for d in (body.get("document_ids") or []) if str(d).strip()]
    if not document_ids:
        raise HTTPException(422, "document_ids required")
    if not await kbdb.can_write(kb_id=kb_id, user_id=str(user["id"])):
        raise HTTPException(403, "kb_write_required")
    return await _refs_service(request).remove_refs(
        kb_id=kb_id, document_ids=document_ids)


@refs_router.get("/refs")
async def kb_list_refs(
    kb_id: str,
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
    request: Request = None,  # type: ignore[assignment]
):
    if not await kbdb.is_visible(kb_id=kb_id, user_id=str(user["id"])):
        raise HTTPException(404, f"KB {kb_id} not found")
    refs = await _refs_service(request).list_refs(kb_id=kb_id)
    return {"refs": refs}


__all__ = ["DEFAULT_WORKSPACE_ROOT", "refs_router", "router"]
