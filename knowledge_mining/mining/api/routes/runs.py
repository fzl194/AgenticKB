"""Mining run routes — CRUD and async execution."""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from knowledge_mining.mining.api.domain_scope import require_domain
from knowledge_mining.mining.api.routes.uploads import resolve_upload_batch_path
from knowledge_mining.mining.kb.auth import current_user, require_admin
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.infra.domain_pack import resolve_domain
from knowledge_mining.mining.infra.mining_config import MiningConfig
from knowledge_mining.mining.infra.pg_config import MiningDbConfig
from knowledge_mining.mining.preflight import build_run_preflight
from knowledge_mining.mining.workflow.repositories.domain_run_repository import (
    AsyncDomainRunRepository,
)
from knowledge_mining.mining.workflow.service import WorkflowArchived, WorkflowNotFound

logger = logging.getLogger(__name__)

# 路由级 Depends(current_user)：这一族**没有匿名端点**。放在 router 上而不是逐个路由挂，
# 是因为漏挂的代价不对称——新加一个路由忘了护栏 = 又开一个越权口，而多挂一次只是多一次
# 已被 FastAPI 依赖缓存去重的解析。逐路由的细粒度授权（可见性 / 写权限 / admin）见下面
# require_run_read / require_run_write / require_admin。
router = APIRouter(prefix="/api/runs", tags=["runs"], dependencies=[Depends(current_user)])

# Mutex to prevent concurrent mining runs —— 每个域一把，域之间互不阻塞。
# 曾经是全局一把锁，任意一个域在挖掘就把其余域全部 409 掉。
_run_locks: dict[str, threading.Lock] = {}
_run_locks_guard = threading.Lock()


def _expand_preprocess_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    legacy_error = meta.get("preprocess_error")
    status = meta.get("preprocess_status")
    if status is None and legacy_error:
        status = "failed"
    warnings = meta.get("preprocess_warnings") or []
    if not isinstance(warnings, list):
        warnings = []
    summary = meta.get("excel_summary")
    if not isinstance(summary, dict):
        summary = None
    return {
        "preprocess_status": status,
        "error_code": meta.get("preprocess_error_code"),
        "error_detail": legacy_error,
        "warnings": warnings,
        "excel_summary": summary,
    }


def _domain_run_lock(domain: str) -> threading.Lock:
    """Return (creating on first use) the mining mutex for one domain."""
    with _run_locks_guard:
        lock = _run_locks.get(domain)
        if lock is None:
            lock = _run_locks[domain] = threading.Lock()
        return lock


# ── Request / Response models ──

class CreateRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    input_path: str | None = None
    upload_batch_id: str | None = None
    domain: str
    workflow_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("workflow_id", "workflowId"),
    )
    workflow_version: int | None = Field(
        default=None,
        ge=1,
        validation_alias=AliasChoices("workflow_version", "workflowVersion"),
    )
    domain_pack: str | None = None
    max_workers: int | None = None
    phase1_only: bool = False
    publish_on_partial_failure: bool = False
    llm_base_url: str | None = None
    preflight_id: str | None = None
    document_decisions: list["RunDocumentDecision"] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_selection(self):
        if bool(self.input_path) == bool(self.upload_batch_id):
            raise ValueError("exactly one of input_path or upload_batch_id is required")
        if self.workflow_version is not None and self.workflow_id is None:
            raise ValueError("workflow_id is required with workflow_version")
        if self.upload_batch_id and self.workflow_id is not None:
            if not self.preflight_id or not self.document_decisions:
                raise ValueError(
                    "explicit Workflow uploads require preflight_id and document_decisions"
                )
        return self


class RunDocumentDecision(BaseModel):
    relative_path: str
    raw_content_hash: str
    selected_action: Literal[
        "NEW", "REUSED", "RESTORED", "REMINED", "KEPT_CURRENT", "JOINED_EXISTING"
    ]
    state_token: str


class RunResponse(BaseModel):
    run_id: str
    status: str
    current_stage: str
    started_at: str | None = None
    execution_engine: str = "legacy"
    workflow_id: str | None = None
    workflow_version: int | None = None
    workflow_graph_hash: str | None = None


class RunPreflightRequest(BaseModel):
    domain: str
    upload_batch_id: str
    workflow_id: str
    workflow_version: int = Field(ge=1)


def _frozen_workflow_summary(
    run: dict[str, Any], *, include_graph: bool = False
) -> dict[str, Any] | None:
    """Render historical Workflow data only from the Domain Run snapshot."""
    if run.get("execution_engine") != "workflow":
        return None
    manifest = run.get("workflow_manifest_json")
    if not isinstance(manifest, dict):
        return None
    summary = {
        "id": run.get("workflow_id") or manifest.get("workflowId"),
        "version": run.get("workflow_version") or manifest.get("workflowVersion"),
        "version_id": run.get("workflow_version_id"),
        "graph_hash": run.get("workflow_graph_hash") or manifest.get("graphHash"),
        "schema_version": manifest.get("schemaVersion"),
        "catalog_version": manifest.get("catalogVersion"),
    }
    if include_graph:
        execution = manifest.get("executionPlan") or {}
        summary.update({
            "graph": manifest.get("graph"),
            "nodes": manifest.get("nodes") or [],
            "edges": manifest.get("edges") or [],
            "required_completion": execution.get("requiredCompletion") or [],
        })
    return summary


class CancelRunResponse(BaseModel):
    run_id: str
    status: str
    message: str


async def _require_run_domain(pool, run_id: str, domain: str) -> dict:
    """Return the run only when it belongs to the requested domain."""
    requested = require_domain(domain)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, domain, status, kb_id FROM mining_runs "
            "WHERE id = %s AND domain = %s",
            [run_id, requested],
        )
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, f"Run {run_id} not found")
    return dict(row)


# ── 身份护栏 ──
#
# 这一族端点历史上完全不校验身份：任何人只要有 runId 就能读别人私有知识库的段落/单元/
# 关系，并 cancel/publish/resume 别人的挖掘。堵掉 list（不再发 id）只关闭了批量枚举，
# 从分享链接或浏览记录拿到一个 id 的成本远低于猜 UUID，所以整族收口。
#
# 归属判定走 mining_runs.kb_id（007 提升为列）：
#   kb_id 非空  → KB 挖掘产生的 run，按该 KB 的可见性/写权限判定
#   kb_id 为空  → legacy 域级 run（/api/runs 直接发起的），无 KB 归属，仅 site admin 可见
#
# 不可见一律 404、与「不存在」同一响应——和 KB 层保持一致，不泄露 run 是否存在；
# 可见但无写权限才 403（这时存在性已经不是秘密了）。

async def _authorize_run(
    request: Request, run_id: str, domain: str, *, user: dict[str, Any], write: bool,
) -> dict:
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    run = await _require_run_domain(pool, run_id, domain)
    is_admin = user.get("site_role") == "admin"
    kb_id = run.get("kb_id")

    if not kb_id:
        if not is_admin:
            raise HTTPException(404, f"Run {run_id} not found")
        return run

    # KB 元数据在主库（pg_pool），run 在域库——沿用 KB 路由既有的取池方式。
    kbdb = KbDB(request.app.state.pg_pool)
    if not await kbdb.is_visible(kb_id=kb_id, user_id=user["id"]):
        raise HTTPException(404, f"Run {run_id} not found")
    if write and not await kbdb.can_write(kb_id=kb_id, user_id=user["id"]):
        raise HTTPException(403, "forbidden")
    return run


async def require_run_read(
    run_id: str,
    request: Request,
    domain: str = Query(..., min_length=1),
    user: dict[str, Any] = Depends(current_user),
) -> dict:
    """读类护栏：run 属于该域，且调用者对其所属 KB 可读。"""
    return await _authorize_run(request, run_id, domain, user=user, write=False)


async def require_run_write(
    run_id: str,
    request: Request,
    domain: str = Query(..., min_length=1),
    user: dict[str, Any] = Depends(current_user),
) -> dict:
    """写类护栏（cancel / publish / resume）：额外要求对该 KB 有写权限。"""
    return await _authorize_run(request, run_id, domain, user=user, write=True)


# ── Routes ──

@router.post("/preflight", dependencies=[Depends(require_admin)])
async def preflight_run(body: RunPreflightRequest, request: Request) -> dict[str, Any]:
    resolved_domain = require_domain(body.domain)
    domain_entry = resolve_domain(resolved_domain)
    channel = str(domain_entry.get("default_channel") or "prod").strip() or "prod"
    batch_path = resolve_upload_batch_path(resolved_domain, body.upload_batch_id)
    try:
        binding = await request.app.state.workflow_run_binder.resolve(
            workflow_id=body.workflow_id,
            workflow_version=body.workflow_version,
            domain=resolved_domain,
            channel=channel,
            upload_batch_id=body.upload_batch_id,
            run_overrides={},
        )
    except WorkflowNotFound as exc:
        raise HTTPException(404, detail={"code": "workflow_not_found", "message": str(exc)}) from exc
    except WorkflowArchived as exc:
        raise HTTPException(409, detail={"code": "workflow_archived", "message": str(exc)}) from exc
    pool = await request.app.state.domain_pools.async_pool(resolved_domain)
    return await build_run_preflight(
        pool=pool,
        batch_path=batch_path,
        domain=resolved_domain,
        channel=channel,
        binding=binding,
    )

@router.post("", response_model=RunResponse, status_code=202,
             dependencies=[Depends(require_admin)])
async def create_run(body: CreateRunRequest, request: Request) -> dict:
    """Submit a mining run (async, returns immediately)."""
    # legacy 域级挖掘入口：逐步废弃，推荐走 KB 中心化挖掘（POST /api/kb/{kb_id}/mine）。
    # 域级挖掘写 asset_documents.kb_id=NULL、默认 publish=true 进域级 active release，
    # 与 KB 作用域知识分属两套模型。详见 docs/融合-KB中心化挖掘-设计规格.md。
    # 也正因为影响面是整个域而非某个库，这个入口是 admin-only（见装饰器）。
    logger.warning(
        "Legacy domain mining (/api/runs) is deprecated; prefer KB-centric mining "
        "(POST /api/kb/{kb_id}/mine). domain=%s",
        getattr(body, "domain", None),
    )
    cfg = MiningConfig()
    resolved_domain = require_domain(body.domain)
    engine = cfg.mining_run_submission_engine
    explicit_workflow = body.workflow_id is not None or body.workflow_version is not None
    if engine == "legacy" and explicit_workflow:
        raise HTTPException(
            503,
            detail={
                "code": "workflow_engine_unavailable",
                "message": "Workflow execution is not enabled for new runs",
                "details": {},
            },
        )

    pool = await request.app.state.domain_pools.async_pool(resolved_domain)
    db_config: MiningDbConfig = request.app.state.db_config
    domain_entry = resolve_domain(resolved_domain)
    channel = str(domain_entry.get("default_channel") or "prod").strip() or "prod"

    batch_path = (
        resolve_upload_batch_path(resolved_domain, body.upload_batch_id)
        if body.upload_batch_id
        else None
    )
    input_path = str(batch_path) if batch_path else str(Path(body.input_path or "").resolve())

    llm_base_url = body.llm_base_url or cfg.llm_service_url

    binding = None
    preflight_manifest: dict[str, Any] | None = None
    if engine == "workflow":
        run_overrides: dict[str, Any] = {}
        if body.max_workers is not None:
            run_overrides["maxWorkers"] = body.max_workers
        if body.phase1_only:
            run_overrides["executionMode"] = "assets_only"
        if "publish_on_partial_failure" in body.model_fields_set:
            run_overrides["publishOnPartialFailure"] = body.publish_on_partial_failure
        try:
            binding = await request.app.state.workflow_run_binder.resolve(
                workflow_id=body.workflow_id,
                workflow_version=body.workflow_version,
                domain=resolved_domain,
                channel=channel,
                upload_batch_id=body.upload_batch_id,
                run_overrides=run_overrides,
            )
        except WorkflowNotFound as exc:
            raise HTTPException(
                404,
                detail={
                    "code": "workflow_not_found",
                    "message": str(exc),
                    "details": {},
                },
            ) from exc
        except WorkflowArchived as exc:
            raise HTTPException(
                409,
                detail={
                    "code": "workflow_archived",
                    "message": str(exc),
                    "details": {},
                },
            ) from exc
        except Exception as exc:
            raise HTTPException(
                503,
                detail={
                    "code": "workflow_store_unavailable",
                    "message": "Unable to resolve the selected Workflow version",
                    "details": {},
                },
            ) from exc

        if body.preflight_id:
            current_preflight = await build_run_preflight(
                pool=pool,
                batch_path=batch_path,
                domain=resolved_domain,
                channel=channel,
                binding=binding,
            )
            current_items = {
                (item["relative_path"], item["raw_content_hash"]): item
                for item in current_preflight["items"]
            }
            submitted = {
                (item.relative_path, item.raw_content_hash): item
                for item in body.document_decisions
            }
            if set(current_items) != set(submitted):
                raise HTTPException(
                    409,
                    detail={
                        "code": "preflight_stale",
                        "message": "Uploaded files changed after preflight",
                    },
                )
            confirmed_items = []
            for key, current_item in current_items.items():
                decision = submitted[key]
                if (
                    decision.state_token != current_item["state_token"]
                    or decision.selected_action not in current_item["allowed_actions"]
                ):
                    raise HTTPException(
                        409,
                        detail={
                            "code": "preflight_stale",
                            "message": f"Preflight state changed for {decision.relative_path}",
                        },
                    )
                confirmed_items.append({
                    **current_item,
                    "selected_action": decision.selected_action,
                })
            preflight_manifest = {
                **current_preflight,
                "preflight_id": body.preflight_id,
                "items": confirmed_items,
                "confirmed_at": _utcnow(),
            }

    # Prevent concurrent mining runs within the same domain
    run_lock = _domain_run_lock(resolved_domain)
    if not run_lock.acquire(blocking=False):
        raise HTTPException(
            409,
            f"A mining run is already in progress for domain '{resolved_domain}'. "
            "Please wait for it to complete.",
        )

    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        await AsyncDomainRunRepository(pool).insert_queued_run(
            run_id=run_id,
            input_path=input_path,
            domain=resolved_domain,
            channel=channel,
            execution_engine=engine,
            binding=binding,
            started_at=started_at,
            preflight_manifest=preflight_manifest,
        )
    except Exception:
        run_lock.release()
        raise

    def _mark_thread_failure(error: Exception) -> None:
        try:
            sync_pool = request.app.state.domain_pools.sync_pool(resolved_domain)
            with sync_pool.connection() as conn:
                conn.execute(
                    "UPDATE mining_runs SET status = 'failed', finished_at = %s, "
                    "error_summary = %s WHERE id = %s AND domain = %s "
                    "AND status IN ('queued', 'running')",
                    [_utcnow(), str(error)[:500], run_id, resolved_domain],
                )
        except Exception:
            logger.exception("Unable to record escaped failure for run %s", run_id)

    def _run_in_thread():
        try:
            from knowledge_mining.mining.jobs.run import run as mining_run
            mining_run(
                input_path,
                db_config=db_config,
                phase1_only=body.phase1_only,
                publish_on_partial_failure=body.publish_on_partial_failure,
                llm_base_url=llm_base_url,
                max_workers=body.max_workers,
                domain=resolved_domain,
                run_id=run_id,
            )
        except Exception as e:
            logger.error("Mining run failed: %s", e, exc_info=True)
            _mark_thread_failure(e)
        finally:
            run_lock.release()

    # Pre-create run to get run_id — but the actual run() creates its own.
    # We start the thread and query the run table after.
    thread = threading.Thread(target=_run_in_thread, daemon=True)
    try:
        thread.start()
    except Exception as exc:
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    "UPDATE mining_runs SET status = 'failed', finished_at = %s, "
                    "error_summary = %s WHERE id = %s AND domain = %s AND status = 'queued'",
                    [_utcnow(), str(exc)[:500], run_id, resolved_domain],
                )
        finally:
            run_lock.release()
        raise HTTPException(500, f"Unable to start mining run: {exc}") from exc

    return {
        "run_id": run_id,
        "status": "queued",
        "current_stage": "queued",
        "started_at": started_at,
        "execution_engine": engine,
        "workflow_id": binding.workflow_id if binding else None,
        "workflow_version": binding.workflow_version if binding else None,
        "workflow_graph_hash": binding.graph_hash if binding else None,
    }


@router.get("")
async def list_runs(
    request: Request,
    domain: str = Query(..., min_length=1),
    status: str | None = None,
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict[str, Any] = Depends(current_user),
) -> dict:
    """List mining runs in one domain (身份收敛：非 admin 只见可见 KB 的 run)。"""
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))

    # 非 admin 收窄到可见 KB 集合。legacy 域级 run（kb_id IS NULL）不属于任何库，
    # 对非 admin 一律不可见——它的 input_path 会把 {upload_root}/{kb_id} 暴露出去。
    kb_scope: list[str] | None = None
    if user.get("site_role") != "admin":
        kb_scope = await KbDB(request.app.state.pg_pool).list_visible_kb_ids(
            user_id=user["id"], domain=require_domain(domain),
        )
        if not kb_scope:
            # 一个可见库都没有 → 空页。这里必须早退：kb_id = ANY('{}') 虽然也返回空，
            # 但空数组参数在 psycopg 里推断不出元素类型，会直接报错。
            return {"total": 0, "limit": limit, "offset": offset, "items": []}

    async with pool.connection() as conn:
        conds: list[str] = ["domain = %s"]
        params: list[Any] = [require_domain(domain)]
        if status:
            conds.append("status = %s")
            params.append(status)
        if kb_scope is not None:
            conds.append("kb_id = ANY(%s)")
            params.append(kb_scope)
        where = f"WHERE {' AND '.join(conds)}" if conds else ""

        count_cur = await conn.execute(
            f"SELECT COUNT(*) as c FROM mining_runs {where}", params
        )
        total = (await count_cur.fetchone())["c"]

        cur = await conn.execute(
            f"SELECT id, status, current_stage, input_path, domain, total_documents, "
            f"committed_count, failed_count, skipped_count, "
            f"new_count, updated_count, build_id, started_at, finished_at, "
            f"execution_engine, workflow_id, workflow_version, workflow_version_id, "
            f"workflow_graph_hash, workflow_manifest_json "
            f"FROM mining_runs {where} "
            f"ORDER BY started_at DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = await cur.fetchall()

    items = []
    for row in rows:
        item = dict(row)
        item["workflow"] = _frozen_workflow_summary(item)
        item.pop("workflow_manifest_json", None)
        items.append(item)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@router.get("/{run_id}", dependencies=[Depends(require_run_read)])
async def get_run(run_id: str, request: Request, domain: str = Query(..., min_length=1)) -> dict:
    """Get run details."""
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, source_batch_id, input_path, domain, status, current_stage, build_id, "
            "total_documents, new_count, updated_count, skipped_count, "
            "failed_count, committed_count, started_at, finished_at, "
            "error_summary, metadata_json, execution_engine, workflow_id, "
            "workflow_version, workflow_version_id, workflow_graph_hash, "
            "workflow_manifest_json, active_node_id, active_operator_type, pause_step "
            "FROM mining_runs WHERE id = %s", [run_id]
        )
        run = await cur.fetchone()
        if not run:
            raise HTTPException(404, f"Run {run_id} not found")
        result = dict(run)
        result["workflow"] = _frozen_workflow_summary(result)
        result.pop("workflow_manifest_json", None)
        return result


# 算子化工作流(execution_engine=workflow)逐模块事件映射到 PipelineFlow 的 12 阶段名。
# 工作流节点事件写 mining_workflow_node_events，传统 StreamingPipeline 写 mining_run_stage_events；
# get_run_stages 合并两表，让流水线可视化对两种执行引擎都生效。
_WORKFLOW_NODE_TO_STAGES: dict[str, list[str]] = {
    "parse_segment": ["parse", "segment"],
    "enrich": ["enrich"],
    "contextual_retrieval_enrich": ["discourse"],
    "discourse_line": ["discourse"],
    "retrieval_unit_build": ["retrieval_units"],
    "embedding": ["embedding"],
    "asset_persist": ["db_write"],
    "entity_extract": ["entity_extract"],
    "entity_resolve": ["resolve"],
    "entity_relation_extract": ["entity_relations"],
    "graph_write": ["graph_write"],
    # input_ingest / mining_finalize / ontology_induction / *_review_gate 无对应 12 阶段槽位，跳过。
}
_WORKFLOW_NODE_STATUS_MAP: dict[str, str] = {
    "completed": "completed",
    "started": "started",
    "running": "started",
    "failed": "failed",
    # not_applicable / skipped 不发，对应阶段保持 pending（灰），避免被误判为 running。
}


@router.get("/{run_id}/stages", dependencies=[Depends(require_run_read)])
async def get_run_stages(run_id: str, request: Request, domain: str = Query(..., min_length=1)) -> dict:
    """Get stage timeline for a run.

    合并 mining_run_stage_events（传统引擎：assemble_build/validate_build 等全局阶段）与
    mining_workflow_node_events（算子化引擎：parse_segment/enrich/embedding 等逐模块），
    后者按算子类型映射到 PipelineFlow 认得的阶段名。
    """
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, run_id, stage, status, created_at, duration_ms, "
            "output_summary, error_message, run_document_id "
            "FROM mining_run_stage_events WHERE run_id = %s "
            "ORDER BY created_at",
            [run_id],
        )
        rows = [dict(r) for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT node_id, operator_type, status, started_at, duration_ms, "
            "error_message, run_document_id "
            "FROM mining_workflow_node_events WHERE run_id = %s "
            "ORDER BY started_at",
            [run_id],
        )
        node_rows = await cur.fetchall()

    for nr in node_rows:
        op = nr["operator_type"] or nr["node_id"]
        status = _WORKFLOW_NODE_STATUS_MAP.get(nr["status"])
        if status is None:
            continue
        for stage_name in _WORKFLOW_NODE_TO_STAGES.get(op, []):
            rows.append({
                "id": f"wf-{nr['node_id']}-{stage_name}-{nr['run_document_id'] or 'global'}",
                "run_id": run_id,
                "stage": stage_name,
                "status": status,
                "created_at": nr["started_at"],
                "duration_ms": nr["duration_ms"],
                "output_summary": None,
                "error_message": nr["error_message"],
                "run_document_id": nr["run_document_id"],
            })

    return {"run_id": run_id, "stages": rows}


@router.get("/{run_id}/documents", dependencies=[Depends(require_run_read)])
async def get_run_documents(
    run_id: str,
    request: Request,
    domain: str = Query(..., min_length=1),
    status: str | None = None,
    action: str | None = None,
    has_error: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    """Get document processing results for a run with pagination and filtering."""
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    async with pool.connection() as conn:
        # Build WHERE conditions
        conds: list[str] = ["d.run_id = %s"]
        params: list[Any] = [run_id]
        if status:
            conds.append("d.status = %s")
            params.append(status)
        if action:
            conds.append("d.action = %s")
            params.append(action)
        if has_error is True:
            conds.append("d.error_message IS NOT NULL AND d.error_message != ''")
        where = " AND ".join(conds)

        # Count
        count_cur = await conn.execute(
            f"SELECT COUNT(*) as c FROM mining_run_documents d WHERE {where}", params
        )
        total = (await count_cur.fetchone())["c"]

        # Fetch page
        offset = (page - 1) * page_size
        cur = await conn.execute(
            f"SELECT d.id, d.run_id, d.document_key, d.action, d.status, "
            f"d.document_id, d.document_snapshot_id, d.error_message, "
            f"d.raw_content_hash, d.normalized_content_hash, d.metadata_json "
            f"FROM mining_run_documents d WHERE {where} "
            f"ORDER BY d.document_key LIMIT %s OFFSET %s",
            params + [page_size, offset],
        )
        rows = await cur.fetchall()

        # Batch enrichment: fetch latest stage and duration for all doc IDs
        doc_ids = [r["id"] for r in rows]
        stage_lookup: dict[str, str | None] = {}
        duration_lookup: dict[str, int | None] = {}

        if doc_ids:
            # Latest stage per document (single batch query)
            placeholders = ",".join(["%s"] * len(doc_ids))
            stage_cur = await conn.execute(
                f"SELECT DISTINCT ON (run_document_id) run_document_id, stage "
                f"FROM mining_run_stage_events "
                f"WHERE run_id = %s AND run_document_id IN ({placeholders}) "
                f"ORDER BY run_document_id, created_at DESC",
                [run_id, *doc_ids],
            )
            for sr in await stage_cur.fetchall():
                stage_lookup[sr["run_document_id"]] = sr["stage"]

            # Duration per document (computed in SQL to avoid TEXT type issues)
            dur_cur = await conn.execute(
                f"SELECT run_document_id, "
                f"EXTRACT(EPOCH FROM (MAX(created_at::timestamp) - MIN(created_at::timestamp))) * 1000 as duration_ms "
                f"FROM mining_run_stage_events "
                f"WHERE run_id = %s AND run_document_id IN ({placeholders}) "
                f"AND status IN ('started', 'completed', 'failed') "
                f"GROUP BY run_document_id",
                [run_id, *doc_ids],
            )
            for dr in await dur_cur.fetchall():
                if dr["duration_ms"] is not None:
                    duration_lookup[dr["run_document_id"]] = int(dr["duration_ms"])

        # Enrich documents with computed fields
        documents = []
        for r in rows:
            doc = dict(r)
            # document_name: strip "doc:/" prefix from document_key
            dk = doc.get("document_key", "")
            doc["document_name"] = dk.replace("doc:/", "", 1) if dk.startswith("doc:/") else dk
            doc["current_stage"] = stage_lookup.get(doc["id"])
            doc["duration_ms"] = duration_lookup.get(doc["id"])
            # file_size: 摄取时存进 metadata_json 的原始文件字节大小（JSONB → dict）
            meta = doc.pop("metadata_json", None) or {}
            if not isinstance(meta, dict):
                meta = {}
            doc["file_size"] = meta.get("file_size")
            doc["skip_reason"] = meta.get("skip_reason")
            doc.update(_expand_preprocess_metadata(meta))
            documents.append(doc)

    return {
        "run_id": run_id,
        "total": total,
        "page": page,
        "page_size": page_size,
        "documents": documents,
    }


@router.get("/{run_id}/progress", dependencies=[Depends(require_run_read)])
async def get_run_progress(run_id: str, request: Request, domain: str = Query(..., min_length=1)) -> dict:
    """Get run progress — aggregated from mining_run_documents + mining_run_stage_events."""
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    async with pool.connection() as conn:
        # Verify run exists
        run_cur = await conn.execute(
            "SELECT id, status, current_stage, total_documents FROM mining_runs "
            "WHERE id = %s AND domain = %s", [run_id, require_domain(domain)]
        )
        run = await run_cur.fetchone()
        if not run:
            raise HTTPException(404, f"Run {run_id} not found")

        # Document status counts
        doc_cur = await conn.execute(
            "SELECT status, COUNT(*) as cnt FROM mining_run_documents "
            "WHERE run_id = %s GROUP BY status",
            [run_id],
        )
        doc_rows = await doc_cur.fetchall()
        status_counts = {r["status"]: r["cnt"] for r in doc_rows}

        # Stage summary: for each stage, count completed/failed
        stage_cur = await conn.execute(
            "SELECT stage, status, COUNT(*) as cnt FROM mining_run_stage_events "
            "WHERE run_id = %s AND run_document_id IS NOT NULL "
            "GROUP BY stage, status",
            [run_id],
        )
        stage_rows = await stage_cur.fetchall()

        # Build stage_summary
        stage_summary: dict[str, dict[str, int]] = {}
        for r in stage_rows:
            stage_summary.setdefault(r["stage"], {"done": 0, "failed": 0})
            if r["status"] == "completed":
                stage_summary[r["stage"]]["done"] += r["cnt"]
            elif r["status"] == "failed":
                stage_summary[r["stage"]]["failed"] += r["cnt"]

        # Determine current stage: latest started event without a matching completed
        current_stage_cur = await conn.execute(
            "SELECT e.stage FROM mining_run_stage_events e "
            "WHERE e.run_id = %s AND e.run_document_id IS NOT NULL AND e.status = 'started' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM mining_run_stage_events e2 "
            "  WHERE e2.run_id = e.run_id AND e2.run_document_id = e.run_document_id "
            "  AND e2.stage = e.stage AND e2.status IN ('completed', 'failed') "
            "  AND e2.created_at > e.created_at"
            ") ORDER BY e.created_at DESC LIMIT 1",
            [run_id],
        )
        current_stage_row = await current_stage_cur.fetchone()
        current_stage = current_stage_row["stage"] if current_stage_row else None

        # 全局尾段（run_document_id IS NULL）：落图 + 建库/发布。这些阶段在所有文档
        # 提交之后才整体跑一次，进度条必须把它们算进去——否则会出现"文档 100%、整轮
        # 其实还在落图/构建"的误导性满格。
        global_cur = await conn.execute(
            "SELECT stage FROM mining_run_stage_events "
            "WHERE run_id = %s AND run_document_id IS NULL AND status = 'completed' "
            "GROUP BY stage",
            [run_id],
        )
        global_done_stages = {r["stage"] for r in await global_cur.fetchall()}

    total = run["total_documents"] or 0
    completed = status_counts.get("committed", 0)
    failed = status_counts.get("failed", 0)
    skipped = status_counts.get("skipped", 0)
    processing = status_counts.get("processing", 0) + status_counts.get("pending", 0)

    # 进度 = 文档单元 + 全局尾段单元。每篇文档算 1 个单元，每个全局尾段也算 1 个单元，
    # 这样"18 篇全提交"只到 ~82%，落图/建库/发布各自完成才继续往上爬到 100%。
    GLOBAL_TAIL_STAGES = ("graph_write", "assemble_build", "validate_build", "publish_release")
    done_global = sum(1 for s in GLOBAL_TAIL_STAGES if s in global_done_stages)
    total_units = total + len(GLOBAL_TAIL_STAGES)
    done_units = completed + failed + skipped + done_global
    progress_percent = round(done_units / total_units * 100, 1) if total_units else 0.0
    # 只有整轮真正结束（status=completed）才显示 100%；尾段还在跑、或尾段被跳过
    # （如无 active 本体跳过落图）导致分子凑不满时，封顶在 99.9% 以免误导。
    if run["status"] == "completed":
        progress_percent = 100.0
    elif progress_percent >= 100.0:
        progress_percent = 99.9

    return {
        "run_id": run_id,
        "total": total,
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "processing": processing,
        "progress_percent": progress_percent,
        "current_stage": current_stage,
        "run_stage": run["current_stage"],
        "stage_summary": stage_summary,
        "global_done_stages": sorted(global_done_stages),
    }


@router.get("/{run_id}/documents/{doc_id}", dependencies=[Depends(require_run_read)])
async def get_run_document(
    run_id: str, doc_id: str, request: Request,
    domain: str = Query(..., min_length=1),
) -> dict:
    """Get single document detail within a run."""
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT d.id, d.run_id, d.document_key, d.action, d.status, "
            "d.document_id, d.document_snapshot_id, d.error_message, "
            "d.raw_content_hash, d.normalized_content_hash, d.started_at, d.finished_at, "
            "d.metadata_json "
            "FROM mining_run_documents d "
            "WHERE d.id = %s AND d.run_id = %s",
            [doc_id, run_id],
        )
        doc = await cur.fetchone()
        if not doc:
            raise HTTPException(404, f"Document {doc_id} not found in run {run_id}")

        result = dict(doc)
        # 跳过原因：SKIP/RESTORE 由 run job 在登记时写入，管线内跳过由 tracker 写入。
        meta = result.pop("metadata_json", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        result["skip_reason"] = meta.get("skip_reason")
        # 明细：解析器异常文本、不支持的 file_type 等；预处理失败在登记时就写好了。
        result["skip_reason_detail"] = (
            meta.get("skip_reason_detail") or meta.get("preprocess_error")
        )
        result["file_size"] = meta.get("file_size")
        result.update(_expand_preprocess_metadata(meta))
        # Compute document_name
        dk = result.get("document_key", "")
        result["document_name"] = dk.replace("doc:/", "", 1) if dk.startswith("doc:/") else dk

    return result


@router.get("/{run_id}/documents/{doc_id}/stages", dependencies=[Depends(require_run_read)])
async def get_run_document_stages(
    run_id: str, doc_id: str, request: Request,
    domain: str = Query(..., min_length=1),
) -> dict:
    """Get stage timeline for a single document within a run."""
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    async with pool.connection() as conn:
        # Verify doc belongs to run
        check_cur = await conn.execute(
            "SELECT id FROM mining_run_documents WHERE id = %s AND run_id = %s",
            [doc_id, run_id],
        )
        if not await check_cur.fetchone():
            raise HTTPException(404, f"Document {doc_id} not found in run {run_id}")

        cur = await conn.execute(
            "SELECT id, stage, status, created_at, duration_ms, "
            "output_summary, error_message "
            "FROM mining_run_stage_events "
            "WHERE run_id = %s AND run_document_id = %s "
            "ORDER BY created_at",
            [run_id, doc_id],
        )
        rows = await cur.fetchall()

    return {"run_id": run_id, "document_id": doc_id, "stages": [dict(r) for r in rows]}


@router.get("/{run_id}/documents/{doc_id}/artifacts", dependencies=[Depends(require_run_read)])
async def get_run_document_artifacts(
    run_id: str, doc_id: str, request: Request,
    domain: str = Query(..., min_length=1),
) -> dict:
    """Get artifact counts for a single document (via document_snapshot_id)."""
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    async with pool.connection() as conn:
        # Get document_snapshot_id
        doc_cur = await conn.execute(
            "SELECT id, document_snapshot_id FROM mining_run_documents "
            "WHERE id = %s AND run_id = %s",
            [doc_id, run_id],
        )
        doc = await doc_cur.fetchone()
        if not doc:
            raise HTTPException(404, f"Document {doc_id} not found in run {run_id}")

        snapshot_id = doc["document_snapshot_id"]
        if not snapshot_id:
            return {"run_id": run_id, "document_id": doc_id, "snapshot_id": None,
                    "segment_count": 0, "unit_count": 0, "relation_count": 0}

        # Count segments
        seg_cur = await conn.execute(
            "SELECT COUNT(*) as c FROM asset_raw_segments WHERE document_snapshot_id = %s",
            [snapshot_id],
        )
        segment_count = (await seg_cur.fetchone())["c"]

        # Count retrieval units
        unit_cur = await conn.execute(
            "SELECT COUNT(*) as c FROM asset_retrieval_units WHERE document_snapshot_id = %s",
            [snapshot_id],
        )
        unit_count = (await unit_cur.fetchone())["c"]

        # Count relations
        rel_cur = await conn.execute(
            "SELECT COUNT(*) as c FROM asset_raw_segment_relations WHERE document_snapshot_id = %s",
            [snapshot_id],
        )
        relation_count = (await rel_cur.fetchone())["c"]

    return {
        "run_id": run_id,
        "document_id": doc_id,
        "snapshot_id": snapshot_id,
        "segment_count": segment_count,
        "unit_count": unit_count,
        "relation_count": relation_count,
    }


@router.get("/{run_id}/documents/{doc_id}/segments", dependencies=[Depends(require_run_read)])
async def get_run_document_segments(
    run_id: str, doc_id: str, request: Request,
    domain: str = Query(..., min_length=1),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Get segments for a single document in run context."""
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    async with pool.connection() as conn:
        doc_cur = await conn.execute(
            "SELECT document_snapshot_id FROM mining_run_documents "
            "WHERE id = %s AND run_id = %s",
            [doc_id, run_id],
        )
        doc = await doc_cur.fetchone()
        if not doc:
            raise HTTPException(404, f"Document {doc_id} not found in run {run_id}")

        snapshot_id = doc["document_snapshot_id"]
        if not snapshot_id:
            return {"run_id": run_id, "document_id": doc_id, "snapshot_id": None, "total": 0, "limit": limit, "offset": offset, "items": []}

        count_cur = await conn.execute(
            "SELECT COUNT(*) as c FROM asset_raw_segments WHERE document_snapshot_id = %s",
            [snapshot_id],
        )
        total = (await count_cur.fetchone())["c"]

        cur = await conn.execute(
            "SELECT id, segment_key, segment_index, block_type, semantic_role, "
            "section_title, raw_text, token_count "
            "FROM asset_raw_segments "
            "WHERE document_snapshot_id = %s "
            "ORDER BY segment_index LIMIT %s OFFSET %s",
            [snapshot_id, limit, offset],
        )
        rows = await cur.fetchall()

    return {
        "run_id": run_id, "document_id": doc_id, "snapshot_id": snapshot_id,
        "total": total, "limit": limit, "offset": offset,
        "items": [dict(r) for r in rows],
    }


@router.get("/{run_id}/documents/{doc_id}/units", dependencies=[Depends(require_run_read)])
async def get_run_document_units(
    run_id: str, doc_id: str, request: Request,
    domain: str = Query(..., min_length=1),
    unit_type: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Get retrieval units for a single document in run context."""
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    async with pool.connection() as conn:
        doc_cur = await conn.execute(
            "SELECT document_snapshot_id FROM mining_run_documents "
            "WHERE id = %s AND run_id = %s",
            [doc_id, run_id],
        )
        doc = await doc_cur.fetchone()
        if not doc:
            raise HTTPException(404, f"Document {doc_id} not found in run {run_id}")

        snapshot_id = doc["document_snapshot_id"]
        if not snapshot_id:
            return {"run_id": run_id, "document_id": doc_id, "snapshot_id": None, "total": 0, "limit": limit, "offset": offset, "items": []}

        where = "AND unit_type = %s" if unit_type else ""
        params: list[str] = [snapshot_id] + ([unit_type] if unit_type else [])

        count_cur = await conn.execute(
            f"SELECT COUNT(*) as c FROM asset_retrieval_units WHERE document_snapshot_id = %s {where}",
            params,
        )
        total = (await count_cur.fetchone())["c"]

        cur = await conn.execute(
            f"SELECT id, unit_key, unit_type, target_type, title, text, "
            f"block_type, semantic_role, weight, created_at "
            f"FROM asset_retrieval_units "
            f"WHERE document_snapshot_id = %s {where} "
            f"ORDER BY created_at LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = await cur.fetchall()

    return {
        "run_id": run_id, "document_id": doc_id, "snapshot_id": snapshot_id,
        "total": total, "limit": limit, "offset": offset,
        "items": [dict(r) for r in rows],
    }


@router.get("/{run_id}/documents/{doc_id}/relations", dependencies=[Depends(require_run_read)])
async def get_run_document_relations(
    run_id: str, doc_id: str, request: Request,
    domain: str = Query(..., min_length=1),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Get segment relations for a single document in run context."""
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    async with pool.connection() as conn:
        doc_cur = await conn.execute(
            "SELECT document_snapshot_id FROM mining_run_documents "
            "WHERE id = %s AND run_id = %s",
            [doc_id, run_id],
        )
        doc = await doc_cur.fetchone()
        if not doc:
            raise HTTPException(404, f"Document {doc_id} not found in run {run_id}")

        snapshot_id = doc["document_snapshot_id"]
        if not snapshot_id:
            return {"run_id": run_id, "document_id": doc_id, "snapshot_id": None, "total": 0, "limit": limit, "offset": offset, "items": []}

        count_cur = await conn.execute(
            "SELECT COUNT(*) as c FROM asset_raw_segment_relations WHERE document_snapshot_id = %s",
            [snapshot_id],
        )
        total = (await count_cur.fetchone())["c"]

        # Join with segments to get preview text
        cur = await conn.execute(
            "SELECT r.id, r.document_snapshot_id, r.source_segment_id, "
            "r.target_segment_id, r.relation_type, r.weight, "
            "r.confidence, r.distance, "
            "s1.raw_text AS source_text, s2.raw_text AS target_text "
            "FROM asset_raw_segment_relations r "
            "LEFT JOIN asset_raw_segments s1 ON s1.id = r.source_segment_id "
            "LEFT JOIN asset_raw_segments s2 ON s2.id = r.target_segment_id "
            "WHERE r.document_snapshot_id = %s "
            "ORDER BY r.confidence DESC NULLS LAST LIMIT %s OFFSET %s",
            [snapshot_id, limit, offset],
        )
        rows = await cur.fetchall()

    return {
        "run_id": run_id, "document_id": doc_id, "snapshot_id": snapshot_id,
        "total": total, "limit": limit, "offset": offset,
        "items": [dict(r) for r in rows],
    }


@router.get("/{run_id}/artifacts", dependencies=[Depends(require_run_read)])
async def get_run_artifacts(run_id: str, request: Request, domain: str = Query(..., min_length=1)) -> dict:
    """Get run-level artifact aggregation."""
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    async with pool.connection() as conn:
        # Verify run exists
        run_cur = await conn.execute(
            "SELECT id FROM mining_runs WHERE id = %s", [run_id]
        )
        if not await run_cur.fetchone():
            raise HTTPException(404, f"Run {run_id} not found")

        # Collect all snapshot IDs for this run
        snap_cur = await conn.execute(
            "SELECT document_snapshot_id FROM mining_run_documents "
            "WHERE run_id = %s AND document_snapshot_id IS NOT NULL",
            [run_id],
        )
        snap_rows = await snap_cur.fetchall()
        snapshot_ids = [r["document_snapshot_id"] for r in snap_rows]

        if not snapshot_ids:
            return {"run_id": run_id, "document_count": 0,
                    "segment_count": 0, "unit_count": 0, "relation_count": 0}

        placeholders = ",".join(["%s"] * len(snapshot_ids))

        seg_cur = await conn.execute(
            f"SELECT COUNT(*) as c FROM asset_raw_segments "
            f"WHERE document_snapshot_id IN ({placeholders})", snapshot_ids
        )
        segment_count = (await seg_cur.fetchone())["c"]

        unit_cur = await conn.execute(
            f"SELECT COUNT(*) as c FROM asset_retrieval_units "
            f"WHERE document_snapshot_id IN ({placeholders})", snapshot_ids
        )
        unit_count = (await unit_cur.fetchone())["c"]

        rel_cur = await conn.execute(
            f"SELECT COUNT(*) as c FROM asset_raw_segment_relations "
            f"WHERE document_snapshot_id IN ({placeholders})", snapshot_ids
        )
        relation_count = (await rel_cur.fetchone())["c"]

    return {
        "run_id": run_id,
        "document_count": len(snapshot_ids),
        "segment_count": segment_count,
        "unit_count": unit_count,
        "relation_count": relation_count,
    }


@router.post("/{run_id}/cancel", response_model=CancelRunResponse,
             dependencies=[Depends(require_run_write)])
async def cancel_run(run_id: str, request: Request, domain: str = Query(..., min_length=1)) -> dict:
    """Cancel a running run (best-effort)."""
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE mining_runs SET status = 'cancelled', finished_at = %s "
            "WHERE id = %s AND domain = %s AND status IN ('queued', 'running') "
            "AND current_stage <> 'publishing' "
            "RETURNING status",
            [_utcnow(), run_id, require_domain(domain)],
        )
        cancelled = await cur.fetchone()
        if cancelled is None:
            cur = await conn.execute(
                "SELECT id, status, current_stage FROM mining_runs WHERE id = %s AND domain = %s",
                [run_id, require_domain(domain)],
            )
            run = await cur.fetchone()
            if run is None:
                raise HTTPException(404, f"Run {run_id} not found")
            if run.get("current_stage") == "publishing":
                raise HTTPException(400, f"Run {run_id} is publishing, cannot cancel")
            raise HTTPException(400, f"Run {run_id} is {run['status']}, cannot cancel")

    return {"run_id": run_id, "status": "cancelled", "message": "Run cancellation requested"}


class PublishRunRequest(BaseModel):
    domain: str | None = None


@router.post("/{run_id}/publish", dependencies=[Depends(require_run_write)])
async def publish_run(
    run_id: str, request: Request,
    domain: str = Query(..., min_length=1),
    body: PublishRunRequest | None = None,
) -> dict:
    """Publish a completed run's build as active release."""
    db_config: MiningDbConfig = request.app.state.db_config
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    try:
        publish_domain = require_domain(domain)

        from knowledge_mining.mining.jobs.run import publish
        result = publish(run_id, domain=publish_domain, db_config=db_config)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Publish failed: {e}")


@router.get("/{run_id}/trace", dependencies=[Depends(require_run_read)])
async def get_run_trace(run_id: str, request: Request, domain: str = Query(..., min_length=1)) -> dict:
    """B7 挖掘过程透视：在常规 run 详情之上叠加本体/图谱视角的概览。

    返回 run 状态（含 awaiting_review 的 subloop_stage）+ 两道检查点的待办数 +
    该 run 落图的对象/边规模，供前端"透明前端"页一屏看清这次挖掘干了什么。
    """
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    async with pool.connection() as conn:
        run_cur = await conn.execute(
            "SELECT id, domain, status, current_stage, subloop_stage, ontology_version_id, "
            "total_documents, committed_count, new_count, updated_count, "
            "failed_count, skipped_count, started_at, finished_at, build_id, "
            "execution_engine, workflow_id, workflow_version, workflow_version_id, "
            "workflow_graph_hash, workflow_manifest_json, active_node_id, "
            "active_operator_type, pause_step "
            "FROM mining_runs WHERE id = %s", [run_id]
        )
        run = await run_cur.fetchone()
        if not run:
            raise HTTPException(404, f"Run {run_id} not found")
        run_domain = run["domain"]

        # 本体确认: 该领域待审本体候选数
        cand_cur = await conn.execute(
            "SELECT COUNT(*) AS n FROM ontology_candidates "
            "WHERE domain_id = %s AND status = 'proposed'", [run_domain]
        )
        proposed_candidates = (await cand_cur.fetchone())["n"]

        # 本体线 entity_extract 产出：逃生口候选数（source='escape_hatch'，含待审/已处理）
        esc_cur = await conn.execute(
            "SELECT COUNT(*) AS n FROM ontology_candidates "
            "WHERE domain_id = %s AND source = 'escape_hatch'", [run_domain]
        )
        escape_hatch_candidates = (await esc_cur.fetchone())["n"]

        # 实体确认: 该 run 处理快照下的待审 mention 数
        ment_cur = await conn.execute(
            "SELECT COUNT(*) AS n FROM asset_segment_entity_mentions m "
            "JOIN mining_run_documents d ON d.document_snapshot_id = m.document_snapshot_id "
            "WHERE d.run_id = %s AND m.resolve_status = 'pending'", [run_id]
        )
        pending_mentions = (await ment_cur.fetchone())["n"]

        # 该 run 落图规模（对象/边按 domain 计；MVP 不按 snapshot 精确切分）
        ent_cur = await conn.execute(
            "SELECT COUNT(*) AS n FROM ontology_entities WHERE domain_id = %s", [run_domain]
        )
        entity_count = (await ent_cur.fetchone())["n"]
        rel_cur = await conn.execute(
            "SELECT COUNT(*) AS n FROM ontology_entity_relations WHERE domain_id = %s", [run_domain]
        )
        relation_count = (await rel_cur.fetchone())["n"]

        stage_cur = await conn.execute(
            "SELECT id, run_id, run_document_id, stage, status, created_at, "
            "duration_ms, output_summary, error_message "
            "FROM mining_run_stage_events WHERE run_id = %s ORDER BY created_at",
            [run_id],
        )
        stage_events = [dict(item) for item in await stage_cur.fetchall()]

        document_cur = await conn.execute(
            "SELECT id, document_key, action, status, document_id, "
            "document_snapshot_id, error_message FROM mining_run_documents "
            "WHERE run_id = %s ORDER BY document_key",
            [run_id],
        )
        documents = [dict(item) for item in await document_cur.fetchall()]

        node_events: list[dict[str, Any]] = []
        if run.get("execution_engine") == "workflow":
            node_cur = await conn.execute(
                "SELECT id, run_id, run_document_id, node_id, operator_type, "
                "operator_version, status, attempt_no, started_at, finished_at, "
                "duration_ms, input_summary_json, output_summary_json, error_code, "
                "error_message, metadata_json FROM mining_workflow_node_events "
                "WHERE run_id = %s ORDER BY started_at, node_id, attempt_no",
                [run_id],
            )
            node_events = [dict(item) for item in await node_cur.fetchall()]

    warnings = []
    for event in node_events:
        metadata = event.get("metadata_json") or {}
        if not isinstance(metadata, dict):
            continue
        for warning in metadata.get("warnings") or []:
            if not isinstance(warning, dict):
                continue
            warnings.append({
                "node_id": event.get("node_id"),
                "attempt_no": event.get("attempt_no"),
                "code": warning.get("code"),
                "message": warning.get("message"),
            })

    return {
        "run_id": run_id,
        "domain": run_domain,
        "status": run["status"],
        "current_stage": run.get("current_stage"),
        "subloop_stage": run["subloop_stage"],
        "ontology_version_id": run["ontology_version_id"],
        "awaiting_review": run["status"] == "awaiting_review",
        "active_gate": run["subloop_stage"] if run["status"] == "awaiting_review" else None,
        "counts": {
            "total_documents": run["total_documents"],
            "committed": run["committed_count"],
            "new": run["new_count"],
            "updated": run["updated_count"],
            "failed": run["failed_count"],
            "skipped": run["skipped_count"],
        },
        "ontology_proposed_candidates": proposed_candidates,
        "entity_pending_mentions": pending_mentions,
        "entity_count": entity_count,
        "relation_count": relation_count,
        "escape_hatch_candidates": escape_hatch_candidates,
        "execution_engine": run.get("execution_engine") or "legacy",
        "workflow": _frozen_workflow_summary(dict(run), include_graph=True),
        "active_node_id": run.get("active_node_id"),
        "active_operator_type": run.get("active_operator_type"),
        "pause_step": run.get("pause_step"),
        "stage_events": stage_events,
        "node_events": node_events,
        "documents": documents,
        "warnings": warnings,
        "asset_counts": {
            "entities": entity_count,
            "relations": relation_count,
        },
        "build_id": run.get("build_id"),
    }


class ResumeRunRequest(BaseModel):
    domain: str | None = None
    publish_on_partial_failure: bool = False


def _is_run_resumable(
    *,
    execution_engine: str,
    status: str,
    subloop_stage: str | None,
    finished_at: Any,
) -> bool:
    if status == "awaiting_review":
        return True
    if execution_engine == "workflow":
        return status in {"failed", "interrupted"} or (
            status == "running" and finished_at is None
        )
    return status == "running" and subloop_stage == "done" and finished_at is None


@router.post("/{run_id}/resume", dependencies=[Depends(require_run_write)])
async def resume_run(
    run_id: str, request: Request,
    domain: str = Query(..., min_length=1),
    body: ResumeRunRequest | None = None,
) -> dict:
    """Resume a review-paused or recoverable interrupted mining Run.

    重新评估两道检查点：仍有待办 → 保持 awaiting_review 刷新 subloop_stage；
    都清空 → 从 graph_write 之后续跑（建库 + 发布），不重抽文档。

    **异步执行**：续跑要跑 LLM 归纳 + 建库发布，可能耗时数分钟。若同步等结果，
    上游网关会先超时（504），用户重试又撞上已变更的状态（400）。所以这里只做快速校验，
    重活丢后台线程，立即返回 status=resuming，让前端轮询 run 状态看进度（与初始挖掘一致）。
    """
    db_config: MiningDbConfig = request.app.state.db_config
    pool = await request.app.state.domain_pools.async_pool(require_domain(domain))
    await _require_run_domain(pool, run_id, domain)

    # 快速读当前 run 状态（同时拿 domain，避免再查一次）
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT domain, status, subloop_stage, finished_at, execution_engine "
            "FROM mining_runs WHERE id = %s",
            [run_id],
        )
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, f"Run {run_id} not found")

    resume_domain = require_domain(domain)
    status = row["status"]
    stage = row["subloop_stage"]
    finished = row["finished_at"]

    # 已完成：别报错，直接告诉前端"无需继续"——重试已跑完的 run 不应是错误。
    if status == "completed":
        return {"run_id": run_id, "status": "completed", "message": "该 run 已完成，无需继续"}

    # Workflow additionally resumes failed/interrupted/crashed running states;
    # legacy keeps its historical awaiting_review and running/done policy.
    resumable = _is_run_resumable(
        execution_engine=str(row.get("execution_engine") or "legacy"),
        status=status,
        subloop_stage=stage,
        finished_at=finished,
    )
    if not resumable:
        raise HTTPException(
            400, f"Run {run_id} 当前状态为 {status}{f'/{stage}' if stage else ''}，无法继续挖掘")

    publish_partial = body.publish_on_partial_failure if body else False

    # 防并发：和初始挖掘共用本域那把锁，避免续跑与新挖掘互相踩（跨域不互斥）。
    run_lock = _domain_run_lock(resume_domain)
    if not run_lock.acquire(blocking=False):
        raise HTTPException(409, f"域 {resume_domain} 已有挖掘任务在进行中，请稍候再试")

    def _resume_in_thread():
        try:
            from knowledge_mining.mining.jobs.run import resume as resume_run_job
            resume_run_job(
                run_id, domain=resume_domain, db_config=db_config,
                publish_on_partial_failure=publish_partial,
            )
        except Exception as e:
            logger.error("Resume run failed: %s", e, exc_info=True)
        finally:
            run_lock.release()

    threading.Thread(target=_resume_in_thread, daemon=True).start()
    return {"run_id": run_id, "status": "resuming", "message": "已开始继续挖掘，请稍候查看进度"}


def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
