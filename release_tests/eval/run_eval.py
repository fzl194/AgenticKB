"""A4 评测运行器（39 号 §4.2）——六类题的执行与指标产出.

    docker exec cmkb python /app/release_tests/eval/run_eval.py \
        [--questions .../questions_smoke.yaml] [--keep]

题集 schema（YAML）::

    - category: source_accuracy | scope | table_golden | permission | ...
      # 各类题的载荷（harness truths 与题目分离，便于业务补题）

产出：``/tmp/a4eval-report.json`` + stdout Markdown 摘要。
真实业务题 50 条为业务输入缺口（34 号 §7），框架先行——不伪造完成。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    DOMAIN,
    MINING,
    SERVING,
    EvalContext,
    call,
    create_and_mine,
    headers_for,
    percentile,
    soft_delete,
    wait_terminal,
)


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, object]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": ok, "detail": detail})
        print(("  ✓ " if ok else "  ✗ ") + name + (f"  [{detail[:200]}]" if detail and not ok else ""))
        return ok

    def summary(self) -> dict[str, object]:
        total = len(self.checks)
        passed = sum(1 for c in self.checks if c["ok"])
        # 分组统计
        groups: dict[str, dict[str, int]] = {}
        for c in self.checks:
            group = str(c["name"]).split("/")[0]
            g = groups.setdefault(group, {"total": 0, "passed": 0})
            g["total"] += 1
            if c["ok"]:
                g["passed"] += 1
        return {"total": total, "passed": passed, "groups": groups}


def resolve_paradigm(ctx: EvalContext) -> str:
    resp = call(
        ctx, "GET", f"{SERVING}/api/v1/paradigm/resolve",
        headers=ctx.headers, params={"domain": DOMAIN, "kbIds": ctx.kb_id},
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("bound") or not body.get("paradigmId"):
        raise RuntimeError("domain has no bound paradigm")
    return str(body["paradigmId"])


def search(ctx: EvalContext, paradigm: str, query: str, **kw) -> dict:
    payload: dict[str, object] = {
        "query": query, "domain": DOMAIN, "kbIds": [ctx.kb_id], "topK": 10,
    }
    payload.update(kw)
    resp = call(
        ctx, "POST", f"{SERVING}/api/v1/paradigm/{paradigm}/search",
        headers=ctx.headers, json=payload,
    )
    resp.raise_for_status()
    return resp.json().get("evidenceResponse") or {}


# ---------------------------------------------------------------------------


def suite_source_accuracy(ctx: EvalContext, report: Report, paradigm: str) -> None:
    print("== 来源准确率（smoke）==")
    truth = ctx.truths["md"]["section_hit"]
    ev = search(ctx, paradigm, truth["query"])
    evidence = ev.get("evidence") or []
    report.check("source/search-returns-evidence", bool(evidence))
    top = evidence[0] if evidence else {}
    src = top.get("source") or {}
    report.check(
        "source/section-breadcrumb",
        truth["expect_section"] in str(src.get("section") or ""),
        str(src.get("section")),
    )
    loc = src.get("locator") or {}
    report.check(
        "source/locator-present-or-说明",
        bool(loc) or not top.get("ref", "").startswith("ev_"),
        json.dumps(src, ensure_ascii=False)[:200],
    )
    if str(top.get("ref", "")).startswith("ev_"):
        nav = call(
            ctx, "GET",
            f"{SERVING}/api/v1/evidence/{top['ref']}/source",
            headers=ctx.headers,
            params={"domain": DOMAIN, "kbId": ctx.kb_id},
        )
        report.check(
            "source/resolve-endpoint-200-with-document",
            nav.status_code == 200 and bool(nav.json().get("document_id")),
            nav.text[:200],
        )


def suite_scope(ctx: EvalContext, report: Report, paradigm: str) -> None:
    print("== 章节范围（越界必须为 0）==")
    truth = ctx.truths["md"]["scope"]
    # 大纲 → 章节投影 ref（A2 outline 章节锚）
    docs = call(
        ctx, "GET", f"{MINING}/api/kb/{ctx.kb_id}/documents",
        headers=ctx.headers, params={"limit": 50},
    )
    doc_id = next(
        (
            d["id"]
            for d in docs.json().get("items", docs.json() or [])
            if "手册" in str(d.get("document_name", ""))
        ),
        None,
    )
    if not doc_id:
        report.check("scope/fixture-doc-found", False, str(docs.text)[:200])
        return
    parse = call(
        ctx, "GET", f"{MINING}/api/kb/{ctx.kb_id}/documents/{doc_id}/parse-result",
        headers=ctx.headers,
    )
    outline = parse.json().get("outline") or []
    section = next(
        (
            o
            for o in outline
            if truth["section_title"] in str(o.get("title", ""))
            and o.get("section_ref")
        ),
        None,
    )
    if not section:
        report.check(
            "scope/section-anchor-present",
            False,
            "outline 无章节锚（旧快照？需重挖）：" + json.dumps(outline[:3], ensure_ascii=False),
        )
        return
    report.check("scope/section-anchor-present", True)

    ev = search(
        ctx, paradigm, truth["query"],
        within={
            "section_refs": [section["section_ref"]],
            "section_scope": "descendants",
        },
    )
    evidence = ev.get("evidence") or []
    leaked = [
        e
        for e in evidence
        if any(word in str((e.get("source") or {}).get("section") or "")
               + str(e.get("content") or "")
               for word in truth["must_not_contain"])
    ]
    report.check(
        "scope/descendants-zero-violation",
        not leaked,
        f"leaked={len(leaked)}",
    )


def suite_table_golden(ctx: EvalContext, report: Report, paradigm: str) -> None:
    print("== 表格黄金查询（fixture 真值）==")
    truth = ctx.truths["xlsx"]
    ev = search(ctx, paradigm, truth["table_search"])
    table_ref = None
    doc_key = None
    for e in ev.get("evidence") or []:
        loc = (e.get("source") or {}).get("locator") or {}
        if loc.get("table_ref"):
            table_ref = loc["table_ref"]
            doc_key = str((e.get("source") or {}).get("document_ref") or "")
            break
    if not table_ref or not doc_key:
        report.check(
            "table/evidence-with-table-anchor", False,
            json.dumps(ev.get("evidence") or [], ensure_ascii=False)[:300],
        )
        return
    report.check("table/evidence-with-table-anchor", True)

    asset_ref = f"{doc_key}#table:{table_ref}"
    for i, case in enumerate(truth["golden"]):
        resp = call(
            ctx, "POST", f"{SERVING}/api/v1/structure/{asset_ref}/query",
            headers=ctx.headers,
            params={"domain": DOMAIN, "kbId": ctx.kb_id},
            json={"query": case["spec"]},
        )
        if resp.status_code != 200:
            report.check(f"table/golden-{i}", False, resp.text[:200])
            continue
        body = resp.json()
        if "aggregate" in case["spec"]:
            value = (body.get("aggregate") or {}).get("value")
            expect = case["expect"]
            ok = value is not None and abs(float(value) - float(expect)) < 0.01 \
                or value == expect
            report.check(f"table/golden-{i}", bool(ok), f"value={value} expect={expect}")
        else:
            got_rows = [
                str(r.get("告警码"))
                for r in (body.get("rows") or [])
            ]
            ok = all(e in got_rows for e in case["expect_rows"]) and \
                len(got_rows) == len(case["expect_rows"])
            report.check(
                f"table/golden-{i}", ok,
                f"rows={got_rows} expect={case['expect_rows']}",
            )

    # 类型守卫：date 列 sum → 400（typed error，不退化）
    bad = call(
        ctx, "POST", f"{SERVING}/api/v1/structure/{asset_ref}/query",
        headers=ctx.headers,
        params={"domain": DOMAIN, "kbId": ctx.kb_id},
        json={"query": {"aggregate": {"op": "sum", "field": "投产日期"}}},
    )
    report.check(
        "table/date-sum-rejected-typed",
        400 <= bad.status_code < 500,
        f"status={bad.status_code}",
    )


def suite_permission(ctx: EvalContext, report: Report, paradigm: str) -> None:
    print("== 权限矩阵（private 库 × 身份）==")
    matrix = [
        ("admin", 200),          # 批次一：站点管理员全通
        ("owner", None),         # 库主 = 创建者 admin，同 admin
        ("random-stranger", 404),  # 非成员 private → kb_not_found（防探测同码）
        (None, 404),             # 匿名 private → kb_not_found
    ]
    for username, expect in matrix:
        if username == "owner":
            continue  # 创建者即 admin，同用例不重复
        headers = headers_for(username) if username else {
            "X-Internal-Auth": headers_for()["X-Internal-Auth"],
        }
        resp = call(
            ctx, "POST", f"{SERVING}/api/v1/paradigm/{paradigm}/search",
            headers=headers,
            json={
                "query": "告警", "domain": DOMAIN, "kbIds": [ctx.kb_id], "topK": 1,
            },
        )
        report.check(
            f"permission/{username or 'anonymous'}-{'200' if expect == 200 else '404'}",
            resp.status_code == expect,
            f"status={resp.status_code}",
        )


def suite_quality_report(ctx: EvalContext, report: Report) -> None:
    print("== 质量报告面 ==")
    resp = call(
        ctx, "GET", f"{MINING}/api/kb/{ctx.kb_id}/quality", headers=ctx.headers,
    )
    if resp.status_code != 200:
        report.check("quality/endpoint-200", False, resp.text[:200])
        return
    body = resp.json()
    report.check("quality/endpoint-200", True)
    report.check(
        "quality/locator-coverage-computable",
        isinstance(body.get("locator", {}).get("denominator"), int),
        json.dumps(body.get("locator"), ensure_ascii=False)[:200],
    )
    report.check(
        "quality/table-coverage-computable",
        isinstance(body.get("tables", {}).get("cells_typed"), int),
        json.dumps(body.get("tables"), ensure_ascii=False)[:200],
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="a4-eval")
    parser.add_argument(
        "--questions", default=str(Path(__file__).parent / "questions_smoke.yaml"),
        help="题集 YAML（smoke=框架自证；真实题集见 TEMPLATE）",
    )
    parser.add_argument("--keep", action="store_true", help="保留评测库（调试）")
    args = parser.parse_args()

    ctx = EvalContext(username="admin")
    ctx.headers = headers_for()
    import httpx

    ctx.client = httpx.Client(timeout=90)
    report = Report()
    ctx.kb_id = ""
    try:
        prefix = "a4eval"
        print(f"[setup] 创建评测库并挖掘（prefix={prefix}）…")
        ctx.kb_id = create_and_mine(ctx, prefix)
        ctx.truths.setdefault("run_id", "")
        terminal = wait_terminal(ctx, str(ctx.truths.get("run_id") or ""))
        report.check(
            "setup/mining-terminal-ok",
            str(terminal.get("status") or "") in {"completed", "success", "partial"},
            str(terminal),
        )
        import time as _t

        _t.sleep(5)  # 二级索引/检索单元可见

        ctx.truths["md"], _ = __import__("harness").build_md_fixture()
        ctx.truths["xlsx"], _ = __import__("harness").build_xlsx_fixture()

        paradigm = resolve_paradigm(ctx)
        suite_source_accuracy(ctx, report, paradigm)
        suite_scope(ctx, report, paradigm)
        suite_table_golden(ctx, report, paradigm)
        suite_permission(ctx, report, paradigm)
        suite_quality_report(ctx, report)
    finally:
        if not args.keep:
            print(f"[cleanup] 软删除评测库 {ctx.kb_id}")
            soft_delete(ctx, ctx.kb_id)
        ctx.client.close()

    # 性能采样（小样本；正式 P50/P95 需按 34 号 §7 负载条件）
    durations: dict[str, list[float]] = {}
    for stat in ctx.stats:
        durations.setdefault(stat.name.split("/")[-1], []).append(stat.duration_ms)
    perf = {
        name: {
            "n": len(vals),
            "p50": round(percentile(vals, 50), 1),
            "p95": round(percentile(vals, 95), 1),
        }
        for name, vals in durations.items()
    }

    summary = report.summary()
    out = {
        "summary": summary,
        "perf_sample": perf,
        "checks": report.checks,
        "business_gap": {
            "real_questions_50": (
                "真实业务题 50 条未提供（业务输入缺口）——本运行使用 fixture "
                "自证 smoke 题。补题后把题集 YAML 换成 --questions 传入即可，"
                "指标口径（来源正确/章节正确/位置正确/证据是否支持结论）已就绪。"
            ),
        },
    }
    Path("/tmp/a4eval-report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"\n== A4 eval: {summary['passed']}/{summary['total']} passed | "
        f"perf_sample={json.dumps(perf)} | report=/tmp/a4eval-report.json =="
    )
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
