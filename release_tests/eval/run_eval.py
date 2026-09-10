"""A4 评测运行器（39 号 §4.2）——题集驱动的执行与指标产出.

    docker exec cmkb python /app/release_tests/eval/run_eval.py \
        [--questions .../questions_smoke.yaml] [--keep]

题集 schema（YAML）::

    - category: source_accuracy | scope | table_golden | permission | quality
      # 各类题的载荷（harness truths 与题目分离，便于业务补题）

产出：``/tmp/a4eval-report.json`` + ``/tmp/a4eval-report.md`` +
stdout 摘要（含 P50/P95/P99）。
真实业务题 50 条为业务输入缺口（34 号 §7），框架先行——不伪造完成。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

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

#: 题目 category → 执行 suite 的唯一映射（未知 category 拒绝）
SUPPORTED_CATEGORIES = {
    "source_accuracy", "scope", "table_golden", "permission", "quality", "unanswerable",
}

_CATEGORY_SUITES = {
    "source_accuracy": "suite_source_accuracy",
    "scope": "suite_scope",
    "table_golden": "suite_table_golden",
    "permission": "suite_permission",
    "quality": "suite_quality_report",
}


from question_sets import load_questions  # noqa: E402
from business_questions import run_business_questions, values_match  # noqa: E402


def suites_for_categories(categories: set[str]) -> list[str]:
    """题集内容决定执行的 suite（保持稳定执行顺序）."""
    order = [
        "suite_source_accuracy", "suite_scope", "suite_table_golden",
        "suite_permission", "suite_quality_report",
    ]
    wanted = {_CATEGORY_SUITES[c] for c in categories}
    return [s for s in order if s in wanted]


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
    body = resp.json()
    result = body.get("evidenceResponse") if isinstance(body, dict) else None
    if not isinstance(result, dict) or not isinstance(result.get("evidence"), list):
        raise ValueError("invalid search response: evidenceResponse.evidence must be a list")
    return result


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
    document_body = docs.json()
    document_items = document_body.get("items", []) if isinstance(document_body, dict) else document_body
    doc_id = next(
        (
            d["id"]
            for d in document_items
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
    report.check("scope/evidence-found", bool(evidence))
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
    asset_ref = None
    for e in ev.get("evidence") or []:
        if not e.get("ref"):
            continue
        inspected = call(ctx, "GET", f"{SERVING}/api/v1/structure/inspect",
                         headers=ctx.headers,
                         params={"ref": e["ref"], "domain": DOMAIN, "kbId": ctx.kb_id})
        inspected.raise_for_status()
        assets = inspected.json().get("assets") or []
        matching = [a for a in assets if "告警码" in (a.get("columns") or []) and a.get("ref")]
        if len(matching) == 1:
            asset_ref = matching[0]["ref"]
            break
    if not asset_ref:
        report.check(
            "table/evidence-with-table-anchor", False,
            json.dumps(ev.get("evidence") or [], ensure_ascii=False)[:300],
        )
        return
    report.check("table/evidence-with-table-anchor", True)

    for i, case in enumerate(truth["golden"]):
        resp = call(
            ctx, "POST", f"{SERVING}/api/v1/structure/query",
            headers=ctx.headers,
            params={},
            json={
                "ref": asset_ref, "query": case["spec"],
                "domain": DOMAIN, "kbId": ctx.kb_id,
            },
        )
        if resp.status_code != 200:
            report.check(f"table/golden-{i}", False, resp.text[:200])
            continue
        body = resp.json()
        if "aggregate" in case["spec"]:
            value = (body.get("aggregate") or {}).get("value")
            expect = case["expect"]
            ok = values_match(value, expect)
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
        ctx, "POST", f"{SERVING}/api/v1/structure/query",
        headers=ctx.headers,
        json={
            "ref": asset_ref, "domain": DOMAIN, "kbId": ctx.kb_id,
            "query": {"aggregate": {"op": "sum", "field": "投产日期"}},
        },
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


# ---------------------------------------------------------------------------


def _perf_summary(ctx: EvalContext) -> dict[str, dict[str, float]]:
    """性能采样 → 每端点 P50/P95/P99（39 号 §4.2 输出要求）."""
    durations: dict[str, list[float]] = {}
    for stat in ctx.stats:
        durations.setdefault(stat.name.split("/")[-1], []).append(stat.duration_ms)
    return {
        name: {
            "n": len(vals),
            "p50": round(percentile(vals, 50), 1),
            "p95": round(percentile(vals, 95), 1),
            "p99": round(percentile(vals, 99), 1),
        }
        for name, vals in durations.items()
    }


def _markdown_report(out: dict, questions_path: str) -> str:
    summary = out["summary"]
    lines = [
        "# A4 评测报告",
        "",
        f"- 题集：`{questions_path}`",
        f"- 自动检查：**{summary['passed']}/{summary['total']}** passed",
        "",
        "| 分组 | 通过/总数 |",
        "|---|---|",
    ]
    for group, g in summary["groups"].items():
        lines.append(f"| {group} | {g['passed']}/{g['total']} |")
    question_results = out.get("question_results") or []
    if question_results:
        passed = sum(q["passed"] for q in question_results)
        lines += ["", f"业务题通过：{passed}/{len(question_results)}（待人工核验不计为通过）"]
    lines += ["", "## 性能采样（ms）", "", "| 端点 | n | P50 | P95 | P99 |", "|---|---|---|---|---|"]
    for name, perf in out["perf_sample"].items():
        lines.append(
            f"| {name} | {perf['n']} | {perf['p50']} | {perf['p95']} | {perf['p99']} |"
        )
    lines += ["", "## 明细", ""]
    for c in out["checks"]:
        mark = "✓" if c["ok"] else "✗"
        lines.append(f"- {mark} {c['name']}" + (f" — {c['detail']}" if not c["ok"] else ""))
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    import tempfile
    parser = argparse.ArgumentParser(prog="a4-eval")
    parser.add_argument("--questions", default=str(Path(__file__).parent / "questions_smoke.yaml"))
    parser.add_argument("--keep", action="store_true", help="保留本次创建的 smoke 库")
    parser.add_argument("--kb-id", help="business 模式必填：已存在的只读评测知识库")
    parser.add_argument("--output-dir", default=tempfile.gettempdir())
    args = parser.parse_args(argv)
    questions = load_questions(args.questions)
    if questions.kind == "business" and not args.kb_id:
        parser.error("business 题集必须提供 --kb-id；不会创建替代业务资料的 fixture 库")
    if questions.kind == "smoke" and args.kb_id:
        parser.error("smoke 自建临时库，不接受 --kb-id")
    categories = {q["category"] for q in questions}
    ctx, report = EvalContext(username="admin"), Report()
    question_results = []
    ctx.client = httpx.Client(timeout=90)
    try:
        ctx.headers = headers_for()
        if questions.kind == "business":
            ctx.kb_id = args.kb_id
            paradigm = resolve_paradigm(ctx)
            question_results = run_business_questions(ctx, report, paradigm, questions, search=search)
        else:
            ctx.kb_id = create_and_mine(ctx, "a4eval")
            terminal = wait_terminal(ctx, str(ctx.truths.get("run_id") or ""))
            ready = str(terminal.get("status") or "") in {"completed", "success", "partial"}
            report.check("setup/mining-terminal-ok", ready, str(terminal))
            if not ready:
                raise RuntimeError("smoke mining did not complete")
            paradigm = resolve_paradigm(ctx)
            for name in suites_for_categories(categories):
                try:
                    if name == "suite_quality_report":
                        suite_quality_report(ctx, report)
                    else:
                        globals()[name](ctx, report, paradigm)
                except Exception as exc:
                    report.check(f"{name}/error", False, type(exc).__name__)
    except Exception as exc:
        report.check("runtime/error", False, type(exc).__name__)
    finally:
        try:
            if questions.kind == "smoke" and ctx.kb_id and not args.keep:
                soft_delete(ctx, ctx.kb_id)
        except Exception as exc:
            report.check("cleanup/error", False, type(exc).__name__)
        finally:
            ctx.client.close()
    summary = report.summary()
    out = {
        "summary": summary,
        "questions": {"path": args.questions, "count": len(questions),
                      "mode": questions.kind, "categories": sorted(categories)},
        "perf_sample": _perf_summary(ctx), "checks": report.checks,
        "question_results": question_results,
        "business_gap": {"note": (
            "本次为 fixture smoke，不作为真实业务题验收。" if questions.kind == "smoke"
            else "已逐题执行；证据支持结论仍需人工确认。题数及自动通过不等于业务放量验收。")},
    }
    directory = Path(args.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "a4eval-report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "a4eval-report.md").write_text(
        _markdown_report(out, args.questions), encoding="utf-8")
    print(f"A4 {questions.kind}: {summary['passed']}/{summary['total']} passed; report={directory}")
    return 0 if summary["total"] and summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
