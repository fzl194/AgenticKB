"""Domain-scoped, read-only knowledge asset routes.

代码瘦身批次4：旧全局读取面（documents/batches/segments/units 等 7 端点）
已随 KB 化收口退役——它们没有 KB membership/owner 授权，与 kb/routes 的
隔离模型冲突，且 12 天访问日志零真实调用。本 Router 仅保留系统状态页
在用的 /stats。
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from knowledge_mining.mining.api.deps import get_domain_async_pool
from knowledge_mining.mining.api.domain_scope import require_domain
from knowledge_mining.mining.infra.domain_pack import resolve_domain


router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


# Every knowledge query starts from the one active release for a domain/channel.
# DISTINCT makes the boundary robust even if corrupt historical data contains a
# duplicate release/selection row; downstream resources are never duplicated.
_ACTIVE_SCOPE_CTE = """
WITH active_scope AS (
    SELECT DISTINCT
        r.id AS release_id,
        r.build_id,
        r.domain,
        r.channel,
        bs.document_id,
        bs.document_snapshot_id,
        bs.source_batch_id
    FROM asset_publish_releases r
    JOIN asset_build_document_snapshots bs
      ON bs.build_id = r.build_id
     AND bs.selection_status = 'active'
    JOIN asset_documents d
      ON d.id = bs.document_id
     AND d.domain = %s
    JOIN asset_document_snapshots s
      ON s.id = bs.document_snapshot_id
     AND s.domain = %s
    WHERE r.domain = %s
      AND r.channel = %s
      AND r.status = 'active'
)
"""


def _active_scope_params(domain: str, channel: str) -> list[str]:
    return [domain, domain, domain, channel]


def _resolve_channel(domain: str, requested: str | None) -> str:
    if requested is not None and requested.strip():
        return requested.strip()
    entry = resolve_domain(domain)
    return str(entry.get("default_channel") or "prod").strip() or "prod"


@router.get("/stats")
async def knowledge_stats(
    request: Request,
    domain: str = Query(...),
    channel: str | None = Query(None),
) -> dict:
    """Return statistics for assets in the current active release only."""
    domain = require_domain(domain)
    channel = _resolve_channel(domain, channel)
    pool = await get_domain_async_pool(request, domain)

    async with pool.connection() as conn:
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + """
SELECT
    (SELECT COUNT(DISTINCT scope.document_id) FROM active_scope scope) AS documents,
    (SELECT COUNT(DISTINCT scope.document_snapshot_id) FROM active_scope scope) AS snapshots,
    (SELECT COUNT(DISTINCT seg.id)
       FROM asset_raw_segments seg
       JOIN active_scope scope
         ON scope.document_snapshot_id = seg.document_snapshot_id) AS segments,
    (SELECT COUNT(DISTINCT rel.id)
       FROM asset_raw_segment_relations rel
       JOIN active_scope scope
         ON scope.document_snapshot_id = rel.document_snapshot_id) AS relations,
    (SELECT COUNT(DISTINCT u.representation_id)
       FROM asset_retrieval_units_v2 u
       JOIN active_scope scope
         ON scope.document_snapshot_id = u.snapshot_id) AS retrieval_units,
    (SELECT COUNT(DISTINCT e.embedding_id)
       FROM asset_retrieval_embeddings_v2 e
       JOIN active_scope scope
         ON scope.document_snapshot_id = e.snapshot_id) AS embeddings
""",
            _active_scope_params(domain, channel),
        )
        counts = dict(await cur.fetchone())

        # 2026-09-01 v2 口径：representation_type
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT u.representation_type AS unit_type, COUNT(DISTINCT u.representation_id) AS c "
            "FROM asset_retrieval_units_v2 u "
            "JOIN active_scope scope "
            "  ON scope.document_snapshot_id = u.snapshot_id "
            "GROUP BY u.representation_type",
            _active_scope_params(domain, channel),
        )
        type_dist = {row["unit_type"]: row["c"] for row in await cur.fetchall()}

        # Release/build lifecycle exists independently of selections. In
        # particular, withdrawing the final document publishes an empty active
        # build which must remain distinguishable from "no active release".
        cur = await conn.execute(
            "SELECT DISTINCT r.id, r.build_id, r.domain, r.channel "
            "FROM asset_publish_releases r "
            "JOIN asset_builds b ON b.id = r.build_id AND b.domain = r.domain "
            "WHERE r.domain = %s AND b.domain = %s AND r.channel = %s "
            "AND r.status = 'active' ORDER BY r.id",
            [domain, domain, channel],
        )
        release_rows = [dict(row) for row in await cur.fetchall()]
        counts["builds"] = len({row["build_id"] for row in release_rows})
        counts["releases"] = len(release_rows)
        active_releases = [
            {key: row[key] for key in ("id", "domain", "channel")}
            for row in release_rows
        ]

    return {
        **counts,
        "retrieval_units_by_type": type_dist,
        "active_releases": active_releases,
    }
