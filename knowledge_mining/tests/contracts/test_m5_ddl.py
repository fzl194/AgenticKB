"""011 DDL 契约测试（M5.3）：切片落库增列 + element links 表.

全链 SQLite 加载（001+008+009+010+011），断言：
1. ``asset_raw_segments.compiler_fingerprint`` 列存在；
2. ``asset_segment_element_links`` 表形状与索引齐全。
"""
from __future__ import annotations

from pathlib import Path

from tests.contracts.test_m4_ddl import _load_full_sqlite_chain

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_DIR = _REPO_ROOT / "databases" / "asset_core" / "schemas"


def test_011_files_exist_both_dialects() -> None:
    for name in (
        "011_m5_segment_links.sql",
        "011_m5_segment_links_postgresql.sql",
    ):
        assert (_SCHEMA_DIR / name).exists(), name


def test_segment_links_table_and_column() -> None:
    conn = _load_full_sqlite_chain("011_m5_segment_links.sql")
    seg_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(asset_raw_segments)")
    }
    assert "compiler_fingerprint" in seg_cols

    cols = {
        row[1] for row in
        conn.execute("PRAGMA table_info(asset_segment_element_links)")
    }
    assert {
        "id", "document_snapshot_id", "segment_index", "element_id",
        "evidence_span_ids", "char_start", "char_end", "metadata_json",
    } <= cols
    # FK（对抗评审 MEDIUM-8）生效：先插父快照行。
    conn.execute(
        "INSERT INTO asset_document_snapshots (id, domain,"
        " normalized_content_hash, raw_content_hash, mime_type, created_at)"
        " VALUES ('snap1', 'd', 'nh', 'rh', 'other', 't')"
    )
    conn.execute(
        "INSERT INTO asset_segment_element_links (id, document_snapshot_id,"
        " segment_index, element_id, evidence_span_ids)"
        " VALUES ('l1', 'snap1', 0, 'e1', '[\"s1\",\"s2\"]')"
    )
    rows = list(conn.execute(
        "SELECT element_id FROM asset_segment_element_links"
        " WHERE document_snapshot_id = 'snap1'"
    ))
    assert rows == [("e1",)]
