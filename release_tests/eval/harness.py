"""A4 评测 harness（39 号 §4.2；34 号 §7 指标）——可复跑的产品 API 驱动框架.

设计约束（33 号 §14 / 34 号 A4）：
- 只走产品 API（建库/上传/挖掘/检索/结构查询/软删除）——不直写业务表；
- 唯一前缀 ``a4eval-<ts>``，finally 软删除清理（DELETE /api/kb/{id}）；
- 指标输出 JSON + Markdown：来源准确（doc/section/locator 分项）、章节范围
  越界数（必须 0）、表格黄金查询正确率、权限矩阵、性能采样；
- 题集分两层：``questions_smoke.yaml``（fixture 自证 smoke 题，跑通框架用）
  与真实业务题集（**业务输入缺口**，见 questions_business_TEMPLATE.yaml）。

用法（容器内）::

    docker exec cmkb python /app/release_tests/eval/run_eval.py \
        [--questions /app/release_tests/eval/questions_smoke.yaml]
"""
from __future__ import annotations

import io
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import yaml

MINING = "http://127.0.0.1:8901"
SERVING = "http://127.0.0.1:8081"
DOMAIN = "cloud_core_network"


def _load_auth_config() -> dict[str, Any]:
    with open(
        "/app/main_control_service/config/system/auth.yaml", encoding="utf-8"
    ) as fh:
        return yaml.safe_load(fh)


def headers_for(username: str = "admin", role: str = "admin") -> dict[str, str]:
    auth = _load_auth_config()
    secret = auth.get("internal_verify_secret")
    if not isinstance(secret, str) or not secret:
        raise RuntimeError("auth.yaml missing internal_verify_secret")
    return {"X-KB-User": username, "X-KB-Role": role, "X-Internal-Auth": secret}


@dataclass
class CallStat:
    name: str
    duration_ms: float


@dataclass
class EvalContext:
    """一次评测运行的全部状态（题目执行器读写）。"""

    kb_id: str = ""
    kb_public_id: str = ""
    username: str = "admin"
    headers: dict[str, str] = field(default_factory=dict)
    client: httpx.Client | None = None
    stats: list[CallStat] = field(default_factory=list)
    #: fixture 事实（黄金真值）：由 fixture 构造期写入
    truths: dict[str, Any] = field(default_factory=dict)


def call(ctx: EvalContext, method: str, url: str, **kw) -> httpx.Response:
    """统一调用：5xx/超时重试（远端 PG 抖动）+ 性能采样。"""
    last: httpx.Response | Exception | None = None
    for _ in range(5):
        start = time.perf_counter()
        try:
            resp = ctx.client.request(method, url, **kw)  # type: ignore[union-attr]
            ctx.stats.append(
                CallStat(url.split("?")[0], (time.perf_counter() - start) * 1000)
            )
            if resp.status_code < 500:
                return resp
            last = resp
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(3)
    if isinstance(last, Exception):
        raise last
    return last  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# fixture：md（章节范围题）+ xlsx（表格黄金题）——真值在构造期固定
# ---------------------------------------------------------------------------


def build_md_fixture() -> tuple[bytes, dict[str, Any]]:
    text = (
        "# 青岚星链运维手册\n\n"
        "## 第一章 系统概述\n\n"
        "系统采用双平面架构，主备倒换时间小于 50 毫秒。\n\n"
        "### 1.1 组网要求\n\n"
        "环网组网时单环节点数不得超过 16 个，跨环需要部署环网网关。\n\n"
        "### 1.2 环境约束\n\n"
        "机房温度要求 18-27 摄氏度，湿度 40%-60%。\n\n"
        "## 第二章 告警处理\n\n"
        "告警分为紧急、重要、次要三级。紧急告警必须在 15 分钟内响应。\n\n"
        "## 第三章 升级流程\n\n"
        "升级前必须完成配置备份与健康检查，失败时按第三章回退流程执行。\n"
    )
    truths = {
        "doc_keyword": "青岚星链",
        "section_hit": {
            "query": "环网节点数",
            "expect_section": "1.1 组网要求",
        },
        "scope": {
            # 第二章内不得命中第一章内容
            "section_title": "第二章 告警处理",
            "query": "告警",
            "must_not_contain": ["环网", "组网"],
        },
    }
    return text.encode("utf-8"), truths


def build_xlsx_fixture() -> tuple[bytes, dict[str, Any]]:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "告警表"
    rows = [
        ["告警码", "级别", "功耗(W)", "投产日期"],
        ["A1-101", "紧急", 1234.5, "2026-01-05"],
        ["A1-102", "重要", 2000, "2026-09-07"],
        ["A1-103", "次要", 500.25, "2025-12-31"],
    ]
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)

    powers = [1234.5, 2000.0, 500.25]
    truths = {
        "table_search": "A1-101",
        "golden": [
            {"spec": {"aggregate": {"op": "count"}},
             "expect": 3},
            {"spec": {"aggregate": {"op": "sum", "field": "功耗(W)"}},
             "expect": round(sum(powers), 2)},
            {"spec": {"aggregate": {"op": "avg", "field": "功耗(W)"}},
             "expect": round(sum(powers) / len(powers), 2)},
            {"spec": {"aggregate": {"op": "min", "field": "功耗(W)"}},
             "expect": min(powers)},
            {"spec": {"aggregate": {"op": "max", "field": "投产日期"}},
             "expect": "2026-09-07"},
            {"spec": {"where": [
                {"field": "功耗(W)", "op": "gt", "value": 1000},
                {"field": "投产日期", "op": "gte", "value": "2026-01-01"},
            ]}, "expect_rows": ["A1-101", "A1-102"]},
        ],
    }
    return buf.getvalue(), truths


# ---------------------------------------------------------------------------
# KB 生命周期（产品 API；唯一前缀 + 软删除清理）
# ---------------------------------------------------------------------------


def create_and_mine(ctx: EvalContext, prefix: str) -> str:
    resp = call(
        ctx, "POST", f"{MINING}/api/kb",
        headers=ctx.headers,
        json={
            "domain": DOMAIN,
            "name": f"{prefix}-{uuid.uuid4().hex[:8]}",
            "description": "A4 eval（自动创建，结束后软删除）",
        },
    )
    resp.raise_for_status()
    kb_id = resp.json()["id"]

    files = [
        ("files", ("a4eval-手册.md", *build_md_fixture()[:1], "text/markdown")),
        ("files", (
            "a4eval-告警表.xlsx", build_xlsx_fixture()[0],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )),
    ]
    up = call(
        ctx, "POST", f"{MINING}/api/kb/{kb_id}/documents",
        headers=ctx.headers, files=files,
    )
    up.raise_for_status()

    mine = call(
        ctx, "POST", f"{MINING}/api/kb/{kb_id}/mine", headers=ctx.headers, json={},
    )
    mine.raise_for_status()
    ctx.truths["run_id"] = mine.json().get("run_id") or mine.json().get("id")
    return kb_id


def wait_terminal(ctx: EvalContext, run_id: str, timeout: int = 900) -> dict[str, Any]:
    """按 run id 直查 DB 轮询终态（远端 PG 抖动期 API 可能卡死，库查更稳）。"""
    from psycopg import connect
    from psycopg.rows import dict_row

    from knowledge_mining.mining.infra.pg_config import MiningDbConfig

    cfg = MiningDbConfig()
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            with connect(cfg.conninfo, connect_timeout=8) as conn, conn.cursor(
                row_factory=dict_row
            ) as cur:
                cur.execute(
                    "SELECT status FROM mining_runs WHERE id = %s", (run_id,),
                )
                row = cur.fetchone()
                if row:
                    last = dict(row)
                    if row["status"] in {
                        "completed", "success", "partial", "failed", "error",
                    }:
                        return last
        except Exception:  # noqa: BLE001 — PG 抖动重试
            pass
        time.sleep(6)
    return last


def soft_delete(ctx: EvalContext, kb_id: str) -> None:
    if not kb_id:
        return
    try:
        call(ctx, "DELETE", f"{MINING}/api/kb/{kb_id}", headers=ctx.headers)
    except Exception:  # noqa: BLE001 — 清理尽力而为
        pass


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1))))
    return ordered[idx]
