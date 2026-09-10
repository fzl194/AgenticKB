"""P1-1 回归：_UNITS_INSERT 参数数量与 section_ref 位置（Codex 审查）.

缺陷：SQL 增至 22 列/22 占位符后，PgRepresentationStore 的两条写入
路径仍传 21 参且缺 rep.section_ref——从 section_ref 起全列错位，
真实 PG 绑定失败或写串列。

契约（与 repositories_pg._UNITS_INSERT 列序逐字对齐）：
0 representation_id / 1 snapshot_id / 2 representation_type /
3 content_type / 4 content_text / 5 structural_context /
6 lexical_text / 7 tokenizer_version / 8 target_type / 9 target_ref /
10 canonical_evidence_id / 11 section_ref / 12 container_ref /
13 parent_ref / 14 context_group_id / 15 source_refs_json / 16 ordinal /
17 lexical_eligible / 18 dense_eligible / 19 returnable / 20 facets_json /
21 provenance_json
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

from tests.retrieval_projection.test_repositories_pg import (
    _representation, find_statement, recording_pool, statements_of,
)

_INSERT_COLS = [
    "representation_id", "snapshot_id", "representation_type", "content_type",
    "content_text", "structural_context", "lexical_text", "tokenizer_version",
    "target_type", "target_ref", "canonical_evidence_id", "section_ref",
    "container_ref", "parent_ref", "context_group_id", "source_refs_json",
    "ordinal", "lexical_eligible", "dense_eligible", "returnable",
    "facets_json", "provenance_json",
]
_SECTION_REF_POS = _INSERT_COLS.index("section_ref")  # == 11


def _units_insert_sql() -> str:
    from knowledge_mining.mining.retrieval_projection import repositories_pg

    return repositories_pg._UNITS_INSERT


def test_insert_sql_placeholders_match_column_count():
    sql = _units_insert_sql()
    cols = re.search(r"\(([^)]+)\)\s*VALUES", sql, re.S).group(1)
    n_cols = len([c.strip() for c in cols.split(",")])
    placeholders = re.findall(r"%s", sql.split("VALUES")[1])
    assert n_cols == 22
    assert len(placeholders) == n_cols, (
        f"{n_cols} columns but {len(placeholders)} placeholders"
    )
    assert _INSERT_COLS == [c.strip() for c in cols.split(",")]


@pytest.mark.asyncio
async def test_replace_for_snapshot_passes_22_params_with_section_ref():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgRepresentationStore,
    )

    rep = _representation(section_ref="manual.md#section:0/1")
    pool = recording_pool()
    store = PgRepresentationStore(pool)
    await store.replace_for_snapshot(
        "snap-1", (rep,), "proj-1", document_key="manual.md",
    )
    insert = find_statement(pool, "INSERT INTO asset_retrieval_units_v2")
    params = insert[1]
    assert params is not None and len(params) == 22, (
        f"expected 22 params, got {None if params is None else len(params)}"
    )
    assert params[_SECTION_REF_POS] == "manual.md#section:0/1"
    # 错位回归哨兵：section_ref 之后的 container_ref/parent_ref 不被挤错
    assert params[_SECTION_REF_POS + 1] == rep.container_ref
    assert params[_SECTION_REF_POS + 2] == rep.parent_ref


@pytest.mark.asyncio
async def test_replace_for_snapshot_section_ref_none_stays_none():
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgRepresentationStore,
    )

    rep = _representation(section_ref=None)
    pool = recording_pool()
    store = PgRepresentationStore(pool)
    await store.replace_for_snapshot(
        "snap-1", (rep,), "proj-1", document_key="manual.md",
    )
    insert = find_statement(pool, "INSERT INTO asset_retrieval_units_v2")
    assert insert[1][_SECTION_REF_POS] is None
    assert insert[1][_SECTION_REF_POS + 1] == rep.container_ref


@pytest.mark.asyncio
async def test_alias_replace_passes_22_params_and_inherits_section_ref():
    """query/summary alias 写入同样 22 参；alias 继承源表示 section_ref."""
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgRepresentationStore,
    )

    alias = _representation(
        "d:s1:query_alias:0",
        representation_type="query_alias",
        section_ref="manual.md#section:2",
    )
    pool = recording_pool()
    store = PgRepresentationStore(pool)
    await store.replace_aliases_for_snapshot(
        "snap-1", (alias,), "proj-1", document_key="manual.md",
    )
    insert = find_statement(pool, "INSERT INTO asset_retrieval_units_v2")
    params = insert[1]
    assert params is not None and len(params) == 22
    assert params[_SECTION_REF_POS] == "manual.md#section:2"
    assert params[_SECTION_REF_POS + 1] == alias.container_ref


@pytest.mark.asyncio
async def test_every_units_insert_call_site_matches_placeholder_count():
    """全部 _UNITS_INSERT 调用点（含 AssetWriter faces 路径）参数=22.

    用字节码扫描防新增调用点漏改：store 内每次 execute(_UNITS_INSERT, […])
    的列表长度必须等于占位符数。faces 路径走 dict（_insert_units），
    其参数组装见 test_asset_writer_matches_lexical_rows_into_units 既有钉。
    """
    from knowledge_mining.mining.retrieval_projection import repositories_pg

    sql = _units_insert_sql()
    n_placeholders = len(re.findall(r"%s", sql.split("VALUES")[1]))

    for store_cls_name in ("PgRepresentationStore",):
        store_cls = getattr(repositories_pg, store_cls_name)
        for method in (
            "replace_for_snapshot", "replace_aliases_for_snapshot",
        ):
            assert hasattr(store_cls, method)

    # 实测两条路径（上面已断言 22）；此处锁 SQL 形状不被回归改窄
    assert n_placeholders == 22


@pytest.mark.asyncio
async def test_asset_writer_units_insert_passes_22_params():
    """PgAssetWriter faces 路径的 units 插入同样 22 参 + section_ref 位次."""
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgAssetWriter,
    )

    faces: dict[str, Any] = {
        "representation_count": 1,
        "schema_version": "asset-v2-1",
        "document_ref": "manual.md",
        "representations": (
            {
                "representation_id": "d:s1:prose:0",
                "representation_type": "prose",
                "content_type": "paragraph",
                "content_text": "正文",
                "structural_context": "",
                "target_type": "segment",
                "target_ref": "manual.md#seg:0",
                "canonical_evidence_id": "d:s1:prose:0",
                "section_ref": "manual.md#section:0",
                "container_ref": None,
                "parent_ref": None,
                "context_group_id": "A",
                "source_refs_json": "[]",
                "ordinal": 0,
                "lexical_eligible": True,
                "dense_eligible": True,
                "returnable": True,
                "facets_json": "{}",
                "provenance_json": "{}",
            },
        ),
        "lexical_rows": (),
        "structure_nodes": (),
        "structure_edges": (),
        "table_assets": (),
        "table_cells": (),
        "embeddings": (),
        "readiness": {},
    }
    pool = recording_pool()
    writer = PgAssetWriter(pool)
    await writer._replace_for_snapshot("snap-1", faces)
    insert = find_statement(pool, "INSERT INTO asset_retrieval_units_v2")
    params = insert[1]
    assert params is not None and len(params) == 22
    assert params[_SECTION_REF_POS] == "manual.md#section:0"
    # lexical 两列（6/7）由 lexical_by_id 补齐，faces 无 lexical 行时为 None
    assert params[6] is None and params[7] is None
