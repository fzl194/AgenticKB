"""一键重启：编排模块单元 + /api/v1/admin/restart 端点（鉴权/防重入/状态读取）。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from main_control_service import restart_services as rs
from main_control_service.jwt_util import encode
from main_control_service.main import create_app

_AUTH = (
    "enabled: true\njwt_secret: s\ntoken_ttl_seconds: 3600\n"
    "internal_verify_secret: ivs\nbootstrap: {admin_password: x}\n"
)


def _client(tmp_path: Path) -> TestClient:
    d = tmp_path / "system"
    d.mkdir(parents=True, exist_ok=True)
    (d / "auth.yaml").write_text(_AUTH, encoding="utf-8")
    (tmp_path / "domain_registry.yaml").write_text(
        "default_domain: d\ndomains:\n  d:\n    display_name: D\n    enabled: true\n",
        encoding="utf-8",
    )
    return TestClient(create_app(config_dir=tmp_path))


def _status_file(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "restart-status.json"
    monkeypatch.setenv("CMKB_RESTART_STATUS_FILE", str(path))
    return path


# ── 编排模块 ────────────────────────────────────────────────────────────────

def test_restart_plan_control_first_mcp_last_nginx_absent():
    plan = [s.program for s in rs.restart_plan()]
    assert plan[0] == "control"  # 配置中心先行，下游启动要从它拉配置
    assert plan[-1] == "mcp"
    assert "nginx" not in plan


def test_status_roundtrip_and_is_active(tmp_path, monkeypatch):
    path = _status_file(tmp_path, monkeypatch)
    assert rs.read_status() == {}  # 无文件 → 空

    fresh = {
        "state": "running",
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    rs.write_status(fresh)
    assert rs.read_status() == fresh
    assert rs.is_active(fresh) is True

    stale = {**fresh,
             "started_at": (datetime.now(timezone.utc) - timedelta(seconds=rs.STALE_AFTER_SECONDS + 1)).isoformat()}
    assert rs.is_active(stale) is False
    assert rs.is_active({"state": "done"}) is False
    assert rs.is_active({"state": "running", "started_at": "garbage"}) is False
    assert not path.with_name(path.name + ".tmp").exists()  # 原子写不留残片


def test_restart_program_start_vs_restart_by_state(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_ctl(*args, timeout=90):
        calls.append(args)
        import subprocess
        stdout = (f"{args[1]}                          RUNNING   pid 1   uptime 0:00:01"
                  if args[0] == "status" else "")
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(rs, "_supervisorctl", fake_ctl)
    # fake_ctl 的 status 输出恒为 RUNNING → 先查态、再 restart
    rs.restart_program("control")
    assert calls[0] == ("status", "control")
    assert calls[-1] == ("restart", "control")

    calls.clear()
    monkeypatch.setattr(rs, "service_status", lambda _p: "FATAL")
    rs.restart_program("mining")
    assert calls == [("start", "mining")]  # 停态走 start，避开 restart 的 stop 报错


def test_health_ok_json_gate_and_any_response():
    control, mcp = rs.restart_plan()[0], rs.restart_plan()[4]

    def with_body(ok: bool, body: str):
        return lambda _url, timeout=3.0: (ok, body)

    import main_control_service.restart_services as mod
    orig = mod._http_get

    mod._http_get = with_body(True, '{"status": "ok"}')
    assert mod._health_ok(control) is True
    mod._http_get = with_body(True, '{"status": "starting"}')
    assert mod._health_ok(control) is False
    mod._http_get = with_body(True, "not json")
    assert mod._health_ok(control) is False
    mod._http_get = with_body(False, "")
    assert mod._health_ok(control) is False
    # mcp：任意 HTTP 回包即在线（裸 GET /mcp 允许 4xx）
    mod._http_get = with_body(True, "anything")
    assert mod._health_ok(mcp) is True
    mod._http_get = with_body(False, "")
    assert mod._health_ok(mcp) is False
    mod._http_get = orig


def test_run_restart_full_success_order(tmp_path, monkeypatch):
    _status_file(tmp_path, monkeypatch)
    monkeypatch.setattr(rs, "restart_program", lambda _p: None)
    monkeypatch.setattr(rs, "wait_healthy", lambda step, **_kw: True)
    monkeypatch.setattr(rs, "snapshot_services",
                        lambda: [{"name": "control", "status": "RUNNING"}])

    assert rs.run_restart("admin") == 0
    status = rs.read_status()
    assert status["state"] == "done"
    assert status["completed"] == [s.program for s in rs.restart_plan()]
    assert status["triggered_by"] == "admin"
    assert status["services"] == [{"name": "control", "status": "RUNNING"}]


def test_run_restart_stops_on_unhealthy_step(tmp_path, monkeypatch):
    _status_file(tmp_path, monkeypatch)
    monkeypatch.setattr(rs, "restart_program", lambda _p: None)

    def fake_wait(step, **_kw):
        return step.program != "mining"

    monkeypatch.setattr(rs, "wait_healthy", fake_wait)
    monkeypatch.setattr(rs, "service_status", lambda _p: "FATAL")
    monkeypatch.setattr(rs, "snapshot_services", lambda: [])

    assert rs.run_restart("admin") == 1
    status = rs.read_status()
    assert status["state"] == "failed"
    assert "mining" in status["error"]
    assert status["completed"] == ["control", "llm_service"]  # 停在失败步，不再往下


# ── 端点 ────────────────────────────────────────────────────────────────────

def _enable_restart(monkeypatch, spawned: list[str]) -> None:
    monkeypatch.setattr(rs, "supervisor_available", lambda: True)

    def fake_spawn(triggered_by: str):
        spawned.append(triggered_by)

    monkeypatch.setattr(rs, "spawn_orchestrator", fake_spawn)


def test_spawn_orchestrator_runs_from_repo_root(tmp_path, monkeypatch):
    """cwd 必须是仓库根：python -m 按 cwd 解析 main_control_service 包，
    上跳错一层（E2E 实测踩过）编排进程会 ModuleNotFoundError 静默死亡。"""
    recorded: dict = {}

    def fake_popen(args, **kwargs):
        recorded["args"] = args
        recorded.update(kwargs)
        return object()

    monkeypatch.setattr(rs.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(rs, "LOG_DIR", tmp_path)

    rs.spawn_orchestrator("admin")

    from pathlib import Path
    repo_root = str(Path(rs.__file__).resolve().parent.parent)
    assert recorded["cwd"] == repo_root
    assert recorded["args"][1:] == ["-m", "main_control_service.restart_services", "--by", "admin"]
    assert recorded["start_new_session"] is True  # 脱钩：control 被杀不牵连编排进程
    assert (tmp_path / "restart-orchestrator.log").exists()


def test_restart_requires_token(tmp_path):
    with _client(tmp_path) as c:
        assert c.post("/api/v1/admin/restart").status_code == 401


def test_restart_member_forbidden(tmp_path, monkeypatch):
    _status_file(tmp_path, monkeypatch)
    _enable_restart(monkeypatch, [])
    token = encode({"sub": "alice", "role": "member", "name": "Alice"}, "s", ttl=3600)
    with _client(tmp_path) as c:
        r = c.post("/api/v1/admin/restart", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403


def test_restart_admin_202_writes_placeholder_and_spawns(tmp_path, monkeypatch):
    _status_file(tmp_path, monkeypatch)
    spawned: list[str] = []
    _enable_restart(monkeypatch, spawned)
    token = encode({"sub": "admin", "role": "admin", "name": "Admin"}, "s", ttl=3600)
    with _client(tmp_path) as c:
        r = c.post("/api/v1/admin/restart", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 202, r.text
        assert r.json()["triggered_by"] == "admin"
    assert spawned == ["admin"]
    status = json.loads((tmp_path / "restart-status.json").read_text(encoding="utf-8"))
    assert status["state"] == "running"
    assert status["triggered_by"] == "admin"


def test_restart_conflict_while_active(tmp_path, monkeypatch):
    _status_file(tmp_path, monkeypatch)
    _enable_restart(monkeypatch, [])
    rs.write_status({
        "state": "running",
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    token = encode({"sub": "admin", "role": "admin"}, "s", ttl=3600)
    with _client(tmp_path) as c:
        assert c.post("/api/v1/admin/restart",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 409


def test_restart_unavailable_without_supervisor(tmp_path, monkeypatch):
    _status_file(tmp_path, monkeypatch)
    monkeypatch.setattr(rs, "supervisor_available", lambda: False)
    token = encode({"sub": "admin", "role": "admin"}, "s", ttl=3600)
    with _client(tmp_path) as c:
        r = c.post("/api/v1/admin/restart", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 503


def test_status_endpoint_idle_and_reading_file(tmp_path, monkeypatch):
    # 状态读取也在 /api/v1/admin/ 下（带触发者信息）——member 一并 403。
    path = _status_file(tmp_path, monkeypatch)
    token = encode({"sub": "admin", "role": "admin"}, "s", ttl=3600)
    member = encode({"sub": "alice", "role": "member"}, "s", ttl=3600)
    with _client(tmp_path) as c:
        assert c.get("/api/v1/admin/restart/status").status_code == 401
        assert c.get("/api/v1/admin/restart/status",
                     headers={"Authorization": f"Bearer {member}"}).status_code == 403

        r = c.get("/api/v1/admin/restart/status",
                  headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json() == {"state": "idle", "active": False}

        rs.write_status({"state": "done", "services": [{"name": "control", "status": "RUNNING"}]})
        r = c.get("/api/v1/admin/restart/status",
                  headers={"Authorization": f"Bearer {token}"})
        assert r.json()["state"] == "done"
        assert r.json()["active"] is False
        assert path.exists()
