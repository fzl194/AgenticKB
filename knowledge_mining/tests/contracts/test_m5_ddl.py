"""011 DDL 契约测试（M5.3）：切片落库增列 + element links 表.

全链 SQLite 加载（001+008+009+010+011），断言：
1. ``asset_raw_segments.compiler_fingerprint`` 列存在；
2. 元素定位仅保留在 ``source_offsets_json``，不再创建重复 link 表。
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


def test_compiler_fingerprint_is_retained_without_link_table() -> None:
    conn = _load_full_sqlite_chain("011_m5_segment_links.sql")
    seg_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(asset_raw_segments)")
    }
    assert "compiler_fingerprint" in seg_cols

    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='asset_segment_element_links'"
    ).fetchone()
    assert row is None
