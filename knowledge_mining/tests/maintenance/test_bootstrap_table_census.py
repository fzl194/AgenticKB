from __future__ import annotations

from pathlib import Path
import re

from knowledge_mining.mining.infra.pg_schema import primary_schema_paths
from knowledge_mining.mining.maintenance.database_upgrade.bootstrap import JAVA_BOOTSTRAP_SQL
from knowledge_mining.mining.maintenance.database_upgrade.contract import (
    EXPECTED_FORMAL_TABLES,
    FORMAL_TABLES,
    RETIRED_TABLES,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def _created_tables(path: Path) -> list[str]:
    sql = path.read_text(encoding="utf-8")
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return re.findall(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
        sql,
        flags=re.IGNORECASE,
    )


def test_current_bootstrap_has_exactly_52_formal_tables() -> None:
    paths = list(primary_schema_paths())
    paths.extend(
        sorted(
            (REPO_ROOT / "databases" / "agent_llm_runtime" / "schemas").glob(
                "*_postgresql.sql"
            )
        )
    )
    paths.extend(REPO_ROOT / relative for relative in JAVA_BOOTSTRAP_SQL)
    tables = {table for path in paths for table in _created_tables(path)}

    assert len(tables) == EXPECTED_FORMAL_TABLES
    assert tables == FORMAL_TABLES
    assert tables.isdisjoint(RETIRED_TABLES)


def test_export_registry_matches_current_physical_tables() -> None:
    import db_tables

    paths = list(primary_schema_paths())
    paths.extend(
        sorted(
            (REPO_ROOT / "databases" / "agent_llm_runtime" / "schemas").glob(
                "*_postgresql.sql"
            )
        )
    )
    paths.extend(REPO_ROOT / relative for relative in JAVA_BOOTSTRAP_SQL)
    expected = {table for path in paths for table in _created_tables(path)}
    expected.add("cmkb_schema_migrations")

    assert set(db_tables.EXPORT_TABLES) == expected
