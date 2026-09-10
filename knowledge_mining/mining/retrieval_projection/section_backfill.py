"""A2 存量回填 CLI（39 号 §2.1）：为已 commit 快照补 units.section_ref.

补齐式（A1 同款纪律）：

- 不重新解析/切片/投影——读 final ``asset_raw_segments`` 的标题链
  （``section_path``，TEXT 存 ``[{level, title}]`` JSON——生产 DDL 列名），
  按快照当时的节点 ref 口径（**标题路径**，与旧快照已落库的
  section 节点/section 单元 target_ref 逐字一致）回填；
- 不改 snapshot 指纹、不发 Build、不失效 ref（新增可空列的 UPDATE）；
- 幂等：目标查询只取 ``section_ref IS NULL`` 的非 document 单元，
  二跑零变更（document 单元按设计恒 NULL，不计缺口——否则永不收敛）；
- 统计以事务内实际 UPDATE 语句数（每语句一行）为准，不按计划数虚报；
- 新快照（015 之后挖掘）在投影期即写 section_ref（序号路径口径），
  本 CLI 对其天然跳过（列已非 NULL）。

用法（域库凭据来自 mining 环境配置，与 worker 同源）::

    python -m knowledge_mining.mining.retrieval_projection.section_backfill \
        --domain default [--kb KB_ID] [--snapshot SNAPSHOT_ID] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

_MAX_FAILURES_PRINTED = 20

#: 按设计 section_ref 恒 NULL 的单元类型（document=文档级；alias 在
#: 无源可继时也允许 NULL——继承失败不伪造归属）
_PLAN_EXCLUDED_TYPES = {"document"}


@dataclass
class BackfillStats:
    total_snapshots: int = 0
    updated_snapshots: int = 0
    updated_units: int = 0
    already_done: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def _parse_chain(raw: Any) -> tuple[tuple[int, str], ...]:
    """``section_path`` 文本 → ((level, title), ...)。

    两种历史形态都接受：``[{"level": l, "title": t}]``（pipeline 写入）
    与 ``[[l, t]]``（早期口径）。非法形态按空链处理（宁缺勿伪造）。
    """
    if not raw:
        return ()
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    chain: list[tuple[int, str]] = []
    for entry in parsed:
        if isinstance(entry, dict):
            level, title = entry.get("level"), entry.get("title")
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            level, title = entry
        else:
            return ()  # 形状不明——整体放弃（不猜测部分链）
        if not isinstance(level, int) or not isinstance(title, str):
            return ()
        chain.append((level, title))
    return tuple(chain)


def plan_section_refs(
    *,
    segments: Sequence[Mapping[str, Any]],
    units: Sequence[Mapping[str, Any]],
    document_ref: str,
) -> dict[str, str]:
    """纯函数：unit 行 → section_ref 赋值计划（标题路径口径）.

    - segment 型单元（含 prose/table/table_row/list/code/formula/
      figure_caption）：按其 ordinal(=segment_index) 的标题链取 section ref；
    - section 单元：自身 target_ref 即 section ref；
    - document 单元：不赋值（设计上恒 NULL）；
    - alias 单元：按 provenance.source_representation 继承源单元赋值
      （源不在计划中则不伪造——保持 NULL）。
    """
    chain_by_index: dict[int, tuple[tuple[int, str], ...]] = {}
    for seg in segments:
        chain_by_index[int(seg["segment_index"])] = _parse_chain(
            seg.get("section_path")
        )

    def _title_path_ref(
        chain: tuple[tuple[int, str], ...],
    ) -> str | None:
        if not chain:
            return None
        return (
            f"{document_ref}#section:"
            + "/".join(title for _level, title in chain)
        )

    assigned: dict[str, str] = {}
    pending_aliases: list[Mapping[str, Any]] = []
    for unit in units:
        rep_id = str(unit["representation_id"])
        rep_type = str(unit.get("representation_type") or "")
        if rep_type in _PLAN_EXCLUDED_TYPES:
            continue
        if rep_type in ("query_alias", "summary_alias"):
            pending_aliases.append(unit)
            continue
        if rep_type == "section":
            ref = str(unit.get("target_ref") or "")
            if ref:
                assigned[rep_id] = ref
            continue
        ordinal = unit.get("ordinal")
        ordinal = int(ordinal) if ordinal is not None else -1
        chain = chain_by_index.get(ordinal, ())
        ref = _title_path_ref(chain)
        if ref:
            assigned[rep_id] = ref

    for alias in pending_aliases:
        rep_id = str(alias["representation_id"])
        provenance = alias.get("provenance_json")
        source = ""
        if isinstance(provenance, str):
            try:
                source = str(json.loads(provenance).get("source_representation") or "")
            except (TypeError, ValueError):
                source = ""
        elif isinstance(provenance, Mapping):
            source = str(provenance.get("source_representation") or "")
        inherited = assigned.get(source) if source else None
        if inherited:
            assigned[rep_id] = inherited

    return assigned


async def _iter_snapshots(
    pool: Any, *, domain: str, kb_id: str | None, snapshot_id: str | None
) -> list[dict[str, Any]]:
    """候选快照：final units 存在 section_ref 缺口的 committed 快照.

    缺口口径排除 document 单元（设计上恒 NULL）与 alias（源缺继允许
    NULL）——只看 segment/section 型单元，保证二跑收敛。
    """
    query = """
        SELECT DISTINCT s.id
        FROM asset_document_snapshots s
        JOIN asset_retrieval_units_v2 u ON u.snapshot_id = s.id
        WHERE s.lifecycle_status = 'READY'
          AND u.section_ref IS NULL
          AND u.representation_type IN ('segment', 'prose', 'table',
              'table_row', 'list', 'code', 'formula', 'figure_caption',
              'section')
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


async def _document_ref_of(pool: Any, snapshot_id: str) -> str | None:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT target_ref FROM asset_retrieval_units_v2 "
            "WHERE snapshot_id = %s AND representation_type = 'document' LIMIT 1",
            [snapshot_id],
        )
        row = await cursor.fetchone()
    if not row:
        return None
    # document target_ref 形如 "{doc}#document"
    ref = str(row["target_ref"])
    return ref[: -len("#document")] if ref.endswith("#document") else ref


async def backfill(
    *, pool: Any, domain: str = "", kb_id: str | None = None,
    snapshot_id: str | None = None, dry_run: bool = False,
) -> BackfillStats:
    stats = BackfillStats()
    candidates = await _iter_snapshots(
        pool, domain=domain, kb_id=kb_id, snapshot_id=snapshot_id
    )
    stats.total_snapshots = len(candidates)
    for row in candidates:
        target = row["id"]
        try:
            document_ref = await _document_ref_of(pool, target)
            if not document_ref:
                stats.failed.append((target, "no document representation"))
                continue
            async with pool.connection() as conn:
                cursor = await conn.execute(
                    "SELECT segment_index, section_path "
                    "FROM asset_raw_segments WHERE document_snapshot_id = %s "
                    "ORDER BY segment_index",
                    [target],
                )
                segments = [dict(r) for r in await cursor.fetchall()]
                cursor = await conn.execute(
                    "SELECT representation_id, representation_type, "
                    "target_ref, ordinal, provenance_json "
                    "FROM asset_retrieval_units_v2 WHERE snapshot_id = %s "
                    "AND section_ref IS NULL "
                    "AND representation_type NOT IN ('document')",
                    [target],
                )
                units = [dict(r) for r in await cursor.fetchall()]
            if not units:
                stats.already_done += 1
                continue
            plan = plan_section_refs(
                segments=segments, units=units, document_ref=document_ref
            )
            if not plan:
                stats.already_done += 1
                continue
            if dry_run:
                stats.updated_snapshots += 1
                stats.updated_units += len(plan)
                continue
            written = 0
            async with pool.connection() as conn:
                async with conn.transaction():
                    for rep_id, ref in plan.items():
                        await conn.execute(
                            "UPDATE asset_retrieval_units_v2 "
                            "SET section_ref = %s "
                            "WHERE snapshot_id = %s AND representation_id = %s "
                            "AND section_ref IS NULL",
                            [ref, target, rep_id],
                        )
                        written += 1
            # 统计=事务内实际 UPDATE 语句数（每语句至多一行——rep_id 唯一）
            stats.updated_snapshots += 1
            stats.updated_units += written
        except Exception as exc:  # noqa: BLE001 — 单快照失败不阻断批量
            stats.failed.append((target, f"{type(exc).__name__}: {exc}"))
    return stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_mining.mining.retrieval_projection.section_backfill",
        description="A2 存量回填：为已 commit 快照补 units.section_ref（幂等）",
    )
    parser.add_argument("--domain", default="", help="限定域（空=全部）")
    parser.add_argument("--kb", default=None, help="限定知识库 id（active build 视角）")
    parser.add_argument("--snapshot", default=None, help="单快照回填")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只计算赋值计划并统计，不写库",
    )
    return parser


async def _main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    from knowledge_mining.mining.infra.control_plane import fetch_database_config

    try:
        fetch_database_config(force=True)
    except Exception as exc:  # noqa: BLE001 — 控制面不可达时回落缓存配置
        print(f"control plane unreachable ({exc}); using cached config", file=sys.stderr)

    from knowledge_mining.mining.infra.pg_config import MiningDbConfig
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    cfg = MiningDbConfig()
    async with AsyncConnectionPool(
        cfg.conninfo, min_size=1, max_size=4, open=True,
        kwargs={"row_factory": dict_row},
    ) as pool:
        stats = await backfill(
            pool=pool, domain=args.domain, kb_id=args.kb,
            snapshot_id=args.snapshot, dry_run=args.dry_run,
        )
    print(
        f"snapshots={stats.total_snapshots} updated={stats.updated_snapshots} "
        f"units={stats.updated_units} already_done={stats.already_done} "
        f"failed={len(stats.failed)}{' (dry-run)' if args.dry_run else ''}"
    )
    for target, reason in stats.failed[:_MAX_FAILURES_PRINTED]:
        print(f"  FAILED {target}: {reason}", file=sys.stderr)
    if stats.failed:
        print(f"  … {len(stats.failed)} failures total (first shown)", file=sys.stderr)
    return 1 if stats.failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
