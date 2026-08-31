"""31 号 DB-01：retrieval v2 schema 只能由启动迁移维护。"""
from __future__ import annotations


def test_domain_schema_paths_include_retrieval_v2_migration() -> None:
    from knowledge_mining.mining.infra.pg_schema import (
        _split_ddl,
        domain_schema_paths,
    )

    paths = domain_schema_paths()
    names = [path.name for path in paths]
    assert "013_retrieval_assets_v2_staging.sql" in names
    migration = next(
        path for path in paths
        if path.name == "013_retrieval_assets_v2_staging.sql"
    )
    statements = _split_ddl(migration.read_text(encoding="utf-8"))
    joined = "\n".join(statements)
    assert "pg_advisory_xact_lock" in statements[0]
    assert "asset_retrieval_units_v2_staging" in joined
    assert "asset_snapshot_readiness_staging" in joined


def test_retrieval_repositories_have_no_runtime_schema_initializer() -> None:
    from knowledge_mining.mining.retrieval_projection.repositories_pg import (
        PgAssetWriter,
        PgEmbeddingStore,
        PgRepresentationStore,
    )

    for repository_type in (PgRepresentationStore, PgEmbeddingStore, PgAssetWriter):
        assert not hasattr(repository_type, "_ensure_schema")
