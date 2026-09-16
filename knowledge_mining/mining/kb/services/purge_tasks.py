# -*- coding: utf-8 -*-
"""KB 硬删任务执行器（2026-09-16 删除体系二期：禁用态 + 后台删除 + 进度）.

DELETE /api/kb/{id} 确认后秒级置 status='deleting'（禁用：读写全退、检索
自动退出——Python is_visible 与 Java KnowledgeBaseMapper 均滤 'active'），
spawn 本执行器跑管线；进度实时落 ``kb_purge_tasks``，前端轮询渲染。

重启恢复：``recover_purge_tasks``（lifespan 调用）把 queued/running 的任务
重新入队——管线幂等，半途状态重跑干净。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from knowledge_mining.mining.kb.services.purge_service import PurgeError, PurgeService

logger = logging.getLogger(__name__)

#: 后台任务强引用（事件循环只留弱引用——同 archive_tasks/import_service 模式）.
_bg_tasks: set[asyncio.Task] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PurgeTaskRepo:
    """kb_purge_tasks 仓储（与 KbDB 同池）."""

    def __init__(self, pool: Any):
        self._pool = pool

    async def insert_task(self, *, kb_id: str, kb_name: str, domain: str,
                          requested_by: str) -> dict[str, Any]:
        row = {"id": uuid.uuid4().hex, "status": "queued", "phase": "queued",
               "progress_json": {}, "error": None,
               "created_at": _now(), "updated_at": _now()}
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO kb_purge_tasks
                     (id, kb_id, kb_name, domain, status, phase, progress_json,
                      requested_by, error, created_at, updated_at)
                   VALUES (%(id)s, %(kb_id)s, %(kb_name)s, %(domain)s,
                           %(status)s, %(phase)s, %(progress_json)s::jsonb,
                           %(requested_by)s, %(error)s,
                           %(created_at)s, %(updated_at)s)
                   RETURNING *""",
                {**row, "kb_id": kb_id, "kb_name": kb_name, "domain": domain,
                 "requested_by": requested_by,
                 "progress_json": json.dumps({})})
            return dict(await cur.fetchone())

    async def get_active_task(self, kb_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM kb_purge_tasks
                   WHERE kb_id = %s AND status IN ('queued', 'running')
                   ORDER BY created_at DESC LIMIT 1""",
                [kb_id])
            r = await cur.fetchone()
            return dict(r) if r else None

    async def list_tasks(self, *, domain: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM kb_purge_tasks WHERE domain = %s
                   ORDER BY created_at DESC LIMIT 50""", [domain])
            rows = [dict(r) for r in await cur.fetchall()]
        for r in rows:
            r["progress"] = _load_progress(r.pop("progress_json", None))
        return rows

    async def list_recoverable(self) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM kb_purge_tasks
                   WHERE status IN ('queued', 'running') LIMIT 100""")
            return [dict(r) for r in await cur.fetchall()]

    async def update_task(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        params = {**fields, "updated_at": _now(), "id": task_id}
        sets: list[str] = []
        for k, v in fields.items():
            if isinstance(v, (dict, list)):
                params[k] = json.dumps(v, ensure_ascii=False)
                sets.append(f"{k} = %({k})s::jsonb")
            else:
                sets.append(f"{k} = %({k})s")
        async with self._pool.connection() as conn:
            await conn.execute(
                f"UPDATE kb_purge_tasks SET {', '.join(sets)}, "
                "updated_at = %(updated_at)s WHERE id = %(id)s", params)


def _load_progress(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    return {}


async def start_purge_task(
    *, pool: Any, object_store: Any, kb_id: str, kb_name: str,
    domain: str, requested_by: str,
) -> dict[str, Any]:
    """创建任务并后台执行（调用方须已置 kb.status='deleting'）."""
    repo = PurgeTaskRepo(pool)

    async def _progress(phase: str, **counts: int) -> None:
        await repo.update_task(
            task_row["id"], status="running", phase=phase,
            progress_json=counts)

    async def _run() -> None:
        try:
            await repo.update_task(task_row["id"], status="running",
                                   phase="prepare")
            svc = PurgeService(pool, object_store, progress=_progress)
            summary = await svc.purge_kb(kb_id)
            await repo.update_task(
                task_row["id"], status="done", phase="finalize",
                progress_json={
                    "documents": len(summary.get("deleted_documents") or []),
                    "snapshots_reclaimed": summary["reclaimed_snapshots"],
                    "objects_reclaimed": summary["reclaimed_objects"]},
                error=None)
            logger.info("[purge-task] done: %s kb=%s", task_row["id"], kb_id)
        except Exception as e:  # noqa: BLE001 - 真实原因入 error 列
            logger.exception("[purge-task] failed: %s kb=%s", task_row["id"], kb_id)
            try:
                await repo.update_task(task_row["id"], status="failed",
                                       error=str(e)[:2000])
            except Exception:
                logger.exception("[purge-task] 失败态回写也失败: %s",
                                 task_row["id"])
        finally:
            # 终态时库行应已删（done）；failed 时保留 deleting 态供排障重删
            pass

    task_row = await repo.insert_task(
        kb_id=kb_id, kb_name=kb_name, domain=domain, requested_by=requested_by)
    t = asyncio.create_task(_run())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return task_row


async def recover_purge_tasks(pool: Any, object_store: Any) -> int:
    """startup 恢复：queued/running 的任务重新跑（管线幂等）."""
    repo = PurgeTaskRepo(pool)
    rows = await repo.list_recoverable()
    for row in rows:
        logger.info("[purge-task] recover: %s kb=%s (state=%s)",
                    row["id"], row["kb_id"], row["status"])

        async def _progress(phase: str, _row=row, **counts: int) -> None:
            await repo.update_task(_row["id"], status="running", phase=phase,
                                   progress_json=counts)

        async def _run(_row=row) -> None:
            try:
                await repo.update_task(_row["id"], status="running",
                                       phase="prepare")
                svc = PurgeService(pool, object_store, progress=_progress)
                summary = await svc.purge_kb(_row["kb_id"])
                await repo.update_task(
                    _row["id"], status="done", phase="finalize",
                    progress_json={
                        "documents": len(summary.get("deleted_documents") or []),
                        "snapshots_reclaimed": summary["reclaimed_snapshots"],
                        "objects_reclaimed": summary["reclaimed_objects"]},
                    error=None)
            except Exception as e:  # noqa: BLE001
                logger.exception("[purge-task] recovered run failed: %s",
                                 _row["id"])
                try:
                    await repo.update_task(_row["id"], status="failed",
                                           error=str(e)[:2000])
                except Exception:
                    pass

        t = asyncio.create_task(_run())
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
    return len(rows)


__all__ = [
    "PurgeTaskRepo", "PurgeError", "recover_purge_tasks", "start_purge_task",
]
