"""57 号工作线①：文件搜索过滤（query / directory_prefix / status）——SQL 形状守卫。

不需要 PostgreSQL：用捕获池抓 KbDB 生成的 SQL 与绑定参数，钉住——
- ILIKE 转义（%/_/\ 按字面匹配）与子串模式；
- directory_prefix 的 (= OR LIKE 'pre/%') 递归边界（'产品' 不误中 '产品文档'）；
- status 过滤走子查询层（派生列不能直接进 WHERE）；
- 零过滤时与既有浏览口径完全一致（无 ILIKE/无子查询）。

真实行为（中文模糊、跨目录命中、状态矩阵）见 test_document_search_filters_pg.py
（需 PostgreSQL）。
"""
from __future__ import annotations

from typing import Any

import pytest

from knowledge_mining.mining.kb.db import (
    KbDB,
    _document_search_filters,
    _escape_like,
    _normalize_directory_prefix,
)

pytestmark = pytest.mark.asyncio


# ── 捕获池：记录 conn.execute(sql, params) ─────────────────────────────────


class _CaptureCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.sql = ""
        self.params: list[Any] = []
        self._rows = rows or []

    async def execute(self, sql: str, params: Any) -> "_CaptureCursor":
        self.sql = " ".join(sql.split())
        self.params = list(params)
        return self

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    async def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _CaptureConnection:
    def __init__(self, cursor: _CaptureCursor) -> None:
        self._cursor = cursor

    async def execute(self, sql: str, params: Any) -> _CaptureCursor:
        return await self._cursor.execute(sql, params)


class _CapturePool:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.cursor = _CaptureCursor(rows)

    def connection(self) -> "_CapturePool":
        return self

    async def __aenter__(self) -> _CaptureConnection:
        return _CaptureConnection(self.cursor)

    async def __aexit__(self, *exc: object) -> None:
        return None


# ── 纯函数 ──────────────────────────────────────────────────────────────────


def test_escape_like_literals():
    assert _escape_like("a%b") == r"a\%b"
    assert _escape_like("a_b") == r"a\_b"
    assert _escape_like(r"a\b") == r"a\\b"
    assert _escape_like("手册") == "手册"


def test_normalize_directory_prefix_strips_slashes():
    assert _normalize_directory_prefix("/产品文档/手册/") == "产品文档/手册"
    assert _normalize_directory_prefix(None) == ""
    assert _normalize_directory_prefix(" / ") == ""


def test_search_filters_helper_shapes():
    sql, params = _document_search_filters(query="V2.3 手册", directory_prefix=None)
    assert sql == " AND d.document_name ILIKE %s"
    assert params == [r"%V2.3 手册%"]

    sql, params = _document_search_filters(query=None, directory_prefix="产品文档")
    assert sql == " AND (d.directory_path = %s OR d.directory_path LIKE %s)"
    assert params == ["产品文档", "产品文档/%"]

    # 组合 + 转义一起生效
    sql, params = _document_search_filters(query="50%", directory_prefix="/a_b/")
    assert params == [r"%50\%%", "a_b", r"a\_b/%"]

    sql, params = _document_search_filters(query=None, directory_prefix=None)
    assert (sql, params) == ("", [])


# ── KbDB SQL 形状 ───────────────────────────────────────────────────────────


async def test_list_documents_zero_filters_keeps_legacy_shape():
    """零过滤 = 浏览口径：不引入 ILIKE / 子查询 / ds.status。"""
    pool = _CapturePool()
    await KbDB(pool).list_documents_in_kb(kb_id="kb-1", limit=50, offset=0)
    assert "ILIKE" not in pool.cursor.sql
    assert "ds.status" not in pool.cursor.sql
    assert "ORDER BY d.created_at DESC LIMIT %s OFFSET %s" in pool.cursor.sql


async def test_list_documents_query_binding():
    pool = _CapturePool()
    await KbDB(pool).list_documents_in_kb(kb_id="kb-1", query="手册", limit=10)
    assert "d.document_name ILIKE %s" in pool.cursor.sql
    assert pool.cursor.params == ["kb-1", "%手册%", 10, 0]


async def test_list_documents_directory_prefix_recursive_boundary():
    pool = _CapturePool()
    await KbDB(pool).list_documents_in_kb(kb_id="kb-1", directory_prefix="产品文档")
    assert "(d.directory_path = %s OR d.directory_path LIKE %s)" in pool.cursor.sql
    assert pool.cursor.params == ["kb-1", "产品文档", "产品文档/%", 200, 0]


async def test_list_documents_status_filters_via_subquery():
    pool = _CapturePool()
    await KbDB(pool).list_documents_in_kb(kb_id="kb-1", status="failed", limit=5, offset=7)
    assert "SELECT * FROM (" in pool.cursor.sql
    assert "ds.status = %s" in pool.cursor.sql
    # 排序/分页移到外层
    assert "ORDER BY ds.created_at DESC LIMIT %s OFFSET %s" in pool.cursor.sql
    assert pool.cursor.params[-3:] == ["failed", 5, 7]


async def test_list_documents_combined_filters():
    pool = _CapturePool()
    await KbDB(pool).list_documents_in_kb(
        kb_id="kb-1", query="告警", directory_prefix="手册", status="mined")
    assert "ILIKE" in pool.cursor.sql and "ds.status = %s" in pool.cursor.sql
    assert pool.cursor.params == ["kb-1", "%告警%", "手册", "手册/%", "mined", 200, 0]


async def test_list_documents_unknown_status_rejected():
    with pytest.raises(ValueError):
        await KbDB(_CapturePool()).list_documents_in_kb(kb_id="kb-1", status="published")


async def test_count_documents_zero_filters_keeps_legacy_shape():
    pool = _CapturePool()
    await KbDB(pool).count_documents_in_kb(kb_id="kb-1")
    assert pool.cursor.sql == "SELECT COUNT(*) FROM asset_documents d WHERE d.kb_id = %s AND d.deleted_at IS NULL"
    assert pool.cursor.params == ["kb-1"]


async def test_count_documents_query_without_status_avoids_join():
    """无 status 的 count 不需要派生列——保持轻量（无 _STATUS_JOIN）。"""
    pool = _CapturePool()
    await KbDB(pool).count_documents_in_kb(kb_id="kb-1", query="手册")
    assert "ILIKE" in pool.cursor.sql
    assert "mining_run_documents" not in pool.cursor.sql


async def test_count_documents_status_uses_subquery():
    pool = _CapturePool()
    await KbDB(pool).count_documents_in_kb(kb_id="kb-1", status="uploaded")
    assert "SELECT COUNT(*) FROM (" in pool.cursor.sql
    assert "ds.status = %s" in pool.cursor.sql
    assert pool.cursor.params[-1] == "uploaded"
