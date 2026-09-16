"""Batched retention cleanup for agent_llm_* audit tables.

Admin-triggered only (POST /api/v1/admin/cleanup). Deletes terminal tasks
older than the retention window in small batches so long transactions never
block the Worker's claim/heartbeat path.

Invariants:
  - Read + delete only; write paths are never touched here.
  - Only terminal task rows are eligible: status whitelist and finished_at
    cutoff are hardcoded SQL literals (never client-supplied parameters).
  - queued/running tasks (finished_at IS NULL fails the `<` comparison),
    agent_llm_prompt_templates, and kb_* business tables are never touched.
  - Each batch is one short transaction (db.run commits per connection-exit);
    the loops MUST stay outside db.run or batching degrades to one giant txn.

Timing: the control-plane reverse proxy enforces read=300s, so real deletes
run under a 240s budget and return truncated=true (+ remaining counts) when
the budget runs out — the admin re-invokes to continue (idempotent).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from llm_service.db import LlmRuntimeDB

logger = logging.getLogger(__name__)

BATCH_TASKS = 1000        # parent tasks per batch (~6-8 child rows each)
BATCH_MODEL_CALLS = 5000  # model_calls rows are lightweight, batch bigger
BATCH_SLEEP_S = 0.1       # yield between batches: autovacuum / WAL / worker
TIME_BUDGET_S = 240.0     # proxy read timeout is 300s; keep 60s for recount
MAX_BATCHES = 500         # hard cap: 500 batches ≈ 500k tasks, loop backstop

# Terminal statuses only. 'failed' is included deliberately: the sync path
# (PersistWriter) inserts failed chat/model tasks directly as final state —
# they are never retried and would otherwise accumulate forever.
# Alias (e.g. "t.") is required in JOIN counts: several child tables also
# have a status column, so a bare reference is ambiguous.
def _task_predicate(alias: str = "", *, domain: bool = False) -> str:
    status_col = f"{alias}status" if alias else "status"
    finished_col = f"{alias}finished_at" if alias else "finished_at"
    domain_col = f"{alias}knowledge_domain" if alias else "knowledge_domain"
    sql = (
        f"{status_col} IN ('succeeded','failed','dead_letter','cancelled') "
        f"AND {finished_col} < %s"
    )
    if domain:
        sql += f" AND {domain_col} = %s"
    return sql

# Child delete order matters: results.attempt_id has ON DELETE SET NULL, so
# results must go before attempts (avoids an UPDATE write-amplification on
# every result row when attempts are deleted first).
_CHILD_ORDER = ("results", "events", "attempts", "requests")
_TABLES = ("tasks", "requests", "attempts", "results", "events", "model_calls")

_TABLE_LABELS = {
    "tasks": "任务主表",
    "requests": "请求快照",
    "attempts": "执行尝试",
    "results": "解析结果",
    "events": "事件流水",
    "model_calls": "模型调用审计",
}

_NOTES = [
    "PostgreSQL 删除后磁盘空间不即时返还操作系统，将由 autovacuum 在表内回收复用；如需把空间还给操作系统需 DBA 手工执行 VACUUM FULL（锁全表，请避开业务时段）。",
    "被删除的 succeeded 任务会释放其 idempotency_key：相同幂等键的新任务将被重新执行而非命中去重。",
    "仅删除已结束（succeeded/failed/dead_letter/cancelled）且超过保留期的任务；排队中/运行中任务、提示词模板与知识库业务表不受影响。",
]


async def run_cleanup(
    db: LlmRuntimeDB,
    *,
    retention_days: int,
    dry_run: bool,
    knowledge_domain: str | None = None,
) -> dict[str, Any]:
    """Estimate (dry_run) or batch-delete stale terminal agent_llm_* rows.

    knowledge_domain=None cleans all domains; otherwise only that domain's
    tasks and model_calls are eligible.
    """
    started = time.monotonic()
    row = await db.fetchone(
        "SELECT NOW() - make_interval(days => %s) AS cutoff", (retention_days,)
    )
    cutoff = row["cutoff"]
    scoped = knowledge_domain is not None
    pred_args = (cutoff, knowledge_domain) if scoped else (cutoff,)

    if dry_run:
        estimates = await _count_all(db, cutoff, knowledge_domain)
        table_sizes = await _table_sizes(db)
        return {
            "dry_run": True,
            "retention_days": retention_days,
            "knowledge_domain": knowledge_domain,
            "cutoff_at": cutoff.isoformat(),
            "truncated": False,
            "batches": 0,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "estimates": estimates,
            "table_sizes": table_sizes,
            "notes": list(_NOTES),
        }

    deadline = time.monotonic() + TIME_BUDGET_S
    deleted = {t: 0 for t in _TABLES}
    batches = 0

    # Loop A: tasks + their child rows (children first, explicit counting).
    while batches < MAX_BATCHES and time.monotonic() < deadline:
        id_rows = await db.fetchall(
            f"SELECT id FROM agent_llm_tasks "
            f"WHERE {_task_predicate(domain=scoped)} LIMIT %s",
            (*pred_args, BATCH_TASKS),
        )
        if not id_rows:
            break
        ids = [r["id"] for r in id_rows]

        async def _delete_batch(conn, ids=ids):
            return await _batch_delete_txn(conn, ids, pred_args, scoped)

        for table, count in (await db.run(_delete_batch)).items():
            deleted[table] += count
        batches += 1
        await asyncio.sleep(BATCH_SLEEP_S)

    # Loop B: model_calls — standalone table (no FK), same cutoff on created_at.
    mc_delete_sql = (
        "DELETE FROM agent_llm_model_calls WHERE id IN ("
        "SELECT id FROM agent_llm_model_calls WHERE created_at < %s"
        + (" AND knowledge_domain = %s" if scoped else "")
        + " LIMIT %s)"
    )
    while batches < MAX_BATCHES and time.monotonic() < deadline:
        mc_params = (*pred_args, BATCH_MODEL_CALLS) if scoped else (cutoff, BATCH_MODEL_CALLS)

        async def _delete_model_calls(conn, params=mc_params):
            cur = await conn.execute(mc_delete_sql, params)
            return cur.rowcount

        n = await db.run(_delete_model_calls)
        if n == 0:
            break
        deleted["model_calls"] += n
        batches += 1
        await asyncio.sleep(BATCH_SLEEP_S)

    truncated = batches >= MAX_BATCHES or time.monotonic() >= deadline
    remaining = await _count_all(db, cutoff, knowledge_domain) if truncated else None
    if truncated and remaining is not None and all(v == 0 for v in remaining.values()):
        truncated = False
        remaining = None

    result: dict[str, Any] = {
        "dry_run": False,
        "retention_days": retention_days,
        "knowledge_domain": knowledge_domain,
        "cutoff_at": cutoff.isoformat(),
        "truncated": truncated,
        "batches": batches,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "deleted": deleted,
    }
    if remaining is not None:
        result["remaining"] = remaining
    logger.info(
        "cleanup(retention_days=%s, domain=%s) deleted=%s batches=%s truncated=%s",
        retention_days, knowledge_domain or "ALL", deleted, batches, truncated,
    )
    return result


async def _batch_delete_txn(conn, ids: list[str], pred_args: tuple, scoped: bool) -> dict[str, int]:
    """Delete one batch of tasks + children inside the caller's transaction.

    Re-verifies and locks the candidates first (FOR UPDATE): a concurrent
    retry of a failed/dead_letter/cancelled task re-queues it (status flips
    to queued, finished_at cleared) — without this check the bare child
    deletes would strip a just-revived task's request snapshot while the
    predicate-guarded parent delete spares the task itself.
    """
    rows = await (
        await conn.execute(
            f"SELECT id FROM agent_llm_tasks WHERE id = ANY(%s) "
            f"AND {_task_predicate(domain=scoped)} FOR UPDATE",
            (ids, *pred_args),
        )
    ).fetchall()
    verified = [r["id"] for r in rows]
    if not verified:
        return {t: 0 for t in ("tasks", *_CHILD_ORDER)}

    counts: dict[str, int] = {}
    for child in _CHILD_ORDER:
        cur = await conn.execute(
            f"DELETE FROM agent_llm_{child} WHERE task_id = ANY(%s)", (verified,)
        )
        counts[child] = cur.rowcount
    # Parent delete repeats the full predicate (defense in depth: ids came
    # from the same predicate, but never delete on ids alone).
    cur = await conn.execute(
        f"DELETE FROM agent_llm_tasks WHERE id = ANY(%s) AND {_task_predicate(domain=scoped)}",
        (verified, *pred_args),
    )
    counts["tasks"] = cur.rowcount
    return counts


async def _count_all(db: LlmRuntimeDB, cutoff, knowledge_domain: str | None = None) -> dict[str, int]:
    """Count cleanup-eligible rows across the six tables (optionally domain-scoped)."""
    scoped = knowledge_domain is not None
    pred_args = (cutoff, knowledge_domain) if scoped else (cutoff,)
    counts = {
        "tasks": await _count(
            db,
            f"SELECT COUNT(*) FROM agent_llm_tasks WHERE {_task_predicate(domain=scoped)}",
            pred_args,
        ),
    }
    for child in ("requests", "attempts", "results", "events"):
        counts[child] = await _count(
            db,
            f"SELECT COUNT(*) FROM agent_llm_{child} c "
            f"JOIN agent_llm_tasks t ON t.id = c.task_id "
            f"WHERE {_task_predicate('t.', domain=scoped)}",
            pred_args,
        )
    counts["model_calls"] = await _count(
        db,
        "SELECT COUNT(*) FROM agent_llm_model_calls WHERE created_at < %s"
        + (" AND knowledge_domain = %s" if scoped else ""),
        pred_args,
    )
    return counts


async def _count(db: LlmRuntimeDB, sql: str, params: tuple) -> int:
    row = await db.fetchone(sql, params)
    return int(row["count"]) if row else 0


async def _table_sizes(db: LlmRuntimeDB) -> list[dict[str, Any]]:
    """Current disk usage baseline for the six tables (both dry-run and real)."""
    rows = await db.fetchall(
        "SELECT c.relname AS table_name, s.n_live_tup AS approx_rows, "
        "pg_total_relation_size(c.oid) AS total_bytes "
        "FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid "
        "WHERE n.nspname = 'public' AND c.relname IN ("
        "'agent_llm_tasks','agent_llm_requests','agent_llm_attempts',"
        "'agent_llm_results','agent_llm_events','agent_llm_model_calls')"
    )
    return [
        {
            "table_name": r["table_name"],
            "label": _TABLE_LABELS.get(r["table_name"].removeprefix("agent_llm_"), ""),
            "approx_rows": int(r["approx_rows"] or 0),
            "total_bytes": int(r["total_bytes"] or 0),
        }
        for r in rows
    ]
