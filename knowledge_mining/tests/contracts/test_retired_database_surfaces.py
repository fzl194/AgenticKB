"""Clean-baseline contract for database convergence.

The one-time migration owns the legacy table names.  Runtime Python and the
bootstrap DDL chain must not keep readers, writers, routes, or table creators
that can resurrect the retired surfaces after the migration drops them.
"""
from __future__ import annotations

from pathlib import Path
import re

from knowledge_mining.mining.infra.pg_schema import domain_schema_paths
from knowledge_mining.mining.maintenance.database_upgrade.contract import (
    RETIRED_TABLES,
)


_ROOT = Path(__file__).resolve().parents[3]

_RETIRED_TABLES = frozenset(RETIRED_TABLES)

_RETIRED_ONTOLOGY_MODULES = (
    "knowledge_mining/mining/api/routes/ontology.py",
    "knowledge_mining/mining/infra/ontology_bootstrap.py",
    "knowledge_mining/mining/infra/ontology_store.py",
    "knowledge_mining/mining/stages/entity_extract/__init__.py",
    "knowledge_mining/mining/stages/entity_relations/__init__.py",
    "knowledge_mining/mining/stages/graph_write/__init__.py",
    "knowledge_mining/mining/stages/ontology_induction/__init__.py",
    "knowledge_mining/mining/stages/resolve/__init__.py",
    "knowledge_mining/mining/workflow/handlers/research.py",
    "knowledge_mining/mining/workflow/operators/research.py",
    "knowledge_mining/mining/stages/withdrawal.py",
)


def test_bootstrap_chain_never_recreates_retired_tables() -> None:
    ddl = "\n".join(path.read_text(encoding="utf-8") for path in domain_schema_paths())

    for table in sorted(_RETIRED_TABLES):
        assert not re.search(rf"\b{re.escape(table)}\b", ddl), (
            f"startup DDL still references retired table {table}"
        )


def test_runtime_python_has_no_retired_table_sql() -> None:
    runtime_root = _ROOT / "knowledge_mining" / "mining"
    offenders: dict[str, list[str]] = {}

    for path in runtime_root.rglob("*.py"):
        if "maintenance" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        hits = sorted(
            table
            for table in _RETIRED_TABLES
            if re.search(rf"\b{re.escape(table)}\b", text)
        )
        if hits:
            offenders[str(path.relative_to(_ROOT))] = hits

    assert offenders == {}


def test_retired_ontology_modules_are_removed() -> None:
    present = [path for path in _RETIRED_ONTOLOGY_MODULES if (_ROOT / path).exists()]

    assert present == []


def test_runtime_pipeline_has_no_retired_graph_write_hook() -> None:
    pipeline = (_ROOT / "knowledge_mining/mining/pipeline.py").read_text(
        encoding="utf-8"
    )

    for retired in (
        "entity_extractor",
        "entity_relation_builder",
        "graph_store",
        "ontology_store",
        "stages.graph_write",
    ):
        assert retired not in pipeline


def test_java_legacy_schema_migration_resource_is_removed() -> None:
    assert not (
        _ROOT / "agent_serving_java/src/main/resources/db/migrate_v1_to_zdy.sql"
    ).exists()
