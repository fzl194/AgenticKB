"""KB mining trigger — POST /api/kb/{kb_id}/mine.

KB 中心化挖掘（融合设计 §5.2）：KB 按自选挖掘范式（knowledge_bases.mining_workflow_id）
走 **workflow 引擎**（不再走 legacy），范式绑定四元组从 workflow_run_binder 解析；
run 行写 kb_id + metadata.publish=false（只 build 不 publish 到域级 active release，
避免 B1 同域多库互相 retire）；产物经 existing_doc→asset_documents.kb_id 自然归属 KB。

整库增量：input_path 锁定 KB 上传目录，workflow 的 _prepare_document_states 在无 preflight
时按 storage_path 走 get_document_lifecycle_state，自动判 SKIP/UPDATE/NEW（未变文档零成本）。
身份定位（G1）：storage_path 含 <kb_id> 前缀、全库唯一，文件改名/移动后仍命中同一身份。

镜像 api/routes/runs.py:create_run 的绑定解析、insert_queued_run、线程异常处理（_mark_thread_failure
+ start() 包 try，防域锁死锁）。不同处：engine 恒为 workflow、workflow_id 取自 KB.mining_workflow_id、
写 kb_id、publish=false。

选择性挖掘：请求体可选 document_ids（asset_documents.id 列表），非空时只挖这些文档；
省略/空 → 整库增量。所选 id 经 metadata_json.document_ids 透传，_prepare_document_states
读取后按 storage_path 过滤 ingest_directory 的扫描结果。
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from knowledge_mining.mining.api.routes.runs import _domain_run_lock
from knowledge_mining.mining.infra.domain_pack import resolve_domain
from knowledge_mining.mining.infra.upload_config import UploadConfig
from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.kb.deps import get_kb_db
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.workflow.repositories.domain_run_repository import (
    AsyncDomainRunRepository,
)
from knowledge_mining.mining.workflow.service import WorkflowArchived, WorkflowNotFound

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kb/{kb_id}/mine", tags=["kb-mining"])


class MineKbBody(BaseModel):
    """选择性挖掘：document_ids 非空时只挖这些文档；省略/空 → 整库增量。
    force_redo=True 强制重挖（无视内容哈希去重，重生已挖文档的派生资产）。"""

    document_ids: list[str] | None = None
    force_redo: bool = False


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("", status_code=202)
async def mine_kb(
    kb_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
    body: MineKbBody | None = Body(None),
) -> dict[str, Any]:
    """Trigger workflow mining for a KB's uploaded documents.

    整库增量（默认）：Lifecycle SKIP/RESTORE 保证未变文档零成本，只挖新增/变更。
    选择性挖掘：body.document_ids 非空时只挖所选文档子集。
    Requires the KB to have selected a mining paradigm (mining_workflow_id).
    """
    kb = await kbdb.get_kb(kb_id)
    if kb is None or not await kbdb.is_visible(kb_id=kb_id, user_id=user["id"]):
        # 看不到 → 404（不泄露存在性）
        raise HTTPException(404, f"KB {kb_id} not found")
    if not await kbdb.can_write(kb_id=kb_id, user_id=user["id"]):
        raise HTTPException(403, "only owner or editor may trigger mining")

    mining_workflow_id = kb.get("mining_workflow_id")
    if not mining_workflow_id:
        raise HTTPException(
            400, "KB has no mining paradigm selected; set mining_workflow_id first"
        )

    domain = kb["domain"]
    input_path = str((UploadConfig().upload_root_path / kb_id).resolve())
    if not Path(input_path).is_dir():
        raise HTTPException(400, "KB has no uploaded files to mine")

    db_config = request.app.state.db_config
    domain_entry = resolve_domain(domain)
    channel = str(domain_entry.get("default_channel") or "prod").strip() or "prod"

    # 解析范式绑定：KB 选的范式 → 其 current_version 的不可变 manifest（workflow_id/version/version_id/graph_hash）。
    # 错误结构镜像 create_run：固定 code+message，不把 str(exc) 回传客户端（避免泄露内部细节）。
    try:
        binding = await request.app.state.workflow_run_binder.resolve(
            workflow_id=mining_workflow_id,
            workflow_version=None,  # None → 取该范式 current_version
            domain=domain,
            channel=channel,
            upload_batch_id=None,
            run_overrides={},
        )
    except WorkflowNotFound:
        raise HTTPException(
            404,
            detail={"code": "workflow_not_found", "message": "mining paradigm not found", "details": {}},
        )
    except WorkflowArchived:
        raise HTTPException(
            409,
            detail={"code": "workflow_archived", "message": "mining paradigm archived", "details": {}},
        )
    except Exception:
        logger.exception("KB mining binding resolve failed for kb=%s workflow=%s", kb_id, mining_workflow_id)
        raise HTTPException(
            503,
            detail={"code": "workflow_store_unavailable", "message": "Unable to resolve the selected mining paradigm", "details": {}},
        )

    pool = await request.app.state.domain_pools.async_pool(domain)

    # 档2：范式签名变更自动失效。签名 = workflow_id:version:graph_hash，比对上一条已完成 run；
    # 不同（范式/版本/图变了）则自动 force_redo，让派生资产重生。用户也可显式 force_redo。
    signature = f"{binding.workflow_id}:{binding.workflow_version}:{binding.graph_hash}"
    user_force_redo = bool(body and body.force_redo)
    auto_force_redo = False
    prev_sig: str | None = None
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT metadata_json->>'signature' AS sig FROM mining_runs "
            "WHERE kb_id = %s AND status IN ('completed', 'succeeded', 'completed_with_errors') "
            "ORDER BY finished_at DESC NULLS LAST LIMIT 1",
            [kb_id],
        )
        _prev = await cur.fetchone()
        if _prev and _prev["sig"]:
            prev_sig = _prev["sig"]
            if prev_sig != signature:
                auto_force_redo = True
    force_redo = user_force_redo or auto_force_redo
    if auto_force_redo:
        logger.info(
            "KB %s paradigm signature changed (%s -> %s); auto force_redo",
            kb_id, prev_sig, signature,
        )

    run_lock = _domain_run_lock(domain)
    if not run_lock.acquire(blocking=False):
        raise HTTPException(
            409, f"A mining run is already in progress for domain '{domain}'.",
        )

    run_id = uuid.uuid4().hex
    started_at = _utcnow()
    try:
        await AsyncDomainRunRepository(pool).insert_queued_run(
            run_id=run_id,
            input_path=input_path,
            domain=domain,
            channel=channel,
            execution_engine="workflow",
            binding=binding,
            started_at=started_at,
            preflight_manifest=None,
        )
        # KB 挖掘专属：标 kb_id 归属 + 抑制 publish（只 build 不进域级 active release，避 B1）。
        # 选择性挖掘：document_ids 非空时一并写入，_prepare_document_states 据此按 storage_path 过滤。
        # metadata_json 用 || 合并，保留 insert_queued_run 写入的其它键。
        meta_patch: dict[str, Any] = {
            "kb_id": kb_id,
            "publish": False,
            "force_redo": force_redo,
            "signature": signature,
        }
        document_ids = body.document_ids if body and body.document_ids else None
        if document_ids:
            meta_patch["document_ids"] = document_ids
        async with pool.connection() as conn:
            await conn.execute(
                "UPDATE mining_runs SET kb_id = %s, "
                "metadata_json = metadata_json || %s::jsonb WHERE id = %s",
                [kb_id, json.dumps(meta_patch), run_id],
            )
    except Exception:
        run_lock.release()
        raise

    # 镜像 create_run 的线程异常兜底：start() 失败要释放锁 + 标 failed；run 抛错要标 failed。
    def _mark_thread_failure(error: Exception) -> None:
        try:
            sync_pool = request.app.state.domain_pools.sync_pool(domain)
            with sync_pool.connection() as conn:
                conn.execute(
                    "UPDATE mining_runs SET status = 'failed', finished_at = %s, "
                    "error_summary = %s WHERE id = %s AND domain = %s "
                    "AND status IN ('queued', 'running')",
                    [_utcnow(), str(error)[:500], run_id, domain],
                )
        except Exception:
            logger.exception("Unable to record escaped failure for run %s", run_id)

    def _run_in_thread() -> None:
        try:
            from knowledge_mining.mining.jobs.run import run as mining_run
            mining_run(input_path, db_config=db_config, domain=domain, run_id=run_id)
        except Exception as e:
            logger.exception("KB mining run %s failed", run_id)
            _mark_thread_failure(e)
        finally:
            run_lock.release()

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    try:
        thread.start()
    except Exception as exc:
        # start() 失败（资源不足等）：必须释放锁 + 标 failed，否则该 domain 永久 409。
        try:
            async with pool.connection() as conn:
                await conn.execute(
                    "UPDATE mining_runs SET status = 'failed', finished_at = %s, "
                    "error_summary = %s WHERE id = %s AND domain = %s AND status = 'queued'",
                    [_utcnow(), str(exc)[:500], run_id, domain],
                )
        except Exception:
            logger.exception("Unable to mark run %s failed after thread start failure", run_id)
        finally:
            run_lock.release()
        raise HTTPException(500, f"Unable to start mining run: {exc}") from exc

    return {
        "run_id": run_id,
        "kb_id": kb_id,
        "status": "queued",
        "started_at": started_at,
        "execution_engine": "workflow",
        "workflow_id": binding.workflow_id,
        "workflow_version": binding.workflow_version,
        "workflow_graph_hash": binding.graph_hash,
        "force_redo": force_redo,
        "auto_force_redo": auto_force_redo,
    }
