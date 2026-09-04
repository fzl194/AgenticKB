"""A0-5（34 号 §P0）：文档检索单元切换正式 v2 数据.

此前文档详情「检索单元」读旧 ``asset_retrieval_units``（v1 遗留表），而正式挖掘
只写 v2。本契约钉住：
- 读取 ``asset_retrieval_units_v2``（current_serving 快照范围）；
- 对外类型 = 公开词表（code_block→code、list_group→list），与 Java
  ``EvidenceTypeVocabulary`` 同一套九词；
- 默认只展示可返回的原始证据表示（returnable）；query_alias/summary_alias
  是搜索辅助，进 ``search_assist_units``（前端高级信息），不冒充原文知识。
"""
from __future__ import annotations

import pytest


class _FakeCur:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, results):
        self._results = list(results)
        self.statements: list[str] = []

    async def execute(self, sql, params):
        self.statements.append(sql)
        return _FakeCur(self._results.pop(0) if self._results else [])


class _FakePool:
    def __init__(self, results):
        self._conn = _FakeConn(results)

    def connection(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool._conn

            async def __aexit__(self, *a):
                return False
        return _Ctx()


def _make_kbdb(results):
    from knowledge_mining.mining.kb.db import KbDB
    db = object.__new__(KbDB)
    db._pool = _FakePool(results)
    return db


def _serving_row():
    return [{
        "document_snapshot_id": "snap-1", "build_id": "b-1",
        "source_storage_object_id": "so-1", "source_content_revision": 1,
        "snapshot_created_at": "2026-09-03T00:00:00Z",
    }]


def _v2_row(rep_id: str, rep_type: str, *, returnable: bool = True,
            text: str = "正文", context: str = "章节 A") -> dict:
    return {
        "representation_id": rep_id,
        "representation_type": rep_type,
        "content_text": text,
        "structural_context": context,
        "returnable": returnable,
    }


@pytest.mark.asyncio
async def test_units_read_from_v2_table():
    """检索单元必须读 asset_retrieval_units_v2（正式挖掘写入面），不是旧 v1 表."""
    db = _make_kbdb([
        _serving_row(),
        [],  # segments
        [ _v2_row("r1", "prose") ],
    ])
    result = await db.get_document_knowledge("kb-1", "doc-1")
    assert result["mined"] is True
    units_sql = db._pool._conn.statements[-1]
    assert "asset_retrieval_units_v2" in units_sql
    assert "asset_retrieval_units" in units_sql  # 前缀包含关系（防只查列名）
    assert "asset_retrieval_units " not in units_sql + " "
    assert result["retrieval_units"][0]["representation_id"] == "r1"


@pytest.mark.asyncio
async def test_public_type_vocabulary_matches_java():
    """对外类型 = 公开九词；内部 code_block/list_group 在服务边界转公开词."""
    db = _make_kbdb([
        _serving_row(),
        [],
        [
            _v2_row("r-prose", "prose"),
            _v2_row("r-code", "code_block"),
            _v2_row("r-list", "list_group"),
            _v2_row("r-table", "table"),
            _v2_row("r-table-row", "table_row"),
            _v2_row("r-section", "section"),
            _v2_row("r-doc", "document"),
            _v2_row("r-formula", "formula"),
            _v2_row("r-figcap", "figure_caption"),
        ],
    ])
    result = await db.get_document_knowledge("kb-1", "doc-1")
    types = {u["unit_type"] for u in result["retrieval_units"]}
    # 与 Java EvidenceTypeVocabulary.PUBLIC_TYPES 同一套九词（契约：两侧同步）
    assert types == {
        "prose", "code", "list", "table", "table_row",
        "section", "document", "formula", "figure_caption",
    }


@pytest.mark.asyncio
async def test_alias_units_separated_as_search_assist():
    """query_alias/summary_alias 是搜索辅助表示——不进默认检索表示清单，
    单独放 search_assist_units（前端高级信息），不冒充原文知识."""
    db = _make_kbdb([
        _serving_row(),
        [],
        [
            _v2_row("r-prose", "prose"),
            _v2_row("r-qa", "query_alias", text="可能的问题"),
            _v2_row("r-sa", "summary_alias", text="章节摘要"),
        ],
    ])
    result = await db.get_document_knowledge("kb-1", "doc-1")

    unit_ids = {u["representation_id"] for u in result["retrieval_units"]}
    assert unit_ids == {"r-prose"}

    assist = {u["representation_id"]: u for u in result["search_assist_units"]}
    assert set(assist) == {"r-qa", "r-sa"}
    assert assist["r-qa"]["unit_type"] == "query_alias"
    assert assist["r-sa"]["unit_type"] == "summary_alias"


@pytest.mark.asyncio
async def test_non_returnable_units_excluded_by_default():
    """不可返回的表示（returnable=FALSE）不进默认清单."""
    db = _make_kbdb([
        _serving_row(),
        [],
        [
            _v2_row("r-ok", "prose", returnable=True),
            _v2_row("r-hidden", "prose", returnable=False),
        ],
    ])
    result = await db.get_document_knowledge("kb-1", "doc-1")
    ids = {u["representation_id"] for u in result["retrieval_units"]}
    assert ids == {"r-ok"}


@pytest.mark.asyncio
async def test_units_carry_structural_context():
    """检索单元带章节上下文（structural_context）供前端展示归属."""
    db = _make_kbdb([
        _serving_row(),
        [],
        [_v2_row("r1", "prose", context="第 1 章 > 1.2 节")],
    ])
    result = await db.get_document_knowledge("kb-1", "doc-1")
    assert result["retrieval_units"][0]["structural_context"] == "第 1 章 > 1.2 节"


@pytest.mark.asyncio
async def test_mined_false_when_no_serving():
    """无 current_serving：mined=False（不虚构 v2 数据）."""
    db = _make_kbdb([None])  # serving 查询无行
    result = await db.get_document_knowledge("kb-1", "doc-1")
    assert result["mined"] is False
    assert result["build_id"] is None
