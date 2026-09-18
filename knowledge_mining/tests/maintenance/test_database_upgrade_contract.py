from __future__ import annotations

from copy import deepcopy
import os

import pytest

from knowledge_mining.mining.maintenance.database_upgrade.config import (
    ConfigConvergenceError,
    converge_database_config,
    restore_config_backup,
    write_converged_config,
)
from knowledge_mining.mining.maintenance.database_upgrade.contract import (
    CURRENT_SCHEMA_VERSION,
    EXPECTED_FORMAL_TABLES,
    EXPECTED_PHYSICAL_TABLES,
    RETIRED_TABLES,
)


EXPECTED_RETIRED_TABLES = {
    "asset_upload_sessions",
    "asset_storage_object_refs",
    "asset_file_audit_events",
    "asset_storage_quotas",
    "asset_storage_operations",
    "asset_parse_run_attempts",
    "asset_raw_segment_relations",
    "asset_segment_element_links",
    "asset_retrieval_units",
    "asset_retrieval_embeddings",
    "ontology_versions",
    "ontology_node_types",
    "ontology_relation_types",
    "ontology_entities",
    "ontology_entity_relations",
    "ontology_alias_dictionary",
    "ontology_evidence_nodes",
    "ontology_candidates",
    "asset_segment_entity_mentions",
    "asset_publish_releases",
}


def _database(dbname: str) -> dict[str, object]:
    return {
        "host": "db.internal",
        "port": 5432,
        "dbname": dbname,
        "user": "kb_user",
        "password": "secret",
        "sslmode": "disable",
        "pool_min": 2,
        "pool_max": 10,
    }


def test_convergence_contract_has_exact_retired_table_set() -> None:
    assert set(RETIRED_TABLES) == EXPECTED_RETIRED_TABLES
    assert len(RETIRED_TABLES) == 20
    assert EXPECTED_FORMAL_TABLES == 52
    assert EXPECTED_PHYSICAL_TABLES == 53
    assert CURRENT_SCHEMA_VERSION


def test_converge_config_switches_default_and_removes_duplicate_inline_databases() -> None:
    database_document = {"driver": "postgresql", "default": _database("old_db")}
    registry_document = {
        "default_domain": "generic",
        "domains": {
            "generic": {
                "display_name": "Generic",
                "enabled": True,
                "database": _database("old_db"),
                "services": {"mining_url": "http://localhost:8901"},
            },
            "odn": {
                "display_name": "ODN",
                "enabled": True,
                "database": _database("old_db"),
            },
        },
    }
    original_database = deepcopy(database_document)
    original_registry = deepcopy(registry_document)

    converged_database, converged_registry = converge_database_config(
        database_document=database_document,
        registry_document=registry_document,
        source_dbname="old_db",
        target_dbname="clean_db",
    )

    assert converged_database["default"]["dbname"] == "clean_db"
    assert converged_registry["default_domain"] == "generic"
    assert converged_registry["domains"]["generic"]["services"] == {
        "mining_url": "http://localhost:8901"
    }
    assert "database" not in converged_registry["domains"]["generic"]
    assert "database" not in converged_registry["domains"]["odn"]
    assert database_document == original_database
    assert registry_document == original_registry


def test_converge_config_refuses_mixed_physical_sources() -> None:
    database_document = {"driver": "postgresql", "default": _database("old_db")}
    registry_document = {
        "default_domain": "generic",
        "domains": {
            "generic": {"database": _database("old_db")},
            "odn": {"database": _database("another_db")},
            "disabled": {"enabled": False, "database": _database("another_db")},
        },
    }

    with pytest.raises(ConfigConvergenceError, match="多个物理数据库"):
        converge_database_config(
            database_document=database_document,
            registry_document=registry_document,
            source_dbname="old_db",
            target_dbname="clean_db",
        )


def test_converge_config_refuses_unexpected_source_name() -> None:
    database_document = {"driver": "postgresql", "default": _database("actual_db")}
    registry_document = {
        "default_domain": "generic",
        "domains": {"generic": {"database": _database("actual_db")}},
    }

    with pytest.raises(ConfigConvergenceError, match="源数据库不匹配"):
        converge_database_config(
            database_document=database_document,
            registry_document=registry_document,
            source_dbname="old_db",
            target_dbname="clean_db",
        )


def test_converge_config_checks_disabled_domains_before_removing_inline_database() -> None:
    database_document = {"driver": "postgresql", "default": _database("old_db")}
    registry_document = {
        "default_domain": "generic",
        "domains": {
            "generic": {"database": _database("old_db")},
            "disabled": {
                "enabled": False,
                "database": _database("another_db"),
            },
        },
    }

    with pytest.raises(ConfigConvergenceError, match="多个物理数据库"):
        converge_database_config(
            database_document=database_document,
            registry_document=registry_document,
            source_dbname="old_db",
            target_dbname="clean_db",
        )


def test_write_converged_config_is_persistent_and_keeps_restricted_backup(tmp_path) -> None:
    config_dir = tmp_path / "config"
    system_dir = config_dir / "system"
    system_dir.mkdir(parents=True)
    database_path = system_dir / "database.yaml"
    registry_path = config_dir / "domain_registry.yaml"
    database_path.write_text(
        "driver: postgresql\ndefault:\n  host: db.internal\n  port: 5432\n"
        "  dbname: old_db\n  user: kb_user\n  password: secret\n",
        encoding="utf-8",
    )
    registry_path.write_text(
        "default_domain: generic\ndomains:\n  generic:\n    enabled: true\n"
        "    database:\n      host: db.internal\n      port: 5432\n"
        "      dbname: old_db\n      user: kb_user\n      password: secret\n",
        encoding="utf-8",
    )

    result = write_converged_config(
        config_dir=config_dir,
        backup_root=tmp_path / "backups",
        migration_id="core-test",
        source_dbname="old_db",
        target_dbname="clean_db",
    )

    assert "dbname: clean_db" in database_path.read_text(encoding="utf-8")
    updated_registry = registry_path.read_text(encoding="utf-8")
    assert "default_domain: generic" in updated_registry
    assert "database:" not in updated_registry
    assert result.backup_dir.is_dir()
    assert (result.backup_dir / "database.yaml").is_file()
    assert (result.backup_dir / "domain_registry.yaml").is_file()
    if os.name != "nt":
        assert result.backup_dir.stat().st_mode & 0o077 == 0

    restore_config_backup(
        config_dir=config_dir,
        backup_root=tmp_path / "backups",
        migration_id="core-test",
    )
    assert "dbname: old_db" in database_path.read_text(encoding="utf-8")
    assert "database:" in registry_path.read_text(encoding="utf-8")
