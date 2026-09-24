# -*- coding: utf-8 -*-
"""一张网管理面 API（47 号 §四-6 / Chunk3 引用路由）.

- ``/api/onenet/*``：管理员（search/probe/toc/imports/resync/selection）；
- ``/api/kb/{kb_id}/onenet/refs``：库主/成员（引用建/删/列）。

凭据未配置 → 503（onenet_not_configured），不伪装成空结果。
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from knowledge_mining.mining.api.domain_scope import require_domain
from knowledge_mining.mining.kb.auth import current_user, require_admin
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.deps import get_kb_db
from knowledge_mining.mining.onenet.config import resolve_config
from knowledge_mining.mining.onenet.fetch import Selection
from knowledge_mining.mining.onenet.restore import RULE_VERSION
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

from knowledge_mining.mining.onenet.probe import SEARCH_FIELDS as _SEARCH_FIELDS

#: source_id 白名单（安全审查 H-1）：source_id 会拼入导入工作区文件系统路径
#: （workspace/domain/source_id），白名单拒绝路径分隔符/绝对段/逃逸。
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

#: 单次建引用上限（安全审查 M-2：串行校验查询的放大上限）。
_MAX_REF_BATCH = 200


def _validated_source_id(raw) -> str:
    sid = str(raw or "").strip()
    if not _SOURCE_ID_RE.match(sid):
        raise HTTPException(
            422, "invalid_source_id: 仅允许字母数字开头、64 位内的 [A-Za-z0-9_.-]")
    return sid


def _client_factory(request: Request):
    cfg = resolve_config()
    if cfg is None:
        raise HTTPException(503, "onenet_not_configured")
    from knowledge_mining.mining.onenet.client import OnenetClient
    return lambda: OnenetClient.from_config(cfg)


def _repo(request: Request) -> OnenetRepo:
    return OnenetRepo(request.app.state.pg_pool)


async def _import_service(request: Request) -> OnenetImportService:
    from knowledge_mining.mining.kb.deps import get_document_service
    from knowledge_mining.mining.kb.services.folder_service import FolderService
    from knowledge_mining.mining.kb.services.kb_service import KbService

    kbdb = KbDB(request.app.state.pg_pool)
    return OnenetImportService(
        repo=_repo(request),
        kbdb=kbdb,
        kb_service=KbService(kbdb),
        # get_document_service 是 async def（FastAPI 依赖）——必须 await，
        # 裸协程注入会让首个 store_source_bytes 调用炸 AttributeError（内网实测）。
        doc_service=await get_document_service(request),
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
    """第一步 · 查询发现（V1.2）：三元组透传 + 文档汇总分页。"""
    try:
        out = await asyncio.to_thread(
            search_documents,
            _client_factory(request)(),
            body.get("conditions") or [],
            page=int(body.get("page") or 1),
            page_size=int(body.get("page_size") or 20),
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {**out,
            "notice": "fuzzy 查询命中封顶 10000，结果可能不全，只作发现手段"}


@router.post("/probe")
async def onenet_probe(
    body: dict[str, Any],
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    source_id = _validated_source_id(body.get("source_id"))
    client = _client_factory(request)()
    try:
        return await asyncio.to_thread(probe_source, client, source_id)
    finally:
        client.close()


# ------------------------------------------------------------ TOC（含缓存）


@router.post("/toc")
async def onenet_toc(
    body: dict[str, Any],
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    domain = require_domain(str(body.get("domain") or ""))
    source_id = _validated_source_id(body.get("source_id"))
    max_part_id = body.get("max_part_id")

    repo = _repo(request)
    # 缓存复用先比对 parsed_version（审查 M2：上游重解析后旧树不复活）；
    # 再比对 rule_version（beta-2：还原规则变版后旧树必须重扫，不复活）；
    # refresh=1 强制重扫（前端「刷新章节树」按钮）。
    client_factory = _client_factory(request)
    cached = await repo.get_toc_cache(domain, source_id)
    if cached is not None and not body.get("refresh"):
        if _cached_rule_version(cached) == RULE_VERSION:
            current_version = await asyncio.to_thread(
                lambda: client_factory().probe_source(source_id).get("parsed_version"))
            if current_version == cached.get("parsed_version_seen"):
                return {"cached": True, **_toc_payload(cached)}

    try:
        mpi = int(max_part_id) if max_part_id else None
    except (TypeError, ValueError):
        raise HTTPException(422, "max_part_id must be an integer") from None
    try:
        client = client_factory()
        try:
            toc = await asyncio.to_thread(scan_toc, client, source_id, max_part_id=mpi)
        finally:
            client.close()
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


def _cached_rule_version(cached: dict[str, Any]) -> str | None:
    """缓存树当时的还原规则版本（无/解析失败 → None，视为不匹配）."""
    import json as _json

    toc = cached.get("toc_json")
    if isinstance(toc, str):
        try:
            toc = _json.loads(toc)
        except ValueError:
            return None
    if isinstance(toc, dict):
        return toc.get("rule_version")
    return None


def _path_hints_from_tree(
    tree: list[dict[str, Any]], subtrees: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    """从服务端 TOC 树生成 raw path → 语义标题段链。"""
    wanted = set(subtrees)
    found: dict[str, tuple[str, ...]] = {}

    def walk(nodes: list[dict[str, Any]], ancestors: tuple[str, ...]) -> None:
        for node in nodes:
            title = str(node.get("title") or "").strip()
            path = str(node.get("path") or "").strip()
            segments = (*ancestors, title)
            if path in wanted:
                previous = found.get(path)
                if previous is not None and previous != segments:
                    raise HTTPException(409, "toc contains duplicate path keys")
                found[path] = segments
            children = node.get("children") or []
            if isinstance(children, list):
                walk(children, segments)

    walk(tree, ())
    missing = wanted - set(found)
    if missing:
        raise HTTPException(422, "selection subtree not found in current toc")
    return found


async def _with_authoritative_path_hints(
    repo: Any, domain: str, source_id: str, selection: Selection,
) -> Selection:
    """用当前规则版本 TOC 校验并替换客户端 path_hints。"""
    if not selection.subtrees:
        return Selection(
            max_part_id=selection.max_part_id,
            restore_mode=selection.restore_mode,
        )
    cached = await repo.get_toc_cache(domain, source_id)
    if cached is None or _cached_rule_version(cached) != RULE_VERSION:
        raise HTTPException(409, "current toc scan required before subtree import")
    payload = _toc_payload(cached)
    tree = payload.get("tree") or []
    if not isinstance(tree, list):
        raise HTTPException(409, "current toc cache is invalid")
    generated = _path_hints_from_tree(tree, selection.subtrees)
    supplied = selection.path_hints_map
    if any(generated.get(path) != segments for path, segments in supplied.items()):
        raise HTTPException(422, "path_hints do not match current toc")
    return Selection(
        subtrees=selection.subtrees,
        max_part_id=selection.max_part_id,
        path_hints=tuple(sorted(generated.items())),
        restore_mode=selection.restore_mode,
    )


# ------------------------------------------------------------ 导入记录


@router.post("/imports", status_code=202)
async def onenet_start_import(
    body: dict[str, Any],
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    domain = require_domain(str(body.get("domain") or ""))
    source_id = _validated_source_id(body.get("source_id"))
    try:
        selection = Selection.from_dict(body.get("selection") or {})
    except (TypeError, ValueError):
        raise HTTPException(422, "invalid selection") from None
    repo = _repo(request)
    selection = await _with_authoritative_path_hints(
        repo, domain, source_id, selection,
    )
    svc = await _import_service(request)
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
    if len(document_ids) > _MAX_REF_BATCH:
        raise HTTPException(422, f"document_ids exceeds {_MAX_REF_BATCH} per request")
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


@refs_router.get("/imports")
async def kb_onenet_imports(
    kb_id: str,
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
    request: Request = None,  # type: ignore[assignment]
):
    """库级导入池（审查 H8）：KB 成员可见本域 done 导入——不再打 admin 端点。

    返回行附产物文档（文件清单），供引用面板直接渲染。
    """
    if not await kbdb.is_visible(kb_id=kb_id, user_id=str(user["id"])):
        raise HTTPException(404, f"KB {kb_id} not found")
    kb = await kbdb.get_kb(kb_id)
    domain = str(kb.get("domain") or "") if kb else ""
    if not domain:
        raise HTTPException(404, f"KB {kb_id} not found")
    rows = await _repo(request).list_imports(domain=domain)
    done = [r for r in rows if r.get("status") == "done"]
    for r in done:
        docs = await kbdb.list_documents_by_key_prefix(
            r["kb_id"], f"onenet:{r['source_id']}:")
        r["documents"] = [
            {"id": d["id"], "document_name": d.get("document_name"),
             "directory_path": d.get("directory_path")}
            for d in docs
        ]
    return {"imports": done}


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


# ------------------------------------------------------------ 重同步 / selection


@router.post("/imports/{import_id}/resync")
async def onenet_resync(
    import_id: str,
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    """重同步：探测 → 有变化则重拉 diff → 文件级传播（47 号 §四-7）."""
    from knowledge_mining.mining.onenet.refs_service import RefsService
    from knowledge_mining.mining.onenet.resync import ResyncError, resync

    repo = _repo(request)
    import_row = await repo.get_import(import_id)
    if import_row is None:
        raise HTTPException(404, "import not found")
    client = _client_factory(request)()
    kbdb = KbDB(request.app.state.pg_pool)
    from knowledge_mining.mining.kb.deps import get_document_service

    refs = RefsService(request.app.state.pg_pool)
    auto = _make_auto_miner(request)
    import_svc = await _import_service(request)

    async def _refs_cleanup(document_ids: list[str]) -> None:
        await refs.remove_refs_for_documents(document_ids)

    async def _reminer(kb_id: str) -> None:
        if auto is None:
            return
        kb = await kbdb.get_kb(kb_id)
        await auto(kb=kb, user_id=import_row["created_by"])

    async def _register_file(**kw):
        await import_svc._import_file(**kw)  # 同导入幂等登记通道（审查 H7）

    import inspect

    force = bool(request.query_params.get("force")) if hasattr(request, "query_params") else False
    try:
        return await resync(
            repo=repo, kbdb=kbdb,
            doc_service=await get_document_service(request),
            client=client, import_id=import_id,
            workspace_root=DEFAULT_WORKSPACE_ROOT,
            refs_cleanup=_refs_cleanup, reminer=_reminer,
            register_file=_register_file, force=force,
        )
    except ResyncError as e:
        raise HTTPException(409, str(e)) from e
    finally:
        client.close()


@router.post("/imports/{import_id}/retry", status_code=202)
async def onenet_retry_import(
    import_id: str,
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    """失败重跑（审查 H6）：段文件/document_key 双层幂等，不产生重复。"""
    svc = await _import_service(request)
    from knowledge_mining.mining.onenet.import_service import OnenetImportError
    try:
        return await svc.retry_import(import_id)
    except OnenetImportError as e:
        raise HTTPException(409, str(e)) from e


@router.delete("/imports/{import_id}")
async def onenet_delete_import(
    import_id: str,
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    """source 级删除（2026-09-16 切统一硬删管线）：该导入全部文档硬删（含
    挖掘产物/独占快照/对象回收）+ 引用清理 + 目录子树 + 导入记录硬删。
    进行中（非 done/failed）拒绝。

    toc 缓存/拉取工作区保留——同 source 重导即复用加速。
    """
    from knowledge_mining.mining.kb.services.purge_service import PurgeService

    repo = _repo(request)
    row = await repo.get_import(import_id)
    if row is None:
        raise HTTPException(404, "import not found")
    if row.get("status") not in ("done", "failed"):
        raise HTTPException(409, f"import_busy: {row.get('status')}")

    kbdb = KbDB(request.app.state.pg_pool)
    # key 前缀全量取文档 id（含软删态——1.1.5 前软删的存量也要清干净）
    doc_ids = await kbdb.list_document_ids_by_key_prefix(
        row["kb_id"], f"onenet:{row['source_id']}:")

    purge = PurgeService(request.app.state.pg_pool,
                         getattr(request.app.state, "object_store", None))
    summary = (await purge.purge_documents(row["kb_id"], doc_ids)
               if doc_ids else purge.empty_summary())

    # 目录子树清理：顶层段「文档名 [source_id]」专属本 source（名字带唯一标识）
    from knowledge_mining.mining.onenet.restore import top_folder_segment
    top = top_folder_segment(row.get("doc_name"), row["source_id"])
    removed_folders = 0
    if await kbdb.count_docs_under_path(kb_id=row["kb_id"], path=top) == 0:
        removed_folders = await kbdb.delete_folder_subtree(row["kb_id"], top)

    await repo.delete_import(import_id)
    return {"deleted_documents": doc_ids,
            "reclaimed_snapshots": summary["reclaimed_snapshots"],
            "reclaimed_objects": summary["reclaimed_objects"],
            "removed_folders": removed_folders}


@router.patch("/imports/{import_id}/selection")
async def onenet_update_selection(
    import_id: str,
    body: dict[str, Any],
    user: dict[str, Any] = Depends(require_admin),
    request: Request = None,  # type: ignore[assignment]
):
    """合并编辑勾选范围（47 号：追加子树=编辑 selection 后走重同步）."""
    repo = _repo(request)
    import_row = await repo.get_import(import_id)
    if import_row is None:
        raise HTTPException(404, "import not found")
    if import_row.get("status") not in ("done", "failed"):
        raise HTTPException(409, f"import_busy: {import_row.get('status')}")
    current = Selection.from_dict(import_row.get("selection_json") or {})
    raw_incoming = body.get("selection") or {}
    if not isinstance(raw_incoming, dict):
        raise HTTPException(422, "invalid selection")
    raw_incoming = {
        **raw_incoming,
        "restore_mode": raw_incoming.get(
            "restore_mode", current.restore_mode),
    }
    try:
        incoming = Selection.from_dict(raw_incoming)
        merged = current.merge(incoming)
    except (TypeError, ValueError):
        raise HTTPException(422, "restore_mode cannot change") from None
    merged = await _with_authoritative_path_hints(
        repo, import_row["domain"], import_row["source_id"], merged,
    )
    await repo.update_import(import_id, selection_json=merged.to_dict())
    return {"selection": merged.to_dict()}


@refs_router.get("/documents/{document_id}/markdown")
async def onenet_document_markdown(
    kb_id: str,
    document_id: str,
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
    request: Request = None,  # type: ignore[assignment]
):
    """一张网逻辑文档的 markdown 预览（JSONL 对象即时还原渲染，47 号 §五）."""
    if not await kbdb.is_visible(kb_id=kb_id, user_id=str(user["id"])):
        raise HTTPException(404, f"KB {kb_id} not found")
    doc = await kbdb.get_document_identity(document_id)
    if doc is None or not await kbdb.document_in_kb_or_referenced(kb_id, document_id):
        raise HTTPException(404, "Document not found")
    from knowledge_mining.mining.kb.deps import get_document_service
    from knowledge_mining.mining.onenet.restore import render_file_markdown

    svc = await get_document_service(request)
    payload = await svc.read_object_bytes(
        document_id=document_id, user_id=str(user["id"]))
    if payload is None:
        raise HTTPException(404, "Document has no onenet object")
    import json as _json

    slices: list[dict[str, Any]] = []
    for line in payload.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = _json.loads(line)
            if isinstance(row, dict):
                slices.append(row)
        except _json.JSONDecodeError:
            continue
    if not slices:
        raise HTTPException(422, "onenet object has no parsable slices")
    md = render_file_markdown(
        slices, title=str(doc.get("document_name") or "").removesuffix(".jsonl"))
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


__all__ = ["DEFAULT_WORKSPACE_ROOT", "refs_router", "router"]
