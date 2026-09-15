# -*- coding: utf-8 -*-
"""真实 PG 端到端回归（默认跳过；ONENET_E2E_PG_DSN 指向一次性测试库才跑）.

背景（2026-09-15 内网事故）：快照表 mime CHECK 白名单漏了
``application/x-onenet+jsonl``，全部挖掘在 snapshot commit 阶段 CheckViolation
失败——fake 注入的单测拦不住 DDL 与代码的漂移，本文件把生产 DDL 链原样铺到
真实 PG 上验证。

运行（一次性容器）::

    docker run --rm -d --name onenet-e2e-pg -e POSTGRES_PASSWORD=e2e \
        -p 15433:5432 postgres:16
    # 等就绪后建库：
    docker exec onenet-e2e-pg createdb -U postgres onenet_e2e
    ONENET_E2E_PG_DSN="host=localhost port=15433 dbname=onenet_e2e \
        user=postgres password=e2e" \
        python -m pytest knowledge_mining/tests/onenet/test_e2e_real_pg.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DSN = os.environ.get("ONENET_E2E_PG_DSN", "")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="需要 ONENET_E2E_PG_DSN（一次性真实 PG）"),
]


_DDL_APPLIED = False


async def _apply_production_ddl() -> None:
    """把生产 DDL 链（pg_schema 同款清单）原样铺进测试库（会话级一次：
    个别 trigger 语句非幂等，重铺会 DuplicateObject）。"""
    global _DDL_APPLIED
    if _DDL_APPLIED:
        return
    from knowledge_mining.mining.infra.pg_schema import domain_schema_paths

    import psycopg

    async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as conn:
        for ddl_path in domain_schema_paths():
            await conn.execute(ddl_path.read_text(encoding="utf-8"))
    _DDL_APPLIED = True


async def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def test_snapshot_mime_whitelist_accepts_onenet():
    """内网事故直接回归：onenet MIME 必须能过 ck_asset_snapshot_mime_type."""
    import psycopg
    from psycopg.errors import CheckViolation

    await _apply_production_ddl()
    async with await psycopg.AsyncConnection.connect(DSN) as conn:
        base = {
            "domain": "IP",
            "normalized_content_hash": "h1" * 32,
            "raw_content_hash": "h1" * 32,
            "created_at": await _now(),
        }
        # ① onenet MIME → 必须成功（事故现场：CheckViolation）
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO asset_document_snapshots
                     (id, domain, normalized_content_hash, raw_content_hash,
                      mime_type, created_at)
                   VALUES (%(id)s, %(domain)s, %(normalized_content_hash)s,
                           %(raw_content_hash)s,
                           'application/x-onenet+jsonl', %(created_at)s)""",
                {"id": "snap-onenet-ok", **base})
        # ② 白名单语义仍在：乱 MIME 仍拒绝
        with pytest.raises(CheckViolation):
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO asset_document_snapshots
                         (id, domain, normalized_content_hash, raw_content_hash,
                          mime_type, created_at)
                       VALUES (%(id)s, %(domain)s, %(normalized_content_hash)s,
                               %(raw_content_hash)s,
                               'evil/not-a-mime', %(created_at)s)""",
                    {"id": "snap-evil", **base})
        await conn.rollback()


async def test_delete_folder_subtree_real_sql():
    """source 级删除的目录清理 SQL 真库验证：活文档清零才整树删."""
    from knowledge_mining.mining.kb.db import KbDB

    await _apply_production_ddl()

    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    # 对齐生产池：dict_row 行工厂（KbDB 按列名取值）
    pool = AsyncConnectionPool(DSN, open=False, kwargs={"row_factory": dict_row})
    await pool.open()
    try:
        kb_id, top = "kb-e2e", "UDG产品文档20.18.0 [DOC1100938722]"
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO kb_users (id, username, site_role, created_at)
                   VALUES ('u-e2e', 'e2e-admin', 'admin', %s)""",
                [await _now()])
            await conn.execute(
                """INSERT INTO knowledge_bases (id, domain, name, owner_id,
                     visibility, status, created_at, updated_at)
                   VALUES (%s, 'IP', '一张网产品文档', 'u-e2e',
                           'private', 'active', %s, %s)""",
                [kb_id, await _now(), await _now()])
            # 目录树：顶层 + 章节 + 深层（+ 一个不属于本 source 的兄弟顶层）
            for i, path in enumerate([
                    top, f"{top}/02 特性配置", f"{top}/02 特性配置/QoS",
                    "别的文档 [DOC9999999999]"]):
                await conn.execute(
                    """INSERT INTO kb_folders (id, kb_id, name, path, created_at)
                       VALUES (%s, %s, %s, %s, %s)""",
                    [f"f{i}", kb_id, path.split("/")[-1], path, await _now()])
            # 两篇文档：一篇软删（本 source 删除后的形态）、一篇顶层外
            await conn.execute(
                """INSERT INTO asset_documents
                     (id, domain, kb_id, document_key, document_name,
                      directory_path, created_at)
                   VALUES ('d1', 'IP', %s, 'onenet:DOC1100938722:x1',
                           'QoS.jsonl', %s, %s)""",
                [kb_id, f"{top}/02 特性配置", await _now()])
            await conn.execute(
                "UPDATE asset_documents SET deleted_at = %s WHERE id = 'd1'",
                [await _now()])

        kbdb = KbDB(pool)
        # 活文档已清零（count_docs_under_path 过滤软删）→ 整树删除本 source 目录
        assert await kbdb.count_docs_under_path(
            kb_id=kb_id, path=top) == 0
        removed = await kbdb.delete_folder_subtree(kb_id, top)
        assert removed == 3                       # 顶层 + 两层章节，兄弟不动
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT path FROM kb_folders WHERE kb_id = %s ORDER BY path",
                [kb_id])
            left = [r["path"] for r in await cur.fetchall()]
        assert left == ["别的文档 [DOC9999999999]"]
    finally:
        await pool.close()
