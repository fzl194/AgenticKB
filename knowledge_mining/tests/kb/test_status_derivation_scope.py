"""派生文档状态的归属边界（缺陷 D9）。

`_STATUS_JOIN_SQL` 原本只按 `r.document_key = d.document_key` 关联 mining_run_documents。
但 `build_document_key()` 产的是 `doc:/{相对路径}`、**不含 kb_id**——全局唯一的是
`storage_path`。而 `004_kb_isolation.sql` 恰恰**故意**删掉了 `(domain, document_key)`
唯一约束以允许「同域多库各放一份 qos.pdf」，所以同 key 跨库共存是设计支持的配置，
不是边缘情况。结果：A 库挖失败，B 库那篇同名文档也显示 failed。

修复是在 LATERAL 里经 run 补上归属维度（kb_id + domain）。这几条钉住它。

⚠️ 需要 PostgreSQL（`_test` 结尾的可丢弃库），与 tests/kb 下其余用例一致。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb import db as kb_db_module
from knowledge_mining.mining.kb.db import KbDB

pytestmark = pytest.mark.asyncio


async def test_status_join_constrains_run_ownership():
    """不需要数据库的形状守卫：归属维度被"简化"掉时立刻红。

    下面几条真正的行为用例要 PostgreSQL 才能跑，CI 之外的开发机常常跑不到；
    这条至少保证「LATERAL 退回只按 document_key 关联」不会静悄悄溜过去。
    """
    fragment = " ".join(kb_db_module._STATUS_JOIN_SQL.split())

    assert "JOIN mining_runs mr ON mr.id = r.run_id" in fragment
    # kb 维度：必须是 IS NOT DISTINCT FROM，否则 legacy 文档（两侧 NULL）全部失配
    assert "mr.kb_id IS NOT DISTINCT FROM d.kb_id" in fragment
    assert "mr.domain = d.domain" in fragment

DOMAIN = "cloud_core_network"
STARTED = "2026-01-01T00:00:00+00:00"
FINISHED = "2026-01-01T00:00:01+00:00"


async def _insert_run(pool, *, run_id: str, kb_id: str | None, domain: str = DOMAIN):
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO mining_runs
               (id, kb_id, input_path, domain, channel, status, current_stage, started_at,
                execution_engine, workflow_manifest_json, metadata_json,
                total_documents, new_count, updated_count, skipped_count,
                failed_count, committed_count)
               VALUES (%s, %s, %s, %s, 'prod', 'completed', 'done', %s,
                       'workflow', '{}'::jsonb, '{}'::jsonb, 1, 1, 0, 0, 0, 1)""",
            (run_id, kb_id, f"/tmp/{kb_id or 'legacy'}", domain, STARTED),
        )


async def _insert_run_document(pool, *, rd_id: str, run_id: str, document_key: str, status: str):
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO mining_run_documents
               (id, run_id, document_key, raw_content_hash, normalized_content_hash,
                action, status, started_at, finished_at)
               VALUES (%s, %s, %s, 'h', 'h', 'NEW', %s, %s, %s)""",
            (rd_id, run_id, document_key, status, STARTED, FINISHED),
        )


async def _insert_legacy_document(pool, *, doc_id: str, document_key: str, domain: str = DOMAIN):
    """legacy 域级文档：kb_id 为 NULL，不能走 insert_document_identity（它必带 kb_id）。"""
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO asset_documents
               (id, domain, document_key, document_name, metadata_json, created_at, kb_id)
               VALUES (%s, %s, %s, %s, '{}', %s, NULL)""",
            (doc_id, domain, document_key, document_key.rsplit("/", 1)[-1], STARTED),
        )


async def _status(db: KbDB, doc_id: str) -> str:
    return await db.derive_document_status(doc_id)


async def test_failed_run_in_one_kb_does_not_bleed_into_another(async_pool):
    """同域两个库各有一份 doc:/spec.pdf；A 库挖失败不得把 B 库那篇也染成 failed。"""
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb_a = await db.create_kb(domain=DOMAIN, name="KB-A", owner_id=owner["id"])
    kb_b = await db.create_kb(domain=DOMAIN, name="KB-B", owner_id=owner["id"])

    key = "doc:/spec.pdf"
    doc_a = await db.insert_document_identity(
        domain=DOMAIN, kb_id=kb_a["id"], document_key=key, document_name="spec.pdf",
        storage_path=f"/tmp/{kb_a['id']}/spec.pdf", directory_path="",
    )
    doc_b = await db.insert_document_identity(
        domain=DOMAIN, kb_id=kb_b["id"], document_key=key, document_name="spec.pdf",
        storage_path=f"/tmp/{kb_b['id']}/spec.pdf", directory_path="",
    )

    await _insert_run(async_pool, run_id="run-a", kb_id=kb_a["id"])
    await _insert_run_document(
        async_pool, rd_id="rd-a", run_id="run-a", document_key=key, status="failed",
    )

    assert await _status(db, doc_a["id"]) == "failed"
    # 修复前这里是 'failed'——B 库的编辑者会看到一篇从没失败过的文档报错
    assert await _status(db, doc_b["id"]) == "uploaded"

    # 列表路径与单点派生必须一致（两者共用同一段 SQL 片段）
    rows_b = {r["id"]: r["status"] for r in await db.list_documents_in_kb(kb_id=kb_b["id"])}
    assert rows_b[doc_b["id"]] == "uploaded"


async def test_each_kb_reflects_its_own_run(async_pool):
    """两边都挖过时各认各的：A 失败、B 成功，互不覆盖。"""
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb_a = await db.create_kb(domain=DOMAIN, name="KB-A", owner_id=owner["id"])
    kb_b = await db.create_kb(domain=DOMAIN, name="KB-B", owner_id=owner["id"])

    key = "doc:/spec.pdf"
    doc_a = await db.insert_document_identity(
        domain=DOMAIN, kb_id=kb_a["id"], document_key=key, document_name="spec.pdf",
        storage_path=f"/tmp/{kb_a['id']}/spec.pdf", directory_path="",
    )
    doc_b = await db.insert_document_identity(
        domain=DOMAIN, kb_id=kb_b["id"], document_key=key, document_name="spec.pdf",
        storage_path=f"/tmp/{kb_b['id']}/spec.pdf", directory_path="",
    )

    await _insert_run(async_pool, run_id="run-a", kb_id=kb_a["id"])
    await _insert_run_document(
        async_pool, rd_id="rd-a", run_id="run-a", document_key=key, status="failed",
    )
    await _insert_run(async_pool, run_id="run-b", kb_id=kb_b["id"])
    await _insert_run_document(
        async_pool, rd_id="rd-b", run_id="run-b", document_key=key, status="committed",
    )

    assert await _status(db, doc_a["id"]) == "failed"
    assert await _status(db, doc_b["id"]) == "mined"


async def test_legacy_document_still_resolves_from_legacy_run(async_pool):
    """kb_id 两侧都为 NULL 时必须仍然匹配——所以用 IS NOT DISTINCT FROM 而不是 `=`。

    普通等值比较下 NULL = NULL 为 UNKNOWN，legacy 文档会整体退回 'uploaded'。
    """
    db = KbDB(async_pool)
    key = "doc:/legacy.md"
    await _insert_legacy_document(async_pool, doc_id="legacy-doc-1", document_key=key)
    await _insert_run(async_pool, run_id="run-legacy", kb_id=None)
    await _insert_run_document(
        async_pool, rd_id="rd-legacy", run_id="run-legacy", document_key=key,
        status="committed",
    )

    assert await _status(db, "legacy-doc-1") == "mined"


async def test_kb_document_is_not_claimed_by_legacy_run(async_pool):
    """legacy 域级 run 不该给 KB 文档定状态：它的 document_key 相对的是另一个输入根，
    对上纯属巧合。KB 文档的状态由该库自己的挖掘决定。"""
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("alice")
    kb = await db.create_kb(domain=DOMAIN, name="KB-A", owner_id=owner["id"])
    key = "doc:/spec.pdf"
    doc = await db.insert_document_identity(
        domain=DOMAIN, kb_id=kb["id"], document_key=key, document_name="spec.pdf",
        storage_path=f"/tmp/{kb['id']}/spec.pdf", directory_path="",
    )

    await _insert_run(async_pool, run_id="run-legacy", kb_id=None)
    await _insert_run_document(
        async_pool, rd_id="rd-legacy", run_id="run-legacy", document_key=key,
        status="committed",
    )

    assert await _status(db, doc["id"]) == "uploaded"


async def test_status_does_not_cross_domains(async_pool):
    """同一 legacy document_key 在两个域各挖一次时也不串——kb_id 两边都是 NULL，
    只剩 domain 能区分。"""
    db = KbDB(async_pool)
    key = "doc:/shared.md"
    await _insert_legacy_document(
        async_pool, doc_id="doc-dom-a", document_key=key, domain=DOMAIN,
    )
    # 另一个域挖了同名文件并失败
    await _insert_run(async_pool, run_id="run-other", kb_id=None, domain="generic")
    await _insert_run_document(
        async_pool, rd_id="rd-other", run_id="run-other", document_key=key, status="failed",
    )

    assert await _status(db, "doc-dom-a") == "uploaded"
