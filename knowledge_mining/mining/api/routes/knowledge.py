"""KB-scoped, read-only knowledge asset routes.

代码瘦身批次4：旧全局读取面（documents/batches/segments/units 等 7 端点）
已随 KB 化收口退役——它们没有 KB membership/owner 授权，与 kb/routes 的
隔离模型冲突，且 12 天访问日志零真实调用。本 Router 仅保留系统状态页
在用的 /stats。
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from knowledge_mining.mining.api.deps import get_domain_async_pool
from knowledge_mining.mining.api.domain_scope import require_domain


router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


# Every knowledge query starts from the latest validated Build of one KB.
# Build is already the complete serving view, so a release/channel indirection
# is neither needed nor safe in a multi-KB domain.
_ACTIVE_SCOPE_CTE = """
WITH latest_build AS (
    SELECT b.id
    FROM asset_builds b
    WHERE b.kb_id = %s
      AND b.domain = %s
      AND b.status IN ('validated', 'published')
    ORDER BY b.created_at DESC, b.id DESC
    LIMIT 1
), active_scope AS (
    SELECT DISTINCT
        b.id AS build_id,
        b.domain,
        b.kb_id,
        bs.document_id,
        bs.document_snapshot_id,
        bs.source_batch_id
    FROM latest_build lb
    JOIN asset_builds b ON b.id = lb.id
    JOIN asset_build_document_snapshots bs
      ON bs.build_id = b.id
     AND bs.selection_status = 'active'
    JOIN asset_documents d
      ON d.id = bs.document_id
     AND d.domain = b.domain
     AND d.kb_id = b.kb_id
    JOIN asset_document_snapshots s
      ON s.id = bs.document_snapshot_id
     AND s.domain = b.domain
)
"""


def _active_scope_params(kb_id: str, domain: str) -> list[str]:
    return [kb_id, domain]


@router.get("/stats")
async def knowledge_stats(
    request: Request,
    domain: str = Query(...),
    kb_id: str = Query(..., min_length=1),
) -> dict:
    """Return statistics for the latest validated Build of one KB."""
    domain = require_domain(domain)
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
    (SELECT COUNT(*)
       FROM asset_structure_edges rel
       JOIN active_scope scope
         ON scope.document_snapshot_id = rel.snapshot_id) AS relations,
    (SELECT COUNT(DISTINCT u.representation_id)
       FROM asset_retrieval_units_v2 u
       JOIN active_scope scope
         ON scope.document_snapshot_id = u.snapshot_id) AS retrieval_units,
    (SELECT COUNT(DISTINCT e.embedding_id)
       FROM asset_retrieval_embeddings_v2 e
       JOIN active_scope scope
         ON scope.document_snapshot_id = e.snapshot_id) AS embeddings
""",
            _active_scope_params(kb_id, domain),
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
            _active_scope_params(kb_id, domain),
        )
        type_dist = {row["unit_type"]: row["c"] for row in await cur.fetchall()}
        counts["builds"] = 1 if counts["documents"] else 0

    return {
        **counts,
        "retrieval_units_by_type": type_dist,
    }
