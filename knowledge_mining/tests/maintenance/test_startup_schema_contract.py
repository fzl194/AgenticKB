from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.api import app as mining_app
from knowledge_mining.mining.infra import domain_db, pg_schema
from knowledge_mining.mining.maintenance.database_upgrade.contract import (
    CURRENT_SCHEMA_CHECKSUM,
    CURRENT_SCHEMA_VERSION,
)


class _Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)

    def execute(self, *_args, **_kwargs):
        return None

    def fetchone(self):
        return next(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Connection:
    def __init__(self, rows):
        self.cursor_instance = _Cursor(rows)
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


def test_production_startup_does_not_call_schema_ddl_initializers() -> None:
    assert "ensure_primary_schema(cfg)" not in inspect.getsource(mining_app)
    assert "ensure_domain_schema(cfg)" not in inspect.getsource(domain_db)


def test_mining_schema_contract_accepts_expected_ledger(monkeypatch) -> None:
    connection = _Connection([(True,), (True,)])
    monkeypatch.setattr(pg_schema, "_connect_safely", lambda *_a, **_k: connection)

    pg_schema.assert_schema_contract(SimpleNamespace(pg_dbname="db"))

    assert connection.closed is True


def test_mining_schema_contract_fails_closed_without_ledger(monkeypatch) -> None:
    connection = _Connection([(False,)])
    monkeypatch.setattr(pg_schema, "_connect_safely", lambda *_a, **_k: connection)

    with pytest.raises(RuntimeError, match="database migration required"):
        pg_schema.assert_schema_contract(SimpleNamespace(pg_dbname="db"))


def test_java_schema_contract_version_matches_python() -> None:
    repo_root = __import__("pathlib").Path(__file__).resolve().parents[3]
    java = (
        repo_root
        / "agent_serving_java/src/main/java/com/coremasterkb/serving/observability/ServingRuntimeSchemaInitializer.java"
    ).read_text(encoding="utf-8")

    assert f'EXPECTED_SCHEMA_VERSION = "{CURRENT_SCHEMA_VERSION}"' in java
    assert f'"{CURRENT_SCHEMA_CHECKSUM}"' in java


def test_python_java_and_purge_share_validated_published_build_contract() -> None:
    repo_root = __import__("pathlib").Path(__file__).resolve().parents[3]
    python_db = (repo_root / "knowledge_mining/mining/kb/db.py").read_text(encoding="utf-8")
    purge = (repo_root / "knowledge_mining/mining/kb/services/purge_service.py").read_text(
        encoding="utf-8"
    )
    java_mapper = (
        repo_root
        / "agent_serving_java/src/main/resources/mapper/AssetBuildDocumentSnapshotMapper.xml"
    ).read_text(encoding="utf-8")
    expected = "status IN ('validated', 'published')"

    assert expected in python_db
    assert expected in purge
    assert expected in java_mapper
