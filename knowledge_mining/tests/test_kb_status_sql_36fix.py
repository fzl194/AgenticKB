"""36 号：KB 状态与 readiness 必须以当前 serving 事实为准。"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from knowledge_mining.mining.kb.db import (
    KbDB,
    _KB_BUILD_JOIN_SQL,
    _RUN_DOC_JOIN_SQL,
)


def test_latest_run_document_orders_by_run_start_not_nullable_finish() -> None:
    """当前 processing 行 finished_at=NULL，也必须压过旧终态。"""
    order = _RUN_DOC_JOIN_SQL.split("ORDER BY", 1)[1]
    assert "mr.started_at DESC" in order
    assert "r.finished_at DESC NULLS LAST" not in order


def test_build_membership_uses_latest_selection_per_document() -> None:
    """历史稀疏 Build 下，状态口径要与 serving 的 per-document latest 对齐。"""
    assert "bs.document_id = d.id" in _KB_BUILD_JOIN_SQL
    assert "ORDER BY b.created_at DESC, b.id DESC" in _KB_BUILD_JOIN_SQL
    assert "SELECT b.id FROM asset_builds" not in _KB_BUILD_JOIN_SQL


def test_asset_stats_do_not_fall_back_past_latest_removed_selection() -> None:
    cte = KbDB._CURRENT_SNAPSHOT_CTE
    latest_body, current_body = cte.split("), cur AS (", 1)
    assert "bs.selection_status = 'active'" not in latest_body
    assert "selection_status = 'active'" in current_body


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, owner):
        self._owner = owner

    async def execute(self, sql, params):
        self._owner.sql = sql
        self._owner.params = params
        return _Cursor({
            "documents": 1,
            "segments": 2,
            "retrieval_units": 3,
            "embeddings": 3,
            "embedding_fallback": False,
        })


class _Pool:
    def __init__(self):
        self.sql = ""
        self.params = None

    @asynccontextmanager
    async def connection(self):
        yield _Connection(self)


@pytest.mark.asyncio
async def test_kb_readiness_counts_validated_build_members_not_latest_links() -> None:
    pool = _Pool()
    readiness = await KbDB(pool).get_kb_readiness("kb-a")

    assert readiness["level"] == "vector_ready"
    assert "asset_build_document_snapshots" in pool.sql
    assert "asset_document_snapshot_links l" not in pool.sql
    assert "selection_status" in pool.sql
