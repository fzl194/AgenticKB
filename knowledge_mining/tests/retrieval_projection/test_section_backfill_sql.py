"""P1-2 回归：A2 回填 SQL 契约（真实列名）+ 收敛语义（P2-18）.

缺陷：回填查询用了不存在的 ``asset_raw_segments.snapshot_id`` /
``heading_chain_json``——生产 DDL 是 ``document_snapshot_id`` /
``section_path``（TEXT 存 ``[{level, title}]`` JSON）。任意真实快照
都会 UndefinedColumn，CLI 全败。

P2-18 同批修复的收敛语义：
- 候选/明细查询排除 document 单元（按设计其 section_ref 恒 NULL，
  否则每次运行都命中"文档级缺口"永不收敛）；
- 统计用真实 UPDATE 行数（affected rows），不按计划数虚报；
- 二跑零更新（updated_units == 0 且 updated_snapshots == 0）。

SQL 契约测试不依赖真实 PG：用录制 pool 断言发出的 SQL 文本与参数。
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from tests.retrieval_projection.test_repositories_pg import (
    _RecordingConnection, _RecordingConnectionContext, statements_of,
)


class _BackfillPool:
    """录制 pool：SELECT 按 SQL 关键词路由返回预置行."""

    def __init__(self, rows_by_keyword: dict[str, list[dict[str, Any]]]):
        self.log: list[tuple[str, list | None]] = []
        self._rows_by_keyword = rows_by_keyword

    def connection(self) -> _RecordingConnectionContext:
        return _BackfillConnContext(self)


class _BackfillConn(_RecordingConnection):
    def __init__(self, pool: _BackfillPool):
        super().__init__()
        self._pool = pool

    async def execute(self, query: str, params: Any = None) -> Any:
        normalized = " ".join(query.split())
        self._pool.log.append(
            (normalized, list(params) if params is not None else None)
        )
        if normalized.startswith("SELECT"):
            for keyword, rows in self._pool._rows_by_keyword.items():
                if keyword in normalized:
                    return _RowsCursor(rows)
        return _RowsCursor([])

    def transaction(self):
        from tests.retrieval_projection.test_repositories_pg import (
            _RecordingTransaction,
        )
        return _RecordingTransaction(self._pool.log)


class _BackfillConnContext(_RecordingConnectionContext):
    def __init__(self, pool: _BackfillPool):
        super().__init__(_BackfillConn(pool))


class _RowsCursor:
    def __init__(self, rows: list):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


# ---------------------------------------------------------------- SQL 契约


def _segments_rows() -> list[dict[str, Any]]:
    # 生产真实形状：section_path 是 TEXT 列存 [{level, title}] JSON
    return [
        {"segment_index": 0, "section_path": json.dumps(
            [{"level": 1, "title": "A"}, {"level": 2, "title": "B"}])},
        {"segment_index": 1, "section_path": json.dumps(
            [{"level": 1, "title": "A"}])},
        {"segment_index": 2, "section_path": None},
    ]


def _units_rows() -> list[dict[str, Any]]:
    return [
        {"representation_id": "u-p0", "representation_type": "prose",
         "target_ref": "d#seg:0", "ordinal": 0, "provenance_json": "{}"},
    ]


@pytest.mark.asyncio
async def test_segment_query_uses_real_column_names():
    from knowledge_mining.mining.retrieval_projection.section_backfill import (
        backfill,
    )

    pool = _BackfillPool({
        "FROM asset_document_snapshots": [{"id": "snap-1"}],
        "FROM asset_raw_segments": _segments_rows(),
        "FROM asset_retrieval_units_v2": (
            [{"target_ref": "d#document"}]
            + _units_rows()
        ),
    })
    stats = await backfill(pool=pool, domain="default", dry_run=False)
    seg_sql = next(
        (sql for sql, _ in statements_of(pool)
         if "FROM asset_raw_segments" in sql), "",
    )
    assert seg_sql, "asset_raw_segments query not issued"
    assert "document_snapshot_id" in seg_sql, (
        "must use document_snapshot_id (production DDL), not snapshot_id"
    )
    assert "section_path" in seg_sql, (
        "must read section_path (production DDL), not heading_chain_json"
    )
    assert "snapshot_id" not in seg_sql.replace("document_snapshot_id", ""), (
        "bare snapshot_id does not exist on asset_raw_segments"
    )


@pytest.mark.asyncio
async def test_section_path_dict_shape_parsed():
    """section_path 的 [{level, title}] 形状被正确解析为标题路径 ref."""
    from knowledge_mining.mining.retrieval_projection.section_backfill import (
        plan_section_refs,
    )

    segments = [
        # 真实形状：list[dict] 序列化（pipeline 写入形态）
        {"segment_index": 0, "section_path": json.dumps(
            [{"level": 1, "title": "A"}, {"level": 2, "title": "B"}])},
        # 兼容形态：[[level, title]] 序列化（旧测试口径）
        {"segment_index": 1, "section_path": json.dumps([[1, "A"]])},
    ]
    units = [
        {"representation_id": "u-p0", "representation_type": "prose",
         "target_ref": "d#seg:0", "ordinal": 0, "provenance_json": "{}"},
        {"representation_id": "u-p1", "representation_type": "prose",
         "target_ref": "d#seg:1", "ordinal": 1, "provenance_json": "{}"},
    ]
    plan = plan_section_refs(
        segments=segments, units=units, document_ref="d",
    )
    assert plan == {
        "u-p0": "d#section:A/B",
        "u-p1": "d#section:A",
    }, plan


@pytest.mark.asyncio
async def test_document_units_excluded_from_backfill_targets():
    """P2-18：文档级单元 section_ref 恒 NULL（设计），不进更新计划."""
    from knowledge_mining.mining.retrieval_projection.section_backfill import (
        plan_section_refs,
    )

    segments = [{"segment_index": 0, "section_path": json.dumps([[1, "A"]])}]
    units = [
        {"representation_id": "u-doc", "representation_type": "document",
         "target_ref": "d#document", "ordinal": -1, "provenance_json": "{}"},
        {"representation_id": "u-p0", "representation_type": "prose",
         "target_ref": "d#seg:0", "ordinal": 0, "provenance_json": "{}"},
    ]
    plan = plan_section_refs(segments=segments, units=units, document_ref="d")
    assert plan == {"u-p0": "d#section:A"}


@pytest.mark.asyncio
async def test_second_run_reports_zero_updates():
    """二跑（units 明细返回空——全部已回填）零更新零快照计数."""
    from knowledge_mining.mining.retrieval_projection.section_backfill import (
        backfill,
    )

    pool = _BackfillPool({
        # 候选查询（含 u. 别名）；即使有 NULL 也返回候选——由明细判定收敛
        "u.section_ref IS NULL": [{"id": "snap-1"}],
        "FROM asset_document_snapshots": [{"id": "snap-1"}],
        "FROM asset_raw_segments": _segments_rows(),
        "AND section_ref IS NULL AND representation_type NOT IN": [],
        "FROM asset_retrieval_units_v2": [{"target_ref": "d#document"}],
    })
    stats = await backfill(pool=pool, domain="default")
    assert stats.updated_snapshots == 0
    assert stats.updated_units == 0
    assert stats.already_done == 1
    assert stats.failed == []


@pytest.mark.asyncio
async def test_update_counts_use_affected_rows_not_plan_size():
    """统计=真实受影响行数：UPDATE 返回 0 行时不得按计划数虚报.

    录制 cursor 的 execute 结果不可行（psycopg 返回 rowcount），此处
    以「UPDATE 语句参数即计划、stats.updated_units 以事务内实际写行
    计」的契约锁定：backfill 的 updated_units 必须等于 UPDATE 语句数
    （每语句一行）而非 len(plan)（含无匹配行的计划项）。
    """
    from knowledge_mining.mining.retrieval_projection.section_backfill import (
        backfill,
    )

    pool = _BackfillPool({
        "u.section_ref IS NULL": [{"id": "snap-1"}],
        "FROM asset_document_snapshots": [{"id": "snap-1"}],
        "FROM asset_raw_segments": _segments_rows(),
        "AND section_ref IS NULL AND representation_type NOT IN": _units_rows(),
        "FROM asset_retrieval_units_v2": (
            [{"target_ref": "d#document"}] + _units_rows()
        ),
    })
    stats = await backfill(pool=pool, domain="default")
    updates = [sql for sql, _ in statements_of(pool)
               if sql.startswith("UPDATE asset_retrieval_units_v2")]
    assert updates, "no UPDATE issued"
    # 每条 UPDATE 恰好对应一个计划项（u-p0）；统计=UPDATE 语句数
    assert stats.updated_units == len(updates) == 1
    assert stats.failed == []
