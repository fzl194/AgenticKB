"""KB mining trigger — POST /api/kb/{kb_id}/mine.

KB 中心化挖掘（融合设计 §5.2）：KB 按自选挖掘范式（knowledge_bases.mining_workflow_id）
走 **workflow 引擎**（不再走 legacy），范式绑定四元组从 workflow_run_binder 解析；
run 行写 kb_id + metadata.publish=false（只 build 不 publish 到域级 active release，
避免 B1 同域多库互相 retire）；产物经 existing_doc→asset_documents.kb_id 自然归属 KB。

整库请求读取 KB 当前文档；具体复用/重算由冻结 Workflow 的文档链决定，本路由
不宣称未变文档会被零成本跳过。身份以 KB 内 document identity 为准。

Run 先可靠落为 queued，再由域级 FIFO 调度器串行认领；同一 KB 已有未结束
Run 时友好拒绝，不创建覆盖任务。

选择性挖掘：请求体可选 document_ids（asset_documents.id 列表），非空时只挖这些文档；
省略/空 → 整库增量。所选 id 经 metadata_json.document_ids 透传，_prepare_document_states
读取后按 storage_path 过滤 ingest_directory 的扫描结果。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel
from psycopg.errors import UniqueViolation

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


def _busy_error(run: dict[str, Any]) -> HTTPException:
    status = str(run.get("status") or "queued")
    messages = {
        "queued": "该知识库已在排队中",
        "running": "该知识库正在挖掘，请完成后再试",
        "awaiting_review": "该知识库存在待处理的挖掘任务",
        "interrupted": "该知识库存在待恢复的挖掘任务",
    }
    return HTTPException(
        409,
        detail={
            "code": "kb_mining_busy",
            "message": messages.get(status, "该知识库已有未结束的挖掘任务"),
            "details": {"run_id": run.get("id"), "status": status},
        },
    )


@router.post("", status_code=202)
async def mine_kb(
    kb_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    kbdb: KbDB = Depends(get_kb_db),
    body: MineKbBody | None = Body(None),
) -> dict[str, Any]:
    """Trigger workflow mining for a KB's uploaded documents.

    整库模式（默认）：提交本库全部当前文档，复用与重算由冻结 Workflow 决定。
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
    # v2 documents are object-store-only and deliberately have no local
    # upload directory.  ``input_path`` remains a legacy run-record field;
    # the worker resolves KB documents from their storage-object pointers.
    documents = await kbdb.list_documents_in_kb(kb_id=kb_id)
    if not documents:
        raise HTTPException(400, "KB has no uploaded files to mine")

    domain_entry = resolve_domain(domain)
    channel = str(domain_entry.get("default_channel") or "prod").strip() or "prod"
    pool = await request.app.state.domain_pools.async_pool(domain)
    run_repository = AsyncDomainRunRepository(pool)
    open_run = await run_repository.find_open_run_for_kb(kb_id)
    if open_run is not None:
        raise _busy_error(open_run)

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

    # 36号：范式签名进入逐文档增量判定，不再据此把整个 KB 自动
    # force_redo。显式 force_redo 仅由用户请求控制。
    signature = f"{binding.workflow_id}:{binding.workflow_version}:{binding.graph_hash}"
    auto_force_redo = False
    force_redo = bool(body and body.force_redo)

    run_id = uuid.uuid4().hex
    started_at = _utcnow()
    document_ids = body.document_ids if body and body.document_ids else None
    meta_patch: dict[str, Any] = {
        "kb_id": kb_id,
        "publish": False,
        "force_redo": force_redo,
        "signature": signature,
        "submitted_by_user_id": user.get("id"),
        "submitted_by_username": user.get("username"),
    }
    if document_ids:
        meta_patch["document_ids"] = document_ids
    try:
        await run_repository.insert_queued_run(
            run_id=run_id,
            input_path=input_path,
            domain=domain,
            channel=channel,
            execution_engine="workflow",
            binding=binding,
            started_at=started_at,
            preflight_manifest=None,
            kb_id=kb_id,
            metadata_json=meta_patch,
        )
    except UniqueViolation as exc:
        concurrent = await run_repository.find_open_run_for_kb(kb_id)
        if concurrent is not None:
            raise _busy_error(concurrent) from exc
        raise

    request.app.state.domain_run_dispatcher.kick(domain)

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
