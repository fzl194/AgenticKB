"""Health check route."""
from __future__ import annotations

from fastapi import APIRouter, Request

from knowledge_mining.mining.services.retrieval_records import (
    get_access_record_metrics,
)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict:
    """Health check — PostgreSQL connectivity."""
    pool = request.app.state.pg_pool
    db_ok = False
    try:
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
            db_ok = True
    except Exception:
        pass

    return {
        "status": "ok" if db_ok else "degraded",
        "service": "mining-api",
        "version": "3.0.0",
        "postgresql": "connected" if db_ok else "disconnected",
        **get_access_record_metrics(),
    }


@router.get("/api/system/status")
async def system_status(request: Request) -> dict:
    """System-level status: DB pool info, recent run status."""
    pool = request.app.state.pg_pool

    pool_info = {
        "min_size": pool.min_size,
        "max_size": pool.max_size,
        "size": pool.size if hasattr(pool, "size") else "N/A",
    }

    recent_runs = []
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, status, started_at FROM mining_runs "
                "ORDER BY started_at DESC LIMIT 5"
            )
            rows = await cur.fetchall()
            recent_runs = [
                {"run_id": r["id"], "status": r["status"], "started_at": r["started_at"]}
                for r in rows
            ]
    except Exception:
        pass

    return {
        "pool": pool_info,
        "recent_runs": recent_runs,
    }
