"""A1 受控重放 CLI（37 号 FR-A1-3 / 38 号 §2.3 replay）.

对已 commit 快照回填来源记录——**补齐式**（拍板决策 1）：

- 不重新调用解析模型（IR 内容寻址已在 parse bucket）；
- 不重新切片/投影（读 final units）；
- 不改 snapshot 指纹、不发 Build、不失效任何已发放 ref；
- 逐快照"物化 → staging → ``promote_locators`` 专用最小晋升"，
  幂等可重跑（staging 先删后插 + final 晋升同事务）。

用法（域库凭据来自 mining 环境配置，与 worker 同源）::

    python -m knowledge_mining.mining.source_locator.replay \
        --domain default [--kb KB_ID] [--snapshot SNAPSHOT_ID] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from typing import Any

_MAX_FAILURES_PRINTED = 20


@dataclass
class ReplayStats:
    total_snapshots: int = 0
    materialized: int = 0
    records: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def note_skip(self, status: str) -> None:
        self.skipped[status] = self.skipped.get(status, 0) + 1


async def _iter_snapshots(
    pool: Any, *, domain: str, kb_id: str | None, snapshot_id: str | None
) -> list[dict[str, Any]]:
    """候选快照：final units 非空的 committed 快照（重放的诚实分母）."""
    query = """
        SELECT DISTINCT s.id, s.domain, s.parse_ir_storage_object_id
        FROM asset_document_snapshots s
        JOIN asset_retrieval_units_v2 u ON u.snapshot_id = s.id
        WHERE s.lifecycle_status = 'READY'
    """
    params: list[Any] = []
    if domain:
        query += " AND s.domain = %s"
        params.append(domain)
    if snapshot_id:
        query += " AND s.id = %s"
        params.append(snapshot_id)
    if kb_id:
        query += """
            AND EXISTS (
                SELECT 1 FROM asset_build_document_snapshots m
                JOIN asset_builds b ON b.id = m.build_id
                WHERE m.document_snapshot_id = s.id
                  AND b.kb_id = %s
                  AND m.selection_status = 'active'
            )
        """
        params.append(kb_id)
    async with pool.connection() as conn:
        cursor = await conn.execute(query + " ORDER BY s.id", params)
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def replay(
    *,
    pool: Any,
    snapshots: Any,
    storage_objects: Any,
    object_store: Any,
    locator_store: Any,
    domain: str = "",
    kb_id: str | None = None,
    snapshot_id: str | None = None,
    dry_run: bool = False,
    service: Any = None,
) -> ReplayStats:
    from knowledge_mining.mining.source_locator.service import SourceLocatorService

    service = service or SourceLocatorService(
        snapshots=snapshots,
        storage_objects=storage_objects,
        object_store=object_store,
        locator_store=locator_store,
    )
    candidates = await _iter_snapshots(
        pool, domain=domain, kb_id=kb_id, snapshot_id=snapshot_id
    )
    stats = ReplayStats(total_snapshots=len(candidates))
    for row in candidates:
        target = row["id"]
        try:
            representations = await locator_store.list_final_representations(target)
            outcome = await service.materialize(
                target, representations=representations
            )
        except Exception as exc:  # noqa: BLE001 — 单快照失败不阻断批量
            stats.failed.append((target, f"{type(exc).__name__}: {exc}"))
            continue
        if outcome.status != "ok":
            stats.note_skip(outcome.status)
            continue
        if dry_run:
            continue
        await locator_store.promote_locators([target])
        stats.materialized += 1
        stats.records += outcome.record_count
    return stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_mining.mining.source_locator.replay",
        description="A1 受控重放：为已 commit 快照回填来源记录（幂等）",
    )
    parser.add_argument("--domain", default="", help="限定域（空=全部）")
    parser.add_argument("--kb", default=None, help="限定知识库 id（active build 视角）")
    parser.add_argument("--snapshot", default=None, help="单快照重放")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只物化到 staging 并统计，不晋升 final",
    )
    return parser


async def _main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # 与 api/app.py 同源：先从控制面拉配置（失败则回落环境/本地配置缓存）。
    from knowledge_mining.mining.infra.control_plane import fetch_database_config

    try:
        fetch_database_config(force=True)
    except Exception as exc:  # noqa: BLE001 — 控制面不可达时回落缓存配置
        print(f"control plane unreachable ({exc}); using cached config", file=sys.stderr)

    from knowledge_mining.mining.infra.object_store.config import ObjectStoreConfig
    from knowledge_mining.mining.infra.object_store.factory import make_object_store
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig
    from knowledge_mining.mining.file_management.repositories_pg import (
        PgStorageObjectRepository,
    )
    from knowledge_mining.mining.snapshot_store.repositories_pg import (
        PgSnapshotRepository,
    )
    from knowledge_mining.mining.source_locator.repositories_pg import PgLocatorStore

    from psycopg_pool import AsyncConnectionPool

    cfg = MiningDbConfig()
    from psycopg.rows import dict_row

    stats: ReplayStats
    async with AsyncConnectionPool(
        cfg.conninfo, min_size=1, max_size=4, open=True,
        kwargs={"row_factory": dict_row},
    ) as pool:
        stats = await replay(
            pool=pool,
            snapshots=PgSnapshotRepository(pool),
            storage_objects=PgStorageObjectRepository(pool),
            object_store=make_object_store(ObjectStoreConfig.from_control_plane()),
            locator_store=PgLocatorStore(pool),
            domain=args.domain,
            kb_id=args.kb,
            snapshot_id=args.snapshot,
            dry_run=args.dry_run,
        )
    print(
        f"snapshots={stats.total_snapshots} materialized={stats.materialized} "
        f"records={stats.records} skipped={stats.skipped} "
        f"failed={len(stats.failed)}{' (dry-run)' if args.dry_run else ''}"
    )
    for target, reason in stats.failed[:_MAX_FAILURES_PRINTED]:
        print(f"  FAILED {target}: {reason}", file=sys.stderr)
    if stats.failed:
        print(f"  … {len(stats.failed)} failures total (first shown)", file=sys.stderr)
    return 1 if stats.failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
