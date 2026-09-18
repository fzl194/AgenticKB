from __future__ import annotations

from pathlib import Path


def test_incremental_sync_keeps_package_surface_and_runs_container_migrations_before_restart() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "deploy-sync.sh").read_text(encoding="utf-8")

    assert '"knowledge_mining:mining"' in script
    assert '"databases:-"' in script
    assert "docker build" not in script
    assert "prepare_migration_code_backup" in script
    assert "restore_migration_code_backup" in script
    assert "rollback_database_cutover" in script
    assert "database_upgrade rollback-config" in script
    for command in ("database_upgrade plan", "database_upgrade apply", "database_upgrade verify"):
        assert command in script
    assert script.index("run_database_upgrade_if_needed") < script.index(
        'restart_services "$needed"', script.index("cmd_apply()")
    )


def test_database_upgrade_stops_writers_but_keeps_control_for_config_cutover() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "deploy-sync.sh").read_text(encoding="utf-8")
    hook = script[script.index("run_database_upgrade_if_needed()") : script.index("restart_services()")]

    assert "for svc in mcp serving mining llm_service" in hook
    assert 'supervisorctl stop "$svc"' in hook
    assert 'if ! compose exec -T app supervisorctl stop "$svc"' in hook
    assert '"$state" != "STOPPED"' in hook
    assert "supervisorctl stop control" not in hook


def test_cutover_rollback_stops_new_processes_before_restoring_config_and_code() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "deploy-sync.sh").read_text(encoding="utf-8")
    rollback_start = script.index("rollback_database_cutover()")
    rollback = script[rollback_start : script.index("restart_services()", rollback_start)]

    stop_at = rollback.index('supervisorctl stop "$svc"')
    config_at = rollback.index("database_upgrade rollback-config")
    code_at = rollback.index("restore_migration_code_backup")
    start_at = rollback.index("restart_old_services_after_rollback")
    assert stop_at < config_at < code_at < start_at


def test_migration_admin_password_is_not_embedded_in_process_arguments() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "deploy-sync.sh").read_text(encoding="utf-8")

    assert '"CMKB_MIGRATION_PG_PASSWORD=$CMKB_MIGRATION_PG_PASSWORD"' not in script
    assert 'env_args+=("-e" "CMKB_MIGRATION_PG_PASSWORD")' in script
