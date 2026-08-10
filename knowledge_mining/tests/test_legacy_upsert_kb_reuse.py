"""H7 — legacy upsert_document_identity 命中 KB 拥有行时复用，不新建 kb_id=NULL 分叉。

(domain, document_key) 唯一约束让位于 (kb_id, document_key) 后能容许多个 NULL kb_id，
legacy 路径若为同物理文件新建 NULL 行会与 KB 拥有行分叉。修复后 legacy 复用已存在的
KB 行（不改其元数据），消除孤儿。
"""
from __future__ import annotations


def test_legacy_upsert_reuses_kb_owned_row_no_divergence(asset_db):
    # 建满足 FK 的 KB 拥有文档：kb_users → knowledge_bases → asset_documents
    asset_db._execute(
        "INSERT INTO kb_users (id, username, created_at) VALUES (%s, %s, %s)",
        ("u-1", "owner1", "2026-01-01T00:00:00+00:00"),
    )
    asset_db._execute(
        """INSERT INTO knowledge_bases (id, domain, name, owner_id, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        ("kb-1", "cloud_core_network", "t-kb", "u-1",
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    asset_db._execute(
        """INSERT INTO asset_documents
               (id, domain, document_key, kb_id, document_name, storage_path, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (
            "doc-kb-1", "cloud_core_network", "doc:/sub/a.txt", "kb-1",
            "kb-owned", "/uploads/kb-1/sub/a.txt", "2026-01-01T00:00:00+00:00",
        ),
    )

    # legacy /api/runs 处理同 domain + document_key 的文件
    returned = asset_db.upsert_document(
        domain="cloud_core_network",
        document_id="doc-legacy-1",
        document_key="doc:/sub/a.txt",
        document_name="legacy-name",
    )

    # 复用了 KB 行的 id，没有新建 NULL 分叉
    assert returned == "doc-kb-1"
    rows = asset_db._fetchall(
        "SELECT id, kb_id, document_name FROM asset_documents "
        "WHERE domain = %s AND document_key = %s",
        ("cloud_core_network", "doc:/sub/a.txt"),
    )
    assert len(rows) == 1, f"expected 1 row, got {len(rows)} (NULL 孤儿?)"
    assert rows[0]["id"] == "doc-kb-1"
    assert rows[0]["kb_id"] == "kb-1"          # KB 拥有身份未变
    assert rows[0]["document_name"] == "kb-owned"  # legacy 没覆盖 KB 元数据


def test_legacy_upsert_updates_existing_null_row(asset_db):
    """纯 legacy 行（kb_id NULL）仍按原逻辑更新元数据。"""
    asset_db._execute(
        """INSERT INTO asset_documents (id, domain, document_key, kb_id, document_name, created_at)
           VALUES (%s, %s, %s, NULL, %s, %s)""",
        ("doc-leg-1", "cloud_core_network", "doc:/x.txt", "old", "2026-01-01T00:00:00+00:00"),
    )
    returned = asset_db.upsert_document(
        domain="cloud_core_network",
        document_id="whatever",
        document_key="doc:/x.txt",
        document_name="new",
    )
    assert returned == "doc-leg-1"
    row = asset_db.get_document_by_key(domain="cloud_core_network", document_key="doc:/x.txt")
    assert row["document_name"] == "new"  # legacy 行元数据被更新
