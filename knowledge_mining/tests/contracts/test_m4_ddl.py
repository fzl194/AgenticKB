"""010 DDL 契约测试（M4.1/M4.3）：Parse Run 状态机扩列 + attempt 事件表.

SQLite 内存库加载 009 + 010，断言：

1. ``asset_parse_runs.status`` CHECK 覆盖完整状态机（12 态 + SUPERSEDED，
   与 ``contracts/state_machines.py`` 单一事实源对齐）；
2. 新表 ``asset_parse_run_attempts`` 存在，含
   ``UNIQUE(parse_run_id, attempt_index)`` 与 attempt_kind/outcome CHECK；
3. 非法状态被数据库层拒绝（双保险：应用层 state_machines + DB CHECK）。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from knowledge_mining.mining.contracts.state_machines import (
    VALID_PARSE_RUN_STATES,
)
from knowledge_mining.mining.infra.pg_schema import _split_ddl
from tests.contracts.test_storage_ddl import (
    _ADD_COLUMN_RE,
    _ensure_columns,
    _strip_comments,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_DIR = _REPO_ROOT / "databases" / "asset_core" / "schemas"


def _load_full_sqlite_chain() -> sqlite3.Connection:
    """001.sqlite + 008 + 009 + 010（复用 test_storage_ddl 的降级加载策略）.

    SQLite 不认 ``ADD COLUMN IF NOT EXISTS``（PG 扩展）——分类回放：
    CREATE TABLE 先行、ADD COLUMN 经存在性守卫降级、INDEX 最后（D-019）。
    同时复刻 004_kb_isolation 的列级补充（008 依赖，与 _load_schema 一致）。
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        (_SCHEMA_DIR / "001_asset_core.sqlite.sql").read_text(encoding="utf-8")
    )
    _ensure_columns(conn, "asset_documents", {
        "kb_id": "TEXT", "storage_path": "TEXT", "directory_path": "TEXT",
    })
    for name in (
        "008_object_storage_foundation.sql",
        "009_shadow_parse_runs.sql",
        "010_m4_parse_run_state_machine.sql",
    ):
        raw = _strip_comments(
            (_SCHEMA_DIR / name).read_text(encoding="utf-8")
        )
        statements = [s for s in _split_ddl(raw) if s.strip()]
        alters: list[str] = []
        indexes: list[str] = []
        others: list[str] = []
        for stmt in statements:
            if re.match(r"CREATE\s+(?:UNIQUE\s+)?INDEX\b", stmt, re.IGNORECASE):
                indexes.append(stmt)
            elif stmt.lstrip().upper().startswith("ALTER"):
                alters.append(stmt)
            else:
                others.append(stmt)
        for stmt in others:  # CREATE TABLE（含 010 的表重建）
            conn.execute(stmt)
        for stmt in alters:
            match = _ADD_COLUMN_RE.match(stmt.lstrip())
            if not match:
                conn.execute(stmt)
                continue
            table, column, rest = (
                match.group(1), match.group(2), match.group(3).strip(),
            )
            _ensure_columns(conn, table, {column: rest})
        for stmt in indexes:  # 索引最后（列已就位）
            conn.execute(stmt)
    conn.commit()
    return conn


def _load_009_010() -> sqlite3.Connection:
    # 010 的快照表重建引用 001/008 的表，必须走全链。
    return _load_full_sqlite_chain()


def test_snapshot_mime_whitelist_extended_and_columns_preserved() -> None:
    """010 重建后：XLSX/PPTX MIME 可入快照表；008 列与索引保留."""
    conn = _load_full_sqlite_chain()
    for mime in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/pdf",
    ):
        conn.execute(
            "INSERT INTO asset_document_snapshots (id, domain,"
            " normalized_content_hash, raw_content_hash, mime_type, created_at)"
            " VALUES (?, 'd', 'nh', 'rh', ?, 't')",
            (f"s-{mime[-6:]}", mime),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO asset_document_snapshots (id, domain,"
            " normalized_content_hash, raw_content_hash, mime_type, created_at)"
            " VALUES ('s-bad', 'd', 'nh', 'rh', 'video/mp4', 't')"
        )
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(asset_document_snapshots)")
    }
    assert {
        "snapshot_fingerprint", "parse_ir_storage_object_id",
        "parse_ir_schema_version", "parser_fingerprint",
        "compiler_fingerprint", "quality_status", "lifecycle_status",
        "created_by_run_id",
    } <= cols
    # 指纹 partial unique 仍在（重建后重挂）。
    conn.execute(
        "INSERT INTO asset_document_snapshots (id, domain,"
        " normalized_content_hash, raw_content_hash, mime_type, created_at,"
        " snapshot_fingerprint)"
        " VALUES ('s-f1', 'd', 'nh1', 'rh1', 'other', 't', 'snap-x')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO asset_document_snapshots (id, domain,"
            " normalized_content_hash, raw_content_hash, mime_type, created_at,"
            " snapshot_fingerprint)"
            " VALUES ('s-f2', 'd', 'nh2', 'rh2', 'other', 't', 'snap-x')"
        )


def _insert_run(conn: sqlite3.Connection, status: str) -> None:
    conn.execute(
        "INSERT INTO asset_parse_runs (id, document_id, source_storage_object_id,"
        " source_raw_hash, source_content_revision, parser_id,"
        " parser_fingerprint, status, started_at)"
        " VALUES ('r1', 'doc1', 'so1', 'raw', 1, 'p', 'fp', ?, '2026-08-18')",
        (status,),
    )


def test_010_files_exist_both_dialects() -> None:
    for name in (
        "010_m4_parse_run_state_machine.sql",
        "010_m4_parse_run_state_machine_postgresql.sql",
    ):
        assert (_SCHEMA_DIR / name).exists(), name


def test_parse_runs_accepts_all_state_machine_statuses() -> None:
    conn = _load_009_010()
    for i, status in enumerate(sorted(VALID_PARSE_RUN_STATES)):
        conn.execute("DELETE FROM asset_parse_runs")
        _insert_run(conn, status)  # 不抛 = CHECK 接受
    conn.close()


def test_parse_runs_rejects_unknown_status() -> None:
    conn = _load_009_010()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_run(conn, "EXPLODED")


def test_attempt_events_table_shape() -> None:
    conn = _load_009_010()
    cols = {
        row[1] for row in
        conn.execute("PRAGMA table_info(asset_parse_run_attempts)")
    }
    assert {
        "id", "parse_run_id", "attempt_index", "parser_id",
        "parser_fingerprint", "attempt_kind", "outcome", "started_at",
        "finished_at", "error_message", "metadata_json",
    } <= cols
    # 幂等：同一 run 的 attempt 序号唯一。
    conn.execute(
        "INSERT INTO asset_parse_run_attempts (id, parse_run_id, attempt_index,"
        " parser_id, parser_fingerprint, attempt_kind, outcome, started_at)"
        " VALUES ('a1', 'r1', 0, 'p', 'fp', 'primary', 'FAILED', 't')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO asset_parse_run_attempts (id, parse_run_id,"
            " attempt_index, parser_id, parser_fingerprint, attempt_kind,"
            " outcome, started_at)"
            " VALUES ('a2', 'r1', 0, 'p', 'fp', 'fallback', 'FAILED', 't')")
    # 非法 attempt_kind 拒绝。
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO asset_parse_run_attempts (id, parse_run_id,"
            " attempt_index, parser_id, parser_fingerprint, attempt_kind,"
            " outcome, started_at)"
            " VALUES ('a3', 'r1', 1, 'p', 'fp', 'magic', 'FAILED', 't')")
