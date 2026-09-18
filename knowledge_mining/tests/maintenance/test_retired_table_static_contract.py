from __future__ import annotations

from pathlib import Path
import re

import pytest

from knowledge_mining.mining.maintenance.database_upgrade.contract import RETIRED_TABLES


REPO_ROOT = Path(__file__).resolve().parents[3]


def _hits(root: Path, suffixes: tuple[str, ...], excluded_parts: set[str]) -> list[str]:
    hits: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in excluded_parts for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        for table in RETIRED_TABLES:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(table)}(?![A-Za-z0-9_])", text):
                hits.append(f"{path.relative_to(REPO_ROOT)} -> {table}")
    return hits


@pytest.mark.parametrize(
    ("relative_root", "suffixes", "excluded_parts"),
    [
        (
            "knowledge_mining/mining",
            (".py",),
            {"maintenance", "__pycache__"},
        ),
        (
            "agent_serving_java/src/main",
            (".java", ".xml", ".sql"),
            {"db"},
        ),
    ],
)
def test_retired_tables_have_no_production_code_consumers(
    relative_root: str,
    suffixes: tuple[str, ...],
    excluded_parts: set[str],
) -> None:
    assert _hits(REPO_ROOT / relative_root, suffixes, excluded_parts) == []


def test_active_mining_schema_chain_does_not_create_retired_tables() -> None:
    from knowledge_mining.mining.infra.pg_schema import primary_schema_paths

    hits: list[str] = []
    for path in primary_schema_paths():
        text = path.read_text(encoding="utf-8")
        for table in RETIRED_TABLES:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(table)}(?![A-Za-z0-9_])", text):
                hits.append(f"{path.name} -> {table}")
    assert hits == []


def test_java_bootstrap_list_does_not_create_retired_tables() -> None:
    from knowledge_mining.mining.maintenance.database_upgrade.bootstrap import (
        JAVA_BOOTSTRAP_SQL,
    )

    hits: list[str] = []
    for relative_path in JAVA_BOOTSTRAP_SQL:
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for table in RETIRED_TABLES:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(table)}(?![A-Za-z0-9_])", text):
                hits.append(f"{relative_path} -> {table}")
    assert hits == []
