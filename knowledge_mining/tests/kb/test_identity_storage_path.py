"""G1 — 身份/位置分离：按 storage_path 查身份，消解「多库同 document_key」歧义。

核心不变量：两个文档同 domain、同 document_key（同路径），但落在不同 KB（不同
storage_path）。挖掘 walk 时按 storage_path 查身份必须精确命中各自的那一行，不串库。
这是 G1 把 ``get_document_lifecycle_state`` / ``get_document_by_storage_path`` 的过滤
键从 ``document_key`` 改成 ``storage_path`` 的直接验证。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.infra.pg_config import MiningDbConfig
from knowledge_mining.mining.infra.db import AssetCoreDB

DOMAIN = "cloud_core_network"


@pytest.fixture
def asset_db():
    cfg = MiningDbConfig()
    db = AssetCoreDB.from_conninfo(cfg.conninfo, pool_min=1, pool_max=2)
    db.open()  # from_conninfo 建池时 open=False，须显式 open
    yield db
    db.close()


def _insert_doc(asset_db, *, did, document_key, storage_path, document_name="qos.pdf"):
    """插一行 asset_documents（kb_id/owner_id 留空，避免 FK 设置；storage_path 各异）。"""
    asset_db._execute(
        """INSERT INTO asset_documents
             (id, domain, document_key, document_name, metadata_json, created_at,
              kb_id, storage_path, directory_path, owner_id)
           VALUES (%s, %s, %s, %s, '{}', NOW(), NULL, %s, '', NULL)""",
        (did, DOMAIN, document_key, document_name, storage_path),
    )


def _cleanup(asset_db, ids):
    asset_db._execute(
        "DELETE FROM asset_documents WHERE id = ANY(%s)", (list(ids),)
    )


def test_get_document_by_storage_path_is_kb_scoped(asset_db):
    """同 domain + 同 document_key + 不同 storage_path → 按 storage_path 精确命中。"""
    _cleanup(asset_db, {"g1_kb1", "g1_kb2"})  # 清上轮可能残留
    _insert_doc(asset_db, did="g1_kb1", document_key="doc:/qos.pdf",
                storage_path="/data/uploads/KB1/qos.pdf")
    _insert_doc(asset_db, did="g1_kb2", document_key="doc:/qos.pdf",
                storage_path="/data/uploads/KB2/qos.pdf")
    try:
        hit1 = asset_db.get_document_by_storage_path(
            domain=DOMAIN, storage_path="/data/uploads/KB1/qos.pdf")
        hit2 = asset_db.get_document_by_storage_path(
            domain=DOMAIN, storage_path="/data/uploads/KB2/qos.pdf")
        assert hit1 is not None and hit1["id"] == "g1_kb1"
        assert hit2 is not None and hit2["id"] == "g1_kb2"
        # 按 document_key（旧方式）查会歧义：两行同 key，只能取其一
        by_key = asset_db.get_document_by_key(domain=DOMAIN, document_key="doc:/qos.pdf")
        assert by_key["id"] in {"g1_kb1", "g1_kb2"}  # 旧方式无法区分 → 这正是 G1 要修的
    finally:
        _cleanup(asset_db, {"g1_kb1", "g1_kb2"})


def test_lifecycle_state_scoped_by_storage_path(asset_db):
    """get_document_lifecycle_state 按 storage_path 隔离身份（无发布时 history/active 为 NULL）。"""
    _cleanup(asset_db, {"ls_kb1", "ls_kb2"})  # 清上轮可能残留
    _insert_doc(asset_db, did="ls_kb1", document_key="doc:/spec.md",
                storage_path="/data/uploads/KB1/spec.md", document_name="spec.md")
    _insert_doc(asset_db, did="ls_kb2", document_key="doc:/spec.md",
                storage_path="/data/uploads/KB2/spec.md", document_name="spec.md")
    try:
        ls1 = asset_db.get_document_lifecycle_state(
            domain=DOMAIN, channel="prod",
            storage_path="/data/uploads/KB1/spec.md", normalized_content_hash="h")
        ls2 = asset_db.get_document_lifecycle_state(
            domain=DOMAIN, channel="prod",
            storage_path="/data/uploads/KB2/spec.md", normalized_content_hash="h")
        assert ls1 is not None and ls1["document_id"] == "ls_kb1"
        assert ls2 is not None and ls2["document_id"] == "ls_kb2"
        # 冻结 document_key 相同（同路径），但身份不同 → mining_run_documents 各自记录，
        # derive_document_status 的 join 不会把 KB2 的 run 挂到 KB1 的文档上。
        assert ls1["document_key"] == ls2["document_key"] == "doc:/spec.md"
    finally:
        _cleanup(asset_db, {"ls_kb1", "ls_kb2"})


def test_storage_path_miss_returns_none(asset_db):
    """storage_path 查不到（含 legacy NULL storage_path）→ None，不回落 document_key。"""
    miss = asset_db.get_document_by_storage_path(
        domain=DOMAIN, storage_path="/nonexistent/path.pdf")
    assert miss is None
    ls = asset_db.get_document_lifecycle_state(
        domain=DOMAIN, channel="prod",
        storage_path="/nonexistent/path.pdf", normalized_content_hash="h")
    assert ls is None
