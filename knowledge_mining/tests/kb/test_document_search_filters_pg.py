"""57 号文件搜索过滤——真实行为用例（需要 PostgreSQL `_test` 可丢弃库）。

覆盖：中文子串、大小写、转义字面量、目录递归边界（产品 vs 产品文档）、
status 派生过滤、跨库合并与 422 矩阵（mcp-tools 路由）。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.storage import build_document_key

pytestmark = pytest.mark.asyncio


async def _mk_doc(
    db: KbDB, kb_id: str, name: str, *, directory: str = "", owner_id: str = "",
) -> str:
    doc = await db.insert_document_identity(
        domain="generic", kb_id=kb_id,
        document_key=build_document_key(directory, name), document_name=name,
        storage_path=f"/tmp/{kb_id}/{name}", directory_path=directory,
        owner_id=owner_id or None,
    )
    return doc["id"]


async def test_search_by_name_substring_and_case(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("fs_case")
    kb = await db.create_kb(domain="generic", name="fs-case", owner_id=owner["id"])
    await _mk_doc(db, kb["id"], "设备X手册.pdf")
    await _mk_doc(db, kb["id"], "Release-Notes.txt")

    hits = await db.list_documents_in_kb(kb_id=kb["id"], query="手册")
    assert [d["document_name"] for d in hits] == ["设备X手册.pdf"]
    # 大小写不敏感（ILIKE）
    hits = await db.list_documents_in_kb(kb_id=kb["id"], query="release")
    assert [d["document_name"] for d in hits] == ["Release-Notes.txt"]
    # like 通配符按字面匹配：'%(' 不当通配符
    await _mk_doc(db, kb["id"], "50%(special).md")
    hits = await db.list_documents_in_kb(kb_id=kb["id"], query="%(special)")
    assert [d["document_name"] for d in hits] == ["50%(special).md"]


async def test_search_directory_prefix_recursive_boundary(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("fs_prefix")
    kb = await db.create_kb(domain="generic", name="fs-prefix", owner_id=owner["id"])
    await _mk_doc(db, kb["id"], "root.pdf")                       # 根
    await _mk_doc(db, kb["id"], "a.pdf", directory="产品文档")
    await _mk_doc(db, kb["id"], "b.pdf", directory="产品文档/手册")
    await _mk_doc(db, kb["id"], "c.pdf", directory="产品文档2")     # 同前缀兄弟目录

    names = lambda docs: sorted(d["document_name"] for d in docs)
    assert names(await db.list_documents_in_kb(kb_id=kb["id"], directory_prefix="产品文档")) \
        == ["a.pdf", "b.pdf"]
    assert names(await db.list_documents_in_kb(kb_id=kb["id"], directory_prefix="产品文档/手册")) \
        == ["b.pdf"]
    # 空前缀（规范化后）= 不过滤
    assert len(await db.list_documents_in_kb(kb_id=kb["id"], directory_prefix="/")) == 4


async def test_search_status_filter(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("fs_status")
    kb = await db.create_kb(domain="generic", name="fs-status", owner_id=owner["id"])
    await _mk_doc(db, kb["id"], "fresh.md")  # 无 run 记录 → uploaded

    hits = await db.list_documents_in_kb(kb_id=kb["id"], status="uploaded")
    assert [d["document_name"] for d in hits] == ["fresh.md"]
    hits = await db.list_documents_in_kb(kb_id=kb["id"], status="mined")
    assert hits == []
    assert await db.count_documents_in_kb(kb_id=kb["id"], status="uploaded") == 1
    assert await db.count_documents_in_kb(kb_id=kb["id"]) == 1
