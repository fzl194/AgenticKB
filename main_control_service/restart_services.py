"""后台服务依赖序重启编排 —— Web 管理按钮与 docker exec CLI 共用。

为什么是独立进程而不是 control 里的后台任务：control 自己也在重启集里。
POST /api/v1/admin/restart 先写状态文件、再脱钩拉起本模块
（start_new_session + stdio 重定向到 restart-orchestrator.log），
随即 202 返回；control 被 supervisor 杀掉后本进程作为孤儿继续执行：
逐个 supervisorctl 重启 + 健康门禁，进度落 restart-status.json。
nginx 不在重启集 —— 前端页面与轮询通路全程活着。

CLI（服务器排查用）：
    docker exec cmkb python -m main_control_service.restart_services --by ops
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(os.environ.get("CMKB_LOG_DIR", "/app/logs"))
SUPERVISOR_CONF = os.environ.get("CMKB_SUPERVISOR_CONF", "/etc/supervisor/supervisord.conf")

# state=running 超过这个秒数判陈旧（编排进程可能已死），允许重新触发。
STALE_AFTER_SECONDS = 600

# supervisor 里「不在运行」的态：restart 的 stop 段会报错，改走 start。
_STOPPED_STATES = frozenset({"STOPPED", "EXITED", "FATAL", "BACKOFF", "UNKNOWN"})


@dataclass(frozen=True)
class RestartStep:
    """一步 = 一个 supervisor program 重启 + 健康门禁。"""

    program: str
    health_url: str
    # JSON status 字段的期望值；None = 任意 HTTP 回包即算在线（mcp 对 GET 无须 200）。
    expect_status: frozenset[str] | None
    timeout_seconds: int


def restart_plan() -> list[RestartStep]:
    """依赖序：control 是配置中心先行，下游启动要从它拉配置；serving（Spring
    Boot）启动最慢，超时给最长。nginx 永不在重启集（前端是重启期间的观测面）。"""
    return [
        RestartStep("control", "http://127.0.0.1:8910/health",
                    frozenset({"ok"}), 120),
        RestartStep("llm_service", "http://127.0.0.1:8900/health",
                    frozenset({"ok"}), 120),
        RestartStep("mining", "http://127.0.0.1:8901/health",
                    frozenset({"ok"}), 180),
        RestartStep("serving", "http://127.0.0.1:8081/actuator/health",
                    frozenset({"ok", "UP"}), 300),
        RestartStep("mcp", "http://127.0.0.1:9000/mcp", None, 60),
    ]


# ── 状态文件 ────────────────────────────────────────────────────────────────

def status_path() -> Path:
    return Path(os.environ.get("CMKB_RESTART_STATUS_FILE")
                or LOG_DIR / "restart-status.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_status() -> dict:
    try:
        data = json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_status(status: dict) -> None:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def is_active(status: dict) -> bool:
    """进行中 = state=running 且未超时。超时/非 running 都允许重新触发。"""
    if status.get("state") != "running":
        return False
    try:
        started = datetime.fromisoformat(str(status.get("started_at")))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - started).total_seconds() < STALE_AFTER_SECONDS


# ── supervisor 操作 ─────────────────────────────────────────────────────────

def _supervisorctl(*args: str, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["supervisorctl", "-c", SUPERVISOR_CONF, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def supervisor_available() -> bool:
    """是否运行在 supervisor 管辖的容器里（本地裸跑开发时为 False）。"""
    try:
        return _supervisorctl("pid", timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def service_status(program: str) -> str:
    """supervisor 状态列（RUNNING/STOPPED/FATAL…），解析失败返回 UNKNOWN。"""
    proc = _supervisorctl("status", program)
    parts = proc.stdout.split()
    if len(parts) >= 2 and parts[0] == program:
        return parts[1]
    return "UNKNOWN"


def snapshot_services() -> list[dict]:
    items: list[dict] = []
    for line in _supervisorctl("status").stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            items.append({"name": parts[0], "status": parts[1]})
    return items


def restart_program(program: str) -> None:
    if service_status(program) in _STOPPED_STATES:
        _supervisorctl("start", program)
    else:
        _supervisorctl("restart", program)


# ── 健康门禁 ────────────────────────────────────────────────────────────────

def _http_get(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return True, resp.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # 4xx/5xx 也算有响应（mcp /mcp 对裸 GET 可能 405/400——有 HTTP 回包即在线）。
        try:
            return True, exc.read(4096).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — 响应体读不出不影响判定
            return True, ""
    except (urllib.error.URLError, OSError, TimeoutError):
        return False, ""


def _health_ok(step: RestartStep) -> bool:
    ok, body = _http_get(step.health_url)
    if not ok or step.expect_status is None:
        return ok
    try:
        status = json.loads(body).get("status")
    except ValueError:
        return False
    return status in step.expect_status


def wait_healthy(step: RestartStep, *, poll_seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + step.timeout_seconds
    while time.monotonic() < deadline:
        if _health_ok(step):
            return True
        time.sleep(poll_seconds)
    return False


# ── 编排主流程 ──────────────────────────────────────────────────────────────

def _failed(status: dict, error: str) -> dict:
    return {**status, "state": "failed", "error": error, "finished_at": now_iso(),
            "services": snapshot_services()}


def run_restart(triggered_by: str) -> int:
    """依赖序重启全部后台服务。返回 0=成功，1=某步失败（已停在该步）。"""
    plan = restart_plan()
    status = {
        "state": "running",
        "triggered_by": triggered_by,
        "started_at": now_iso(),
        "finished_at": None,
        "plan": [s.program for s in plan],
        "completed": [],
        "current": None,
        "error": None,
    }
    write_status(status)
    for step in plan:
        status = {**status, "current": step.program}
        write_status(status)
        try:
            restart_program(step.program)
        except subprocess.SubprocessError as exc:
            write_status(_failed(status, f"{step.program}: supervisorctl 调用失败：{exc}"))
            return 1
        if not wait_healthy(step):
            state = service_status(step.program)
            write_status(_failed(
                status,
                f"{step.program}: {step.timeout_seconds}s 内健康检查未通过"
                f"（supervisor 状态 {state}），排查 ./logs/{step.program}.log",
            ))
            return 1
        status = {**status, "completed": [*status["completed"], step.program]}
        write_status(status)
    write_status({**status, "state": "done", "current": None,
                  "finished_at": now_iso(), "services": snapshot_services()})
    return 0


def spawn_orchestrator(triggered_by: str) -> subprocess.Popen:
    """control 端点调用：脱钩拉起编排进程（control 被重启不牵连它）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_DIR / "restart-orchestrator.log", "ab")  # noqa: SIM115 — 子进程持有到退出
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "main_control_service.restart_services",
             "--by", triggered_by],
            start_new_session=True,
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            # cwd=仓库根（/app）：python -m 按 cwd 解析 main_control_service 包。
            cwd=str(Path(__file__).resolve().parent.parent),
        )
    finally:
        log.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="依赖序重启后台服务（control→llm→mining→serving→mcp，nginx 不动）。"
    )
    parser.add_argument("--by", default="cli", help="触发者（Web 按钮传用户名）")
    args = parser.parse_args(argv)
    return run_restart(args.by)


if __name__ == "__main__":
    raise SystemExit(main())
