"""MCP 上传后的自动挖掘入队（排队语义）。

与手动 ``POST /api/kb/{kb_id}/mine`` 的差别只有一条：库里已有未结束 Run 时，
手动路径 409 拒绝，本模块改为**排队/合并**——

- 已有 queued 整库 Run → 直接复用（整库 Run 在 worker 认领时才枚举文档，
  排队期间新上传的文件会被自然捞走，重复入队只是空转）；
- 库处于活跃状态（running / awaiting_review / interrupted）→ 新插一条 queued
  Run 排在后面（010 号迁移后 queued 不占唯一性槽位），由域级 FIFO 串行执行。

契约：**任何失败只降级为 ``auto_mined=False`` + 机器可读 reason，绝不向上抛**——
上传本身必须成功，自动触发是锦上添花。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from psycopg.errors import UniqueViolation

from knowledge_mining.mining.infra.domain_pack import resolve_domain
from knowledge_mining.mining.infra.upload_config import UploadConfig
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.workflow.repositories.domain_run_repository import (
    AsyncDomainRunRepository,
)
from knowledge_mining.mining.workflow.service import WorkflowArchived, WorkflowNotFound

logger = logging.getLogger(__name__)

#: 失败 reason → Agent 可读短语（上传响应的 message 用）。
REASON_MESSAGES = {
    "kb_no_paradigm": "该知识库未绑定挖掘范式",
    "kb_empty": "该知识库没有可挖掘的文件",
    "paradigm_not_found": "挖掘范式不存在",
    "paradigm_archived": "挖掘范式已归档",
    "paradigm_unavailable": "挖掘范式暂不可用",
    "enqueue_conflict": "入队冲突，请稍后重试",
    "internal": "内部错误",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


async def enqueue_auto_mining(
    *,
    app_state: Any,
    kbdb: KbDB,
    kb: dict[str, Any],
    user_id: str,
    username: str,
) -> dict[str, Any]:
    """上传成功后自动入队一条整库增量挖掘 Run（排队语义）。

    返回 ``{"auto_mined": True, "run_id", "merged", "detail"}`` 或
    ``{"auto_mined": False, "reason"}``；不抛异常。
    """
    kb_id = str(kb["id"])
    try:
        return await _enqueue(app_state, kbdb, kb, kb_id, user_id, username)
    except Exception:  # noqa: BLE001 - 上传不能因自动触发失败而失败
        logger.exception("[auto-mine] enqueue failed for kb=%s", kb_id)
        return {"auto_mined": False, "reason": "internal"}


async def _enqueue(
    app_state: Any,
    kbdb: KbDB,
    kb: dict[str, Any],
    kb_id: str,
    user_id: str,
    username: str,
) -> dict[str, Any]:
    mining_workflow_id = kb.get("mining_workflow_id")
    if not mining_workflow_id:
        return {"auto_mined": False, "reason": "kb_no_paradigm"}

    documents = await kbdb.list_documents_in_kb(kb_id=kb_id)
    if not documents:
        return {"auto_mined": False, "reason": "kb_empty"}

    domain = kb["domain"]
    channel = str((resolve_domain(domain).get("default_channel") or "prod")).strip() or "prod"
    pool = await app_state.domain_pools.async_pool(domain)
    run_repository = AsyncDomainRunRepository(pool)

    # 合并：已有 queued 整库 Run 就不再插（认领时枚举文档，新文件自然被捞走）。
    # kick 仍要发：排队中的 Run 可能是服务重启前的遗留，dispatcher 线程未必在排水。
    existing = await run_repository.find_queued_whole_kb_run(kb_id)
    if existing is not None:
        app_state.domain_run_dispatcher.kick(domain)
        return {
            "auto_mined": True,
            "run_id": existing["id"],
            "merged": True,
            "detail": "已并入该库排队中的挖掘任务",
        }

    try:
        binding = await app_state.workflow_run_binder.resolve(
            workflow_id=mining_workflow_id,
            workflow_version=None,  # None → 取该范式 current_version
            domain=domain,
            channel=channel,
            upload_batch_id=None,
            run_overrides={},
        )
    except WorkflowNotFound:
        return {"auto_mined": False, "reason": "paradigm_not_found"}
    except WorkflowArchived:
        return {"auto_mined": False, "reason": "paradigm_archived"}
    except Exception:
        logger.exception(
            "[auto-mine] binding resolve failed for kb=%s workflow=%s",
            kb_id, mining_workflow_id,
        )
        return {"auto_mined": False, "reason": "paradigm_unavailable"}

    signature = f"{binding.workflow_id}:{binding.workflow_version}:{binding.graph_hash}"
    run_id = uuid.uuid4().hex
    input_path = str((UploadConfig().upload_root_path / kb_id).resolve())
    meta_patch: dict[str, Any] = {
        "kb_id": kb_id,
        "publish": False,
        "force_redo": False,
        "signature": signature,
        "triggered_by": "mcp_upload",
        "submitted_by_user_id": user_id,
        "submitted_by_username": username,
    }
    try:
        await run_repository.insert_queued_run(
            run_id=run_id,
            input_path=input_path,
            domain=domain,
            channel=channel,
            execution_engine="workflow",
            binding=binding,
            started_at=_utcnow(),
            preflight_manifest=None,
            kb_id=kb_id,
            metadata_json=meta_patch,
        )
    except UniqueViolation:
        # 010 号后 queued 插入不再撞唯一索引；这里兜底的是"数据库尚未应用
        # 010 迁移"的部署窗口（旧索引仍约束 queued）：撞上时优先合并到既有
        # 整库 Run，合并不了则显式可重试，绝不影响上传结果。
        concurrent = await run_repository.find_queued_whole_kb_run(kb_id)
        if concurrent is not None:
            app_state.domain_run_dispatcher.kick(domain)
            return {
                "auto_mined": True,
                "run_id": concurrent["id"],
                "merged": True,
                "detail": "已并入该库排队中的挖掘任务",
            }
        return {"auto_mined": False, "reason": "enqueue_conflict"}

    app_state.domain_run_dispatcher.kick(domain)
    return {
        "auto_mined": True,
        "run_id": run_id,
        "merged": False,
        "detail": "已入队整库增量挖掘",
    }
