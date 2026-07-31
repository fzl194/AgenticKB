"""KB 挖掘 build 必须写 kb_id，否则 get_document_knowledge（WHERE kb_id=%s）读不到 →
前端「看不到挖掘后的知识」。验证 insert_build 的 kb_id 参数落库。"""
from __future__ import annotations


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
