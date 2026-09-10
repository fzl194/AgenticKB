"""Read-only business questions; never substitute fixture truths for answers."""
import math

from harness import DOMAIN, SERVING, call


def values_match(actual, expected):
    if actual is None or isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(expected, (float, int)):
        try:
            return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=0.01)
        except (TypeError, ValueError):
            return False
    return actual == expected


def run_business_questions(ctx, report, paradigm, questions, *, search):
    results = []
    for q in questions:
        start = len(report.checks)
        try:
            _run_question(ctx, report, paradigm, q, search)
        except Exception as exc:
            # Still execute and report later questions. Never print credentials
            # or HTTP payloads in ordinary exception details.
            report.check(f"{q['id']}/error", False, type(exc).__name__)
        checks = report.checks[start:]
        results.append({"id": q["id"], "passed": bool(checks) and all(c["ok"] for c in checks),
                        "checks": len(checks)})
    return results


def _run_question(ctx, report, paradigm, q, search):
    prefix, expect, category = q["id"], q["expect"], q["category"]
    if category == "table_golden":
        response = call(ctx, "POST", f"{SERVING}/api/v1/structure/query",
                        headers=ctx.headers, json={"domain": DOMAIN, "kbId": ctx.kb_id,
                            "ref": q["asset_ref"], "query": expect["spec"]})
        response.raise_for_status()
        body = response.json()
        if "value" in expect:
            value = body["aggregate"]["value"]
            report.check(f"{prefix}/value", values_match(value, expect["value"]), repr(value))
        if "rows" in expect:
            report.check(f"{prefix}/rows", body.get("rows") == expect["rows"])
        return
    options = {"within": q["within"]} if category == "scope" else {}
    evidence = search(ctx, paradigm, q["question"], **options).get("evidence") or []
    if category == "unanswerable":
        report.check(f"{prefix}/no-evidence", not evidence,
                     "非空召回是否不支持结论需要人工核验，不能自动视为无答案")
        return
    report.check(f"{prefix}/evidence-found", bool(evidence))
    if category == "scope":
        forbidden = expect["must_not_contain"]
        leak = any(str(word) in str(e.get("content", "")) + str((e.get("source") or {}).get("section", ""))
                   for e in evidence for word in forbidden)
        report.check(f"{prefix}/scope", not leak)
    else:
        def matches(e):
            source = e.get("source") or {}
            return (not expect.get("document") or str(expect["document"]) in
                    str(source.get("file_name", "")) + str(source.get("relative_path", ""))) and (
                    not expect.get("section") or str(expect["section"]) in str(source.get("section", ""))) and (
                    not expect.get("locator_kind") or (source.get("locator") or {}).get("kind") == expect["locator_kind"])
        report.check(f"{prefix}/source", any(matches(e) for e in evidence))
    if "supports_conclusion" in expect:
        report.check(f"{prefix}/manual-review", False,
                     "证据是否支持结论需人工确认；expected 标签不能当作实际评价结果")
