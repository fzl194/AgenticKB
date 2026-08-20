from pathlib import Path

from knowledge_mining.mining.infra import pg_schema
from knowledge_mining.mining.infra.domain_db import (
    ResolvedDomainDatabase,
    ensure_domain_database_schema,
)


def test_primary_schema_contains_control_and_runtime_migrations() -> None:
    names = [path.name for path in pg_schema.primary_schema_paths()]
    assert "001_mining_workflow_postgresql.sql" in names
    assert "005_mining_workflow_runtime.sql" in names


def test_domain_schema_never_contains_global_control_store() -> None:
    paths = pg_schema.domain_schema_paths()
    names = [path.name for path in paths]
    assert "005_mining_workflow_runtime.sql" in names
    assert "001_mining_workflow_postgresql.sql" not in names
    # 本体表必须晚于其依赖（asset/runtime）；链尾为后续里程碑的增量
    # DDL（009 影子解析/010 状态机/011 切片落库，见 ADR-0003）。
    assert names.index("001_ontology_concept_postgresql.sql") > names.index(
        "005_mining_workflow_runtime.sql"
    )


def test_compatibility_initializer_delegates_to_primary(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(pg_schema, "ensure_primary_schema", lambda cfg: calls.append(cfg))
    cfg = object()
    pg_schema.ensure_schema(cfg)
    assert calls == [cfg]


def test_domain_database_initializer_uses_domain_schema_only(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(pg_schema, "ensure_domain_schema", lambda cfg: calls.append(cfg))
    resolved = ResolvedDomainDatabase(
        "host=localhost dbname=plant_test user=test password=test",
        pool_min=1,
        pool_max=2,
        source="inline",
    )
    ensure_domain_database_schema(resolved)
    assert len(calls) == 1
    assert calls[0].pg_dbname == "plant_test"


def test_runtime_schema_has_no_cross_database_workflow_foreign_key() -> None:
    runtime_path = next(
        path
        for path in pg_schema.domain_schema_paths()
        if path.name == "005_mining_workflow_runtime.sql"
    )
    ddl = Path(runtime_path).read_text(encoding="utf-8").lower()
    assert "references mining_workflows" not in ddl
    assert "references mining_workflow_versions" not in ddl
    assert "'not_applicable'" in ddl
    assert "'fallback'" in ddl
