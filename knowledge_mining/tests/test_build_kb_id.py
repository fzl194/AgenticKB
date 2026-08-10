"""KB 挖掘 build 必须写 kb_id，否则 get_document_knowledge（WHERE kb_id=%s）读不到 →
前端「看不到挖掘后的知识」。验证 insert_build 的 kb_id 参数落库。"""
from __future__ import annotations

import pytest


def test_insert_build_writes_kb_id(asset_db):
    build_id = asset_db.insert_build(
        "b-kb-1", "B-KB-1", "validated", "full", domain="cloud_core_network", kb_id="kb-123"
    )
    row = asset_db.get_build(build_id)
    assert row is not None
    assert row["kb_id"] == "kb-123"


def test_insert_build_kb_id_defaults_null(asset_db):
    """域级 run（无 kb_id）调用时默认 NULL，行为不变。"""
    build_id = asset_db.insert_build(
        "b-kb-2", "B-KB-2", "validated", "full", domain="cloud_core_network"
    )
    row = asset_db.get_build(build_id)
    assert row is not None
    assert row["kb_id"] is None


def test_publish_release_rejects_kb_scoped_build(asset_db):
    """H5：KB-scoped build 不得进域级 active release（会 retire 同域其它 KB 的 release）。

    护栏在 publish_release 发布边界强制，不依赖入口 metadata.publish=false 约定。
    """
    from knowledge_mining.mining.stages.publishing import publish_release

    asset_db.insert_build(
        "b-kb-pub", "B-KB-PUB", "validated", "full",
        domain="cloud_core_network", kb_id="kb-999",
    )
    with pytest.raises(ValueError, match="KB-scoped"):
        publish_release(
            asset_db, "b-kb-pub",
            domain="cloud_core_network", channel="prod",
        )
