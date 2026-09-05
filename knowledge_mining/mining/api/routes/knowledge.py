"""Domain-scoped, read-only knowledge asset routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

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


async def _active_snapshot_id(
    conn: Any,
    *,
    document_id: str,
    domain: str,
    channel: str,
) -> str:
    cur = await conn.execute(
        _ACTIVE_SCOPE_CTE
        + "SELECT scope.document_snapshot_id "
        "FROM active_scope scope "
        "WHERE scope.document_id = %s "
        "ORDER BY scope.document_snapshot_id LIMIT 1",
        _active_scope_params(domain, channel) + [document_id],
    )
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, f"No active snapshot found for document {document_id}")
    return row["document_snapshot_id"]


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


@router.get("/documents")
async def list_documents(
    request: Request,
    domain: str = Query(...),
    channel: str | None = Query(None),
    type: str | None = None,
    source_batch_id: str | None = None,
    unclassified: bool = Query(False),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """List active documents, optionally filtered by type or source batch."""
    domain = require_domain(domain)
    channel = _resolve_channel(domain, channel)
    if source_batch_id is not None and unclassified:
        raise HTTPException(
            422, "source_batch_id and unclassified=true are mutually exclusive"
        )
    pool = await get_domain_async_pool(request, domain)

    conditions: list[str] = []
    filter_params: list[Any] = []
    if type:
        conditions.append("d.document_type = %s")
        filter_params.append(type)
    if source_batch_id is not None:
        conditions.append("scope.source_batch_id = %s")
        filter_params.append(source_batch_id)
    elif unclassified:
        conditions.append("scope.source_batch_id IS NULL")
    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    async with pool.connection() as conn:
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT COUNT(DISTINCT d.id) AS c "
            "FROM active_scope scope "
            "JOIN asset_documents d ON d.id = scope.document_id"
            + where,
            _active_scope_params(domain, channel) + filter_params,
        )
        total = (await cur.fetchone())["c"]

        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT DISTINCT d.id, d.document_key, d.document_name, "
            "d.document_type, d.created_at, scope.source_batch_id, b.batch_code "
            "FROM active_scope scope "
            "JOIN asset_documents d ON d.id = scope.document_id "
            "LEFT JOIN asset_source_batches b "
            "  ON b.id = scope.source_batch_id AND b.domain = %s"
            + where
            + " ORDER BY d.created_at DESC LIMIT %s OFFSET %s",
            _active_scope_params(domain, channel)
            + [domain]
            + filter_params
            + [limit, offset],
        )
        rows = await cur.fetchall()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [dict(row) for row in rows],
    }


@router.get("/batches")
async def list_batches(
    request: Request,
    domain: str = Query(...),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Aggregate current active documents by effective Mining source batch."""
    domain = require_domain(domain)
    channel = _resolve_channel(domain, None)
    pool = await get_domain_async_pool(request, domain)

    async with pool.connection() as conn:
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + """
SELECT
    scope.source_batch_id,
    b.batch_code,
    mr.mining_run_id,
    COUNT(DISTINCT scope.document_id) AS active_document_count,
    b.created_at,
    scope.source_batch_id IS NOT NULL AS deletable,
    scope.source_batch_id IS NULL AS unclassified
FROM active_scope scope
LEFT JOIN asset_source_batches b
  ON b.id = scope.source_batch_id
 AND b.domain = %s
LEFT JOIN LATERAL (
    SELECT runs.id AS mining_run_id
    FROM mining_runs runs
    WHERE runs.source_batch_id = scope.source_batch_id
      AND runs.domain = %s
    ORDER BY runs.started_at DESC
    LIMIT 1
) mr ON TRUE
GROUP BY scope.source_batch_id, b.batch_code, mr.mining_run_id, b.created_at
ORDER BY b.created_at DESC NULLS LAST, scope.source_batch_id NULLS LAST
LIMIT %s OFFSET %s
""",
            _active_scope_params(domain, channel) + [domain, domain, limit, offset],
        )
        rows = await cur.fetchall()

    return {"items": [dict(row) for row in rows]}


@router.get("/documents/{document_id}")
async def get_document(
    document_id: str,
    request: Request,
    domain: str = Query(...),
    channel: str | None = Query(None),
) -> dict:
    """Return a document and only its currently selected active snapshot."""
    domain = require_domain(domain)
    channel = _resolve_channel(domain, channel)
    pool = await get_domain_async_pool(request, domain)

    async with pool.connection() as conn:
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT DISTINCT d.id, d.document_key, d.document_name, "
            "d.document_type, d.created_at, scope.source_batch_id, b.batch_code "
            "FROM active_scope scope "
            "JOIN asset_documents d ON d.id = scope.document_id "
            "LEFT JOIN asset_source_batches b "
            "  ON b.id = scope.source_batch_id AND b.domain = %s "
            "WHERE scope.document_id = %s",
            _active_scope_params(domain, channel) + [domain, document_id],
        )
        document = await cur.fetchone()
        if not document:
            raise HTTPException(404, f"Document {document_id} not found")

        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT DISTINCT ON (s.id) "
            "s.id, s.title, s.normalized_content_hash, s.mime_type, s.created_at, "
            "dsl.linked_at "
            "FROM active_scope scope "
            "JOIN asset_document_snapshots s "
            "  ON s.id = scope.document_snapshot_id "
            "LEFT JOIN asset_document_snapshot_links dsl "
            "  ON dsl.document_id = scope.document_id "
            " AND dsl.document_snapshot_id = scope.document_snapshot_id "
            " AND dsl.source_batch_id IS NOT DISTINCT FROM scope.source_batch_id "
            "WHERE scope.document_id = %s "
            "ORDER BY s.id, dsl.linked_at DESC",
            _active_scope_params(domain, channel) + [document_id],
        )
        public_snapshot_fields = (
            "id",
            "title",
            "normalized_content_hash",
            "mime_type",
            "created_at",
            "linked_at",
        )
        snapshots = [
            {key: row[key] for key in public_snapshot_fields if key in row}
            for row in await cur.fetchall()
        ]

    return {**dict(document), "snapshots": snapshots}


@router.get("/documents/{document_id}/segments")
async def get_document_segments(
    document_id: str,
    request: Request,
    domain: str = Query(...),
    channel: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """List segments from the document's active selected snapshot."""
    domain = require_domain(domain)
    channel = _resolve_channel(domain, channel)
    pool = await get_domain_async_pool(request, domain)

    async with pool.connection() as conn:
        snapshot_id = await _active_snapshot_id(
            conn, document_id=document_id, domain=domain, channel=channel
        )
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT COUNT(DISTINCT seg.id) AS c "
            "FROM asset_raw_segments seg "
            "JOIN active_scope scope "
            "  ON scope.document_snapshot_id = seg.document_snapshot_id "
            "WHERE scope.document_id = %s "
            "  AND scope.document_snapshot_id = %s",
            _active_scope_params(domain, channel) + [document_id, snapshot_id],
        )
        total = (await cur.fetchone())["c"]
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT DISTINCT seg.id, seg.segment_key, seg.segment_index, "
            "seg.block_type, seg.semantic_role, seg.section_title, seg.raw_text, "
            "seg.token_count "
            "FROM asset_raw_segments seg "
            "JOIN active_scope scope "
            "  ON scope.document_snapshot_id = seg.document_snapshot_id "
            "WHERE scope.document_id = %s "
            "  AND scope.document_snapshot_id = %s "
            "ORDER BY seg.segment_index LIMIT %s OFFSET %s",
            _active_scope_params(domain, channel)
            + [document_id, snapshot_id, limit, offset],
        )
        rows = await cur.fetchall()

    return {
        "document_id": document_id,
        "snapshot_id": snapshot_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [dict(row) for row in rows],
    }


@router.get("/documents/{document_id}/units")
async def get_document_units(
    document_id: str,
    request: Request,
    domain: str = Query(...),
    channel: str | None = Query(None),
    unit_type: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """List retrieval units from the document's active selected snapshot."""
    domain = require_domain(domain)
    channel = _resolve_channel(domain, channel)
    pool = await get_domain_async_pool(request, domain)
    type_clause = " AND u.unit_type = %s" if unit_type else ""
    type_params = [unit_type] if unit_type else []

    async with pool.connection() as conn:
        snapshot_id = await _active_snapshot_id(
            conn, document_id=document_id, domain=domain, channel=channel
        )
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT COUNT(DISTINCT u.id) AS c "
            "FROM asset_retrieval_units u "
            "JOIN active_scope scope "
            "  ON scope.document_snapshot_id = u.document_snapshot_id "
            "WHERE scope.document_id = %s "
            "  AND scope.document_snapshot_id = %s"
            + type_clause,
            _active_scope_params(domain, channel)
            + [document_id, snapshot_id]
            + type_params,
        )
        total = (await cur.fetchone())["c"]
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT DISTINCT u.id, u.unit_key, u.unit_type, u.target_type, "
            "u.title, u.text, u.block_type, u.semantic_role, u.weight, u.created_at "
            "FROM asset_retrieval_units u "
            "JOIN active_scope scope "
            "  ON scope.document_snapshot_id = u.document_snapshot_id "
            "WHERE scope.document_id = %s "
            "  AND scope.document_snapshot_id = %s"
            + type_clause
            + " ORDER BY u.created_at LIMIT %s OFFSET %s",
            _active_scope_params(domain, channel)
            + [document_id, snapshot_id]
            + type_params
            + [limit, offset],
        )
        rows = await cur.fetchall()

    return {
        "document_id": document_id,
        "snapshot_id": snapshot_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [dict(row) for row in rows],
    }


async def get_document_relations(
    document_id: str,
    request: Request,
    domain: str = Query(...),
    channel: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """List relations from the document's active selected snapshot."""
    domain = require_domain(domain)
    channel = _resolve_channel(domain, channel)
    pool = await get_domain_async_pool(request, domain)

    async with pool.connection() as conn:
        snapshot_id = await _active_snapshot_id(
            conn, document_id=document_id, domain=domain, channel=channel
        )
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT COUNT(DISTINCT rel.id) AS c "
            "FROM asset_raw_segment_relations rel "
            "JOIN active_scope scope "
            "  ON scope.document_snapshot_id = rel.document_snapshot_id "
            "WHERE scope.document_id = %s "
            "  AND scope.document_snapshot_id = %s",
            _active_scope_params(domain, channel) + [document_id, snapshot_id],
        )
        total = (await cur.fetchone())["c"]
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT DISTINCT rel.id, rel.document_snapshot_id, "
            "rel.source_segment_id, rel.target_segment_id, rel.relation_type, "
            "rel.weight, rel.confidence, rel.distance, "
            "s1.raw_text AS source_text, s2.raw_text AS target_text "
            "FROM asset_raw_segment_relations rel "
            "JOIN active_scope scope "
            "  ON scope.document_snapshot_id = rel.document_snapshot_id "
            "LEFT JOIN asset_raw_segments s1 ON s1.id = rel.source_segment_id "
            "LEFT JOIN asset_raw_segments s2 ON s2.id = rel.target_segment_id "
            "WHERE scope.document_id = %s "
            "  AND scope.document_snapshot_id = %s "
            "ORDER BY rel.confidence DESC NULLS LAST LIMIT %s OFFSET %s",
            _active_scope_params(domain, channel)
            + [document_id, snapshot_id, limit, offset],
        )
        rows = await cur.fetchall()

    return {
        "document_id": document_id,
        "snapshot_id": snapshot_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [dict(row) for row in rows],
    }


@router.get("/segments")
async def list_segments(
    request: Request,
    domain: str = Query(...),
    channel: str | None = Query(None),
    role: str | None = None,
    type: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """List active segments across documents."""
    domain = require_domain(domain)
    channel = _resolve_channel(domain, channel)
    pool = await get_domain_async_pool(request, domain)
    conditions: list[str] = []
    filter_params: list[Any] = []
    if role:
        conditions.append("seg.semantic_role = %s")
        filter_params.append(role)
    if type:
        conditions.append("seg.block_type = %s")
        filter_params.append(type)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    async with pool.connection() as conn:
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT COUNT(DISTINCT seg.id) AS c "
            "FROM asset_raw_segments seg "
            "JOIN active_scope scope "
            "  ON scope.document_snapshot_id = seg.document_snapshot_id"
            + where,
            _active_scope_params(domain, channel) + filter_params,
        )
        total = (await cur.fetchone())["c"]
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT DISTINCT seg.id, seg.document_snapshot_id, seg.segment_key, "
            "seg.segment_index, seg.block_type, seg.semantic_role, seg.section_title, "
            "LEFT(seg.raw_text, 200) AS raw_text_preview, seg.token_count "
            "FROM asset_raw_segments seg "
            "JOIN active_scope scope "
            "  ON scope.document_snapshot_id = seg.document_snapshot_id"
            + where
            + " ORDER BY seg.document_snapshot_id, seg.segment_index LIMIT %s OFFSET %s",
            _active_scope_params(domain, channel)
            + filter_params
            + [limit, offset],
        )
        rows = await cur.fetchall()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [dict(row) for row in rows],
    }


@router.get("/units")
async def list_units(
    request: Request,
    domain: str = Query(...),
    channel: str | None = Query(None),
    unit_type: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """List active retrieval units across documents."""
    domain = require_domain(domain)
    channel = _resolve_channel(domain, channel)
    pool = await get_domain_async_pool(request, domain)
    where = " WHERE u.unit_type = %s" if unit_type else ""
    filter_params = [unit_type] if unit_type else []

    async with pool.connection() as conn:
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT COUNT(DISTINCT u.id) AS c "
            "FROM asset_retrieval_units u "
            "JOIN active_scope scope "
            "  ON scope.document_snapshot_id = u.document_snapshot_id"
            + where,
            _active_scope_params(domain, channel) + filter_params,
        )
        total = (await cur.fetchone())["c"]
        cur = await conn.execute(
            _ACTIVE_SCOPE_CTE
            + "SELECT DISTINCT u.id, u.document_snapshot_id, u.unit_key, "
            "u.unit_type, u.target_type, u.title, LEFT(u.text, 200) AS text_preview, "
            "u.block_type, u.semantic_role, u.weight, u.created_at "
            "FROM asset_retrieval_units u "
            "JOIN active_scope scope "
            "  ON scope.document_snapshot_id = u.document_snapshot_id"
            + where
            + " ORDER BY u.created_at DESC LIMIT %s OFFSET %s",
            _active_scope_params(domain, channel)
            + filter_params
            + [limit, offset],
        )
        rows = await cur.fetchall()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [dict(row) for row in rows],
    }
