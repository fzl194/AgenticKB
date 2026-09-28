from __future__ import annotations

import os
import subprocess
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
    # stop 退出码被豁免（重跑时已停服务会报非零），真实停不掉由 STOPPED 断言拦截。
    assert 'supervisorctl stop "$svc" >/dev/null 2>&1 || true' in hook
    # 门禁只拦 RUNNING/STARTING：FATAL/EXITED（崩溃放弃）同样满足停写屏障。
    assert 'STOPPED|EXITED|FATAL) ;;' in hook
    assert "supervisorctl stop control" not in hook
    # pipefail 下 supervisorctl status 对非 RUNNING 态可能返回非零，读取必须豁免退出码。
    assert "supervisorctl status \"$svc\" 2>/dev/null | awk '{print $2}' || true" in hook


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
    assert 'if [ "$DB_CONFIG_SWITCHED" = true ]' in rollback


def test_migration_admin_password_is_not_embedded_in_process_arguments() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "deploy-sync.sh").read_text(encoding="utf-8")

    assert '"CMKB_MIGRATION_PG_PASSWORD=$CMKB_MIGRATION_PG_PASSWORD"' not in script
    assert 'env_args+=("-e" "CMKB_MIGRATION_PG_PASSWORD")' in script
    assert "CMKB_MIGRATION_FALLBACK_DOMAIN" not in script


def test_existing_rotation_target_requires_interactive_delete_confirmation() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "deploy-sync.sh").read_text(encoding="utf-8")
    hook = script[
        script.index("run_database_upgrade_if_needed()") : script.index("prepare_migration_code_backup()")
    ]

    assert 'replace_target_required' in hook
    assert 'grep -Eq' in hook
    assert 'read -r -p' in hook
    assert '</dev/tty' in hook
    assert '--replace-target' in hook
    assert hook.index('read -r -p') < hook.index('database_upgrade apply')


def test_apply_failure_uses_unified_rollback_before_restoring_old_code() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "deploy-sync.sh").read_text(encoding="utf-8")
    hook = script[
        script.index("run_database_upgrade_if_needed()") : script.index("prepare_migration_code_backup()")
    ]
    failure = hook[hook.index("database_upgrade apply") : hook.index("database_upgrade verify")]

    assert "DB_MIGRATION_RAN=true" in failure
    assert "rollback_database_cutover" in failure
    assert failure.index("rollback_database_cutover") < failure.index(
        "restart_old_services_after_rollback"
    ) if "restart_old_services_after_rollback" in failure else True


def test_online_rollback_removes_new_ledger_rows_before_old_code_restore() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "deploy-sync.sh").read_text(encoding="utf-8")
    rollback_start = script.index("rollback_database_cutover()")
    rollback = script[rollback_start : script.index("restart_services()", rollback_start)]

    ledger_at = rollback.index("database_upgrade rollback-online-ledger")
    code_at = rollback.index("restore_migration_code_backup")
    assert 'if [ "$DB_ONLINE_MIGRATION" = true ]' in rollback
    assert '--deployment-id "$DB_MIGRATION_DEPLOYMENT_ID"' in rollback
    assert ledger_at < code_at


def test_online_apply_and_rollback_share_one_deployment_id() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "deploy-sync.sh").read_text(encoding="utf-8")
    hook = script[
        script.index("run_database_upgrade_if_needed()") : script.index("prepare_migration_code_backup()")
    ]

    assert 'DB_MIGRATION_DEPLOYMENT_ID=' in script
    assert 'apply_args+=("--deployment-id" "$DB_MIGRATION_DEPLOYMENT_ID")' in hook


def test_running_deploy_script_is_replaced_atomically_without_corrupting_current_process(
    tmp_path: Path,
) -> None:
    """The old process must finish from its old inode; the next run uses the new script."""
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "deploy-sync.sh").read_text(encoding="utf-8")
    start = script.index("replace_deploy_sync_script_atomically()")
    end = script.index("\n}\n", start) + 3
    helper = script[start:end]
    assert 'mktemp "$target_dir/.${target_name}.next.XXXXXX"' in helper
    assert 'cp -p -- "$source_script" "$pending_script"' in helper
    assert 'mv -f -- "$pending_script" "$target_script"' in helper
    assert 'cat -- "$STAGE_DIR/$DEPLOY_SYNC_SCRIPT" > "$DEPLOY_SYNC_SCRIPT"' not in script

    # Windows may expose a broken WSL launcher named `bash`. Structural checks
    # still run there; developers with Git Bash can opt into the executable
    # handoff through CMKB_TEST_BASH. Linux CI executes it with ordinary bash.
    bash_bin = os.environ.get("CMKB_TEST_BASH", "bash")
    if os.name == "nt" and "CMKB_TEST_BASH" not in os.environ:
        return

    runner = tmp_path / "deploy-sync.sh"
    new_script = tmp_path / "new-deploy-sync.sh"
    runner.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + helper
        + '\nprintf "old-before\\n"\n'
        + 'replace_deploy_sync_script_atomically "$1" "$0"\n'
        + 'printf "old-after\\n"\n',
        encoding="utf-8",
    )
    new_script.write_text(
        '#!/usr/bin/env bash\nprintf "new-version\\n"\n', encoding="utf-8"
    )

    first = subprocess.run(
        [bash_bin, str(runner), str(new_script)],
        check=True,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        [bash_bin, str(runner)], check=True, capture_output=True, text=True
    )

    assert first.stdout.splitlines() == ["old-before", "old-after"]
    assert second.stdout.splitlines() == ["new-version"]


def test_script_only_package_installs_new_deploy_script_before_no_restart_return() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "deploy-sync.sh").read_text(encoding="utf-8")
    apply = script[script.index("cmd_apply()") :]
    no_restart = apply[
        apply.index('if [ -z "$(printf \'%s\' "$needed" | tr -d \' \')" ]; then') :
        apply.index("restart_services", apply.index('if [ -z "$(printf \'%s\' "$needed"'))
    ]

    assert "install_staged_deploy_sync_script" in no_restart
    assert no_restart.index("install_staged_deploy_sync_script") < no_restart.index("return 0")


def test_atomic_self_update_validates_new_script_before_replacing_current_one(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "deploy-sync.sh").read_text(encoding="utf-8")
    start = script.index("replace_deploy_sync_script_atomically()")
    end = script.index("\n}\n", start) + 3
    helper = script[start:end]

    syntax_at = helper.index('bash -n "$pending_script"')
    move_at = helper.index('mv -f -- "$pending_script" "$target_script"')
    assert syntax_at < move_at

    bash_bin = os.environ.get("CMKB_TEST_BASH", "bash")
    if os.name == "nt" and "CMKB_TEST_BASH" not in os.environ:
        return

    runner = tmp_path / "deploy-sync.sh"
    invalid_script = tmp_path / "invalid-deploy-sync.sh"
    original = (
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + helper
        + '\nreplace_deploy_sync_script_atomically "$1" "$0"\n'
        + 'printf "must-not-run\\n"\n'
    )
    runner.write_text(original, encoding="utf-8")
    invalid_script.write_text("#!/usr/bin/env bash\nif then\n", encoding="utf-8")

    rejected = subprocess.run(
        [bash_bin, str(runner), str(invalid_script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert rejected.returncode != 0
    assert "must-not-run" not in rejected.stdout
    assert runner.read_text(encoding="utf-8") == original


def test_self_update_failure_reports_business_publish_succeeded() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "deploy-sync.sh").read_text(encoding="utf-8")
    start = script.index("install_staged_deploy_sync_script()")
    end = script.index("\n}\n", start) + 3
    install = script[start:end]

    assert "业务发布成功但 deploy-sync.sh 更新失败" in install
    assert 'if ! replace_deploy_sync_script_atomically' in install
    assert "return 1" in install
