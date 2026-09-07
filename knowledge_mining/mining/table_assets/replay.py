"""A3 存量回填 CLI（39 号 §3.1）：为已 commit 快照补表格 cell 类型化事实.

补齐式（A1/A2 同款纪律）：

- 从 parse bucket 的 IR 重投影缺失列（不重新解析、不动切片/units）；
- 只 UPDATE ``value_type IS NULL`` 的行（幂等，二跑零变更）；
- ``asset_structured_assets.sheet_name`` 同批补齐（IR 容器树）；
- 不改 snapshot 指纹、不发 Build、不失效 ref。

用法（域库凭据来自 mining 环境配置，与 worker 同源）::

    python -m knowledge_mining.mining.table_assets.replay \
        --domain default [--kb KB_ID] [--snapshot SNAPSHOT_ID] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from typing import Any

from knowledge_mining.mining.table_assets.facts import (
    extract_table_facts,
    plan_cell_updates,
)

_MAX_FAILURES_PRINTED = 20


@dataclass
class ReplayStats:
    total_snapshots: int = 0
    updated_snapshots: int = 0
    updated_cells: int = 0
    updated_assets: int = 0
    skipped_no_ir: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


async def _iter_snapshots(
    pool: Any, *, domain: str, kb_id: str | None, snapshot_id: str | None
) -> list[dict[str, Any]]:
    """候选快照：final cells 存在类型化缺口的 committed 快照."""
    query = """
        SELECT DISTINCT s.id, s.parse_ir_storage_object_id
        FROM asset_document_snapshots s
        JOIN asset_table_cells c ON c.snapshot_id = s.id
        WHERE s.lifecycle_status = 'READY'
          AND c.value_type IS NULL
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
    domain: str = "",
    kb_id: str | None = None,
    snapshot_id: str | None = None,
    dry_run: bool = False,
) -> ReplayStats:
    from knowledge_mining.mining.snapshot_store.ir_access import (
        SnapshotIRUnavailable,
        load_parsed_document,
    )

    stats = ReplayStats()
    candidates = await _iter_snapshots(
        pool, domain=domain, kb_id=kb_id, snapshot_id=snapshot_id
    )
    stats.total_snapshots = len(candidates)
    for row in candidates:
        target = row["id"]
        try:
            try:
                doc = await load_parsed_document(
                    snapshots=snapshots,
                    storage_objects=storage_objects,
                    object_store=object_store,
                    snapshot_id=target,
                )
            except SnapshotIRUnavailable:
                stats.skipped_no_ir += 1
                continue
            facts = extract_table_facts(doc)

            async with pool.connection() as conn:
                cursor = await conn.execute(
                    "SELECT table_ref, row_index AS row, column_index, "
                    "value_type, normalized_value, formula, row_span, "
                    "column_span, source_span_id "
                    "FROM asset_table_cells "
                    "WHERE snapshot_id = %s AND value_type IS NULL",
                    [target],
                )
                cell_rows = [dict(r) for r in await cursor.fetchall()]
                cursor = await conn.execute(
                    "SELECT table_ref FROM asset_structured_assets "
                    "WHERE snapshot_id = %s AND sheet_name IS NULL",
                    [target],
                )
                asset_rows = [dict(r) for r in await cursor.fetchall()]

            updates = plan_cell_updates(cell_rows, facts)
            sheet_updates = [
                (ref, sheet) for ref, sheet in facts.sheets.items()
                if any(a["table_ref"] == ref for a in asset_rows)
            ]
            if not updates and not sheet_updates:
                continue
            if dry_run:
                stats.updated_snapshots += 1
                stats.updated_cells += len(updates)
                stats.updated_assets += len(sheet_updates)
                continue
            async with pool.connection() as conn:
                async with conn.transaction():
                    for u in updates:
                        await conn.execute(
                            "UPDATE asset_table_cells SET "
                            "value_type=%s, normalized_value=%s, formula=%s, "
                            "row_span=%s, column_span=%s, source_span_id=%s "
                            "WHERE snapshot_id=%s AND table_ref=%s "
                            "AND row_index=%s AND column_index=%s "
                            "AND value_type IS NULL",
                            [
                                u["value_type"], u["normalized_value"],
                                u["formula"], u["row_span"], u["column_span"],
                                u["source_span_id"], target, u["table_ref"],
                                u["row"], u["column_index"],
                            ],
                        )
                    for ref, sheet in sheet_updates:
                        await conn.execute(
                            "UPDATE asset_structured_assets SET sheet_name=%s "
                            "WHERE snapshot_id=%s AND table_ref=%s "
                            "AND sheet_name IS NULL",
                            [sheet, target, ref],
                        )
            stats.updated_snapshots += 1
            stats.updated_cells += len(updates)
            stats.updated_assets += len(sheet_updates)
        except Exception as exc:  # noqa: BLE001 — 单快照失败不阻断批量
            stats.failed.append((target, f"{type(exc).__name__}: {exc}"))
    return stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_mining.mining.table_assets.replay",
        description="A3 存量回填：补表格 cell 类型化事实 + sheet 名（幂等）",
    )
    parser.add_argument("--domain", default="", help="限定域（空=全部）")
    parser.add_argument("--kb", default=None, help="限定知识库 id（active build 视角）")
    parser.add_argument("--snapshot", default=None, help="单快照回填")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    return parser


async def _main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
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
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    cfg = MiningDbConfig()
    async with AsyncConnectionPool(
        cfg.conninfo, min_size=1, max_size=4, open=True,
        kwargs={"row_factory": dict_row},
    ) as pool:
        stats = await replay(
            pool=pool,
            snapshots=PgSnapshotRepository(pool),
            storage_objects=PgStorageObjectRepository(pool),
            object_store=make_object_store(ObjectStoreConfig.from_control_plane()),
            domain=args.domain,
            kb_id=args.kb,
            snapshot_id=args.snapshot,
            dry_run=args.dry_run,
        )
    print(
        f"snapshots={stats.total_snapshots} updated={stats.updated_snapshots} "
        f"cells={stats.updated_cells} assets={stats.updated_assets} "
        f"skipped_no_ir={stats.skipped_no_ir} failed={len(stats.failed)}"
        f"{' (dry-run)' if args.dry_run else ''}"
    )
    for target, reason in stats.failed[:_MAX_FAILURES_PRINTED]:
        print(f"  FAILED {target}: {reason}", file=sys.stderr)
    if stats.failed:
        print(f"  … {len(stats.failed)} failures total (first shown)", file=sys.stderr)
    return 1 if stats.failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
