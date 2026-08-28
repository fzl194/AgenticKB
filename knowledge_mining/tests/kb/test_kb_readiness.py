"""批次4：建库默认范式 + readiness 四档派生。"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.db import (
    READINESS_LEVELS,
    KbDB,
    derive_readiness_level,
)

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------- 纯函数（本地可跑）

def test_readiness_ladder_order() -> None:
    assert READINESS_LEVELS == (
        "empty", "parsed", "segmented", "lexical_ready", "vector_ready",
    )


def test_readiness_ladder_progression() -> None:
    kw = dict(documents=0, segments=0, retrieval_units=0, embeddings=0)
    assert derive_readiness_level(**kw) == "empty"

    assert derive_readiness_level(
        documents=2, segments=0, retrieval_units=0, embeddings=0,
    ) == "parsed"
    assert derive_readiness_level(
        documents=2, segments=7, retrieval_units=0, embeddings=0,
    ) == "segmented"
    assert derive_readiness_level(
        documents=2, segments=7, retrieval_units=30, embeddings=0,
    ) == "lexical_ready"
    assert derive_readiness_level(
        documents=2, segments=7, retrieval_units=30, embeddings=29,
    ) == "vector_ready"


def test_readiness_embedding_fallback_caps_at_lexical_ready() -> None:
    """嵌入降级已留痕 → 语义检索缺失但可见，最高 lexical_ready。"""
    assert derive_readiness_level(
        documents=1, segments=5, retrieval_units=9, embeddings=9,
        embedding_fallback=True,
    ) == "lexical_ready"
    assert derive_readiness_level(
        documents=1, segments=5, retrieval_units=9, embeddings=0,
        embedding_fallback=True,
    ) == "lexical_ready"


def test_readiness_missing_units_blocks_lexical() -> None:
    """units=0（批次4 修复前的病态形态）→ 停在 segmented，不得称可检索。"""
    assert derive_readiness_level(
        documents=1, segments=5, retrieval_units=0, embeddings=3,
    ) == "segmented"


# ---------------------------------------------------------- PG 集成（async_pool）

async def test_create_kb_defaults_to_full_baseline(async_pool):
    """建库默认范式 system-full-baseline（2026-08-27 决策），可 PATCH 后改。"""
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("readiness-owner")
    kb = await db.create_kb(
        domain="cloud_core_network", name="readiness-default",
        owner_id=owner["id"],
    )
    assert kb["mining_workflow_id"] == "system-full-baseline"
    fetched = await db.get_kb(kb["id"])
    assert fetched is not None
    assert fetched["mining_workflow_id"] == "system-full-baseline"


async def test_create_kb_honors_explicit_workflow_override(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("readiness-owner")
    kb = await db.create_kb(
        domain="cloud_core_network", name="readiness-override",
        owner_id=owner["id"],
        metadata={"mining_workflow_id": "some-other-paradigm"},
    )
    assert kb["mining_workflow_id"] == "some-other-paradigm"


async def test_get_kb_readiness_empty_kb(async_pool):
    db = KbDB(async_pool)
    owner = await db.upsert_user_by_username("readiness-owner")
    kb = await db.create_kb(
        domain="cloud_core_network", name="readiness-empty",
        owner_id=owner["id"],
    )
    readiness = await db.get_kb_readiness(kb["id"])
    assert readiness["level"] == "empty"
    assert readiness["documents"] == 0
    assert readiness["embedding_fallback"] is False
