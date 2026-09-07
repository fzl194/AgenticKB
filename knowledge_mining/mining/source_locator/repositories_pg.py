"""PostgreSQL locator 存储（A1，38 号 §2.3）.

- 只做参数化 DML，DDL 真相源 = ``014_a1_source_locators.sql``（pg_schema 启动迁移）；
- 快照级替换（同 PgRepresentationStore 语义）：幂等重跑 = 事务内先删再插；
- ``promote_locators`` 是**专用最小晋升**（受控重放专用）：只动 locator
  双表，绝不动其他六张派生表——不得复用 ``promote_snapshot_assets``
  （那会清空 staging 为空的 final 表）。挖掘主链的晋升由
  ``PROMOTE_TABLE_COLUMNS`` 纳入 Build 组装事务，不经本方法；
- ``list_final_representations``：重放路径读 **final** units（committed
  快照的 staging 已在晋升时清空，不能复用 staging 读）。
"""
from __future__ import annotations

import json
from typing import Any

from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrievalRepresentation,
)
from knowledge_mining.mining.source_locator.extract import (
    LOCATOR_VERSION,
    LocatorRecord,
)

_LOCATORS_DELETE = (
    "DELETE FROM asset_source_locators_staging WHERE snapshot_id = %s"
)

_LOCATORS_INSERT = """
    INSERT INTO asset_source_locators_staging (
        snapshot_id, representation_id, target_ref, document_ref,
        source_format, locator_kind, section_path, section_element_id,
        page, line_start, line_end, sheet, cell, table_ref, row_index,
        native_ref_json, description, locator_version
    ) VALUES (
        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s
    )
"""

_LOCATORS_SELECT = """
    SELECT snapshot_id, representation_id, target_ref, document_ref,
           source_format, locator_kind, section_path, section_element_id,
           page, line_start, line_end, sheet, cell, table_ref, row_index,
           native_ref_json, description, locator_version
    FROM asset_source_locators
    WHERE snapshot_id = %s
    ORDER BY representation_id
"""

_FINAL_UNITS_SELECT = """
    SELECT representation_id, representation_type, content_type, content_text,
           structural_context, target_type, target_ref, canonical_evidence_id,
           container_ref, parent_ref, context_group_id, source_refs_json,
           ordinal, lexical_eligible, dense_eligible,
           returnable, facets_json, provenance_json
    FROM asset_retrieval_units_v2
    WHERE snapshot_id = %s
    ORDER BY ordinal, representation_id
"""


class PgLocatorStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def replace_for_snapshot(
        self, snapshot_id: str, records: tuple[LocatorRecord, ...]
    ) -> int:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(_LOCATORS_DELETE, [snapshot_id])
                for record in records:
                    await conn.execute(
                        _LOCATORS_INSERT,
                        [
                            snapshot_id, record.representation_id,
                            record.target_ref, record.document_ref,
                            record.source_format, record.locator_kind,
                            record.section_path, record.section_element_id,
                            record.page, record.line_start, record.line_end,
                            record.sheet, record.cell, record.table_ref,
                            record.row_index,
                            json.dumps(record.native_ref, ensure_ascii=False)
                            if record.native_ref is not None
                            else None,
                            record.description,
                            LOCATOR_VERSION,
                        ],
                    )
        return len(records)

    async def list_final_for_snapshot(
        self, snapshot_id: str
    ) -> tuple[LocatorRecord, ...]:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(_LOCATORS_SELECT, [snapshot_id])
            rows = await cursor.fetchall()
        return tuple(_record_of(dict(row)) for row in rows)

    async def list_final_representations(
        self, snapshot_id: str
    ) -> tuple[RetrievalRepresentation, ...]:
        """重放路径的 units 来源：final 表（committed 快照）."""
        async with self._pool.connection() as conn:
            cursor = await conn.execute(_FINAL_UNITS_SELECT, [snapshot_id])
            rows = await cursor.fetchall()
        return tuple(_representation_of(dict(row)) for row in rows)

    async def promote_locators(self, snapshot_ids: list[str]) -> int:
        """受控重放专用：staging → final 单快照原子晋升（只动 locator 双表）.

        列清单取 PROMOTE_TABLE_COLUMNS 单一真相源（显式列，禁 SELECT *——
        staging 是 LIKE 建表，014 后续加列不会同步到既有 staging，SELECT *
        会因列数错位失败；显式列在此 fail-fast 且信息明确）。
        """
        if not snapshot_ids:
            return 0
        from knowledge_mining.mining.retrieval_projection.schema import (
            PROMOTE_TABLE_COLUMNS,
        )

        columns = next(
            cols for table, cols in PROMOTE_TABLE_COLUMNS
            if table == "asset_source_locators"
        )
        col_list = ", ".join(columns)
        async with self._pool.connection() as conn:
            async with conn.transaction():
                for snapshot_id in snapshot_ids:
                    await conn.execute(
                        "DELETE FROM asset_source_locators WHERE snapshot_id = %s",
                        [snapshot_id],
                    )
                    await conn.execute(
                        f"INSERT INTO asset_source_locators ({col_list}) "
                        f"SELECT {col_list} FROM asset_source_locators_staging "
                        "WHERE snapshot_id = %s",
                        [snapshot_id],
                    )
                    await conn.execute(
                        "DELETE FROM asset_source_locators_staging "
                        "WHERE snapshot_id = %s",
                        [snapshot_id],
                    )
        return len(snapshot_ids)


def _record_of(row: dict[str, Any]) -> LocatorRecord:
    native = row.get("native_ref_json")
    return LocatorRecord(
        representation_id=str(row["representation_id"]),
        target_ref=str(row["target_ref"]),
        document_ref=str(row["document_ref"]),
        source_format=str(row["source_format"]),
        locator_kind=str(row["locator_kind"]),
        section_path=row.get("section_path"),
        section_element_id=row.get("section_element_id"),
        page=row.get("page"),
        line_start=row.get("line_start"),
        line_end=row.get("line_end"),
        sheet=row.get("sheet"),
        cell=row.get("cell"),
        table_ref=row.get("table_ref"),
        row_index=row.get("row_index"),
        native_ref=dict(native) if isinstance(native, dict) else None,
        description=row.get("description"),
    )


def _representation_of(row: dict[str, Any]) -> RetrievalRepresentation:
    def _as_str(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    def _as_dict(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _as_list(value: Any) -> list[Any]:
        return list(value) if isinstance(value, (list, tuple)) else []

    return RetrievalRepresentation(
        representation_id=str(row["representation_id"]),
        representation_type=str(row["representation_type"]),
        content_type=str(row["content_type"]),
        content_text=str(row["content_text"]),
        target_type=str(row["target_type"]),
        target_ref=str(row["target_ref"]),
        canonical_evidence_id=str(row["canonical_evidence_id"]),
        structural_context=str(row.get("structural_context") or ""),
        container_ref=_as_str(row.get("container_ref")),
        parent_ref=_as_str(row.get("parent_ref")),
        context_group_id=_as_str(row.get("context_group_id")),
        source_refs=tuple(
            dict(item) for item in _as_list(row.get("source_refs_json"))
            if isinstance(item, dict)
        ),
        ordinal=int(row.get("ordinal") or 0),
        lexical_eligible=bool(row.get("lexical_eligible")),
        dense_eligible=bool(row.get("dense_eligible")),
        returnable=bool(row.get("returnable")),
        facets=_as_dict(row.get("facets_json")),
        provenance=_as_dict(row.get("provenance_json")),
    )
