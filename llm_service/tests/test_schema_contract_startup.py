from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from llm_service import main as llm_main
from llm_service import pg_schema


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.queries: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.queries.append((str(query), params))
        return _Cursor(next(self.rows))


def test_llm_startup_does_not_replay_ddl_or_terminate_shared_connections() -> None:
    main_source = inspect.getsource(llm_main)
    schema_source = inspect.getsource(pg_schema)

    assert "ensure_schema(pg_cfg)" not in main_source
    assert "pg_terminate_backend" not in schema_source


def test_schema_contract_accepts_expected_ledger_version(monkeypatch) -> None:
    connection = _Connection(rows=[(True,), (True,)])
    monkeypatch.setattr(pg_schema.psycopg, "connect", lambda *_a, **_k: connection)

    pg_schema.assert_schema_contract(SimpleNamespace(conninfo="secret"))

    assert len(connection.queries) == 2
    assert "to_regclass" in connection.queries[0][0]
    assert "details_json" in connection.queries[1][0]


def test_schema_contract_fails_closed_without_ledger(monkeypatch) -> None:
    connection = _Connection(rows=[(False,)])
    monkeypatch.setattr(pg_schema.psycopg, "connect", lambda *_a, **_k: connection)

    with pytest.raises(RuntimeError, match="database migration required"):
        pg_schema.assert_schema_contract(SimpleNamespace(conninfo="secret"))
