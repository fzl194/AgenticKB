"""P1.3 — 验证 kb DDL 被 pg_schema 加载（需 PG，库名须以 _test 结尾）。

依赖 conftest 的 db_config + _ensure_schema（后者调 ensure_schema 把所有 DDL 按序应用）。
"""
from __future__ import annotations

import psycopg
import pytest


def _connect(db_config):
    return psycopg.connect(db_config.conninfo, autocommit=True)


def test_kb_tables_exist(db_config, _ensure_schema):
    """kb_users / knowledge_bases / kb_members 三张表都建出来了。"""
    with _connect(db_config) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('kb_users'), to_regclass('knowledge_bases'), to_regclass('kb_members')"
            )
            assert cur.fetchone() == ("kb_users", "knowledge_bases", "kb_members")


def test_asset_documents_has_kb_columns(db_config, _ensure_schema):
    """asset_documents 加了 kb_id / storage_path / directory_path / owner_id 四列。"""
    with _connect(db_config) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_name = 'asset_documents'
                     AND column_name IN ('kb_id', 'storage_path', 'directory_path', 'owner_id')"""
            )
            names = {r[0] for r in cur.fetchall()}
            assert names == {"kb_id", "storage_path", "directory_path", "owner_id"}


def test_asset_documents_unique_is_kb_scoped(db_config, _ensure_schema):
    """UNIQUE 约束从 (domain, document_key) 改成了 (kb_id, document_key)。"""
    with _connect(db_config) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT conname FROM pg_constraint
                   WHERE conrelid = 'asset_documents'::regclass AND contype = 'u'"""
            )
            uniq_names = {r[0] for r in cur.fetchall()}
            assert "uq_asset_documents_kb_key" in uniq_names
            # 旧约束应已删除（002 内联自动名 + 003 命名约束两个名字）
            assert "asset_documents_domain_document_key_key" not in uniq_names
            assert "uq_asset_documents_domain_document_key" not in uniq_names


def test_knowledge_bases_soft_delete_columns(db_config, _ensure_schema):
    """knowledge_bases 有软删字段 status('active','deleted') + deleted_at。"""
    with _connect(db_config) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_name = 'knowledge_bases' AND column_name IN ('status', 'deleted_at')"""
            )
            names = {r[0] for r in cur.fetchall()}
            assert names == {"status", "deleted_at"}


def test_visibility_check_is_private_public(db_config, _ensure_schema):
    """007 收口:visibility 命名 CHECK 存在且仅允许 private/public;shared 插入被拒。"""
    with _connect(db_config) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'knowledge_bases'::regclass
                     AND contype = 'c'
                     AND conname = 'knowledge_bases_visibility_check'"""
            )
            assert cur.fetchone() is not None
            # 准备 owner 满足 FK
            cur.execute(
                """INSERT INTO kb_users (id, username, created_at)
                   VALUES ('u-vis-test', 'u-vis-test', '2026-01-01T00:00:00+00:00')
                   ON CONFLICT (username) DO NOTHING"""
            )
            cur.execute("DELETE FROM knowledge_bases WHERE name = '__vis_test__'")
            # shared 应被新 CHECK 拒绝
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(
                    """INSERT INTO knowledge_bases
                       (id, domain, name, owner_id, visibility, status, created_at, updated_at)
                       VALUES ('kb-vis-test', 'cloud_core_network', '__vis_test__',
                               'u-vis-test', 'shared', 'active',
                               '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"""
                )
