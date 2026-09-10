"""Distinct contracts for fixture suites and per-question business evaluation."""
from pathlib import Path

import yaml

SMOKE_CATEGORIES = {"source_accuracy", "scope", "table_golden", "permission", "quality"}
BUSINESS_CATEGORIES = {"source_accuracy", "scope", "table_golden", "unanswerable"}


class QuestionSet(list):
    def __init__(self, questions, *, kind):
        super().__init__(questions)
        self.kind = kind


def load_questions(path: str) -> QuestionSet:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        kind = data.get("kind", "business" if "questions" in data else "smoke")
        questions = data.get("questions" if kind == "business" else "suites")
    else:
        questions = data
        kind = "business" if isinstance(data, list) and any(
            isinstance(q, dict) and "question" in q for q in data) else "smoke"
    if kind not in {"smoke", "business"}:
        raise ValueError(f"未知题集类型: {kind}")
    if not isinstance(questions, list) or not questions:
        raise ValueError("题集为空或缺少 questions/suites 列表 (empty question set)")
    allowed = BUSINESS_CATEGORIES if kind == "business" else SMOKE_CATEGORIES
    ids = set()
    for q in questions:
        if not isinstance(q, dict) or q.get("category") not in allowed:
            raise ValueError(f"未知 category；{kind} 支持: {sorted(allowed)}")
        if kind == "business":
            _validate_business(q, ids)
    return QuestionSet(questions, kind=kind)


def _validate_business(q, ids):
    for key in ("id", "question"):
        if not isinstance(q.get(key), str) or not q[key].strip():
            raise ValueError(f"业务题缺少 {key}")
    if q["id"] in ids:
        raise ValueError(f"重复题目 id: {q['id']}")
    ids.add(q["id"])
    expect = q.get("expect")
    if not isinstance(expect, dict) or not expect:
        raise ValueError(f"{q['id']}: expect 必须非空")
    expected_fields = {
        "source_accuracy": {"document", "section", "locator_kind", "supports_conclusion"},
        "scope": {"must_not_contain", "supports_conclusion"},
        "table_golden": {"spec", "value", "rows"},
        "unanswerable": {"empty_or_unsupported"},
    }[q["category"]]
    if set(expect) - expected_fields:
        raise ValueError(f"{q['id']}: 不支持的 expect 字段，不能静默跳过断言")
    if "supports_conclusion" in expect and not isinstance(expect["supports_conclusion"], bool):
        raise ValueError(f"{q['id']}: supports_conclusion 必须是布尔值")
    if q["category"] == "source_accuracy":
        fields = [expect.get(k) for k in ("document", "section", "locator_kind")]
        if any(v is not None and (not isinstance(v, str) or not v.strip()) for v in fields):
            raise ValueError(f"{q['id']}: 来源期望必须是非空字符串或 null")
        if not any(fields) and "supports_conclusion" not in expect:
            raise ValueError(f"{q['id']}: 来源题至少需要一项有效断言")
    if q["category"] == "unanswerable" and expect.get("empty_or_unsupported") is not True:
        raise ValueError(f"{q['id']}: unanswerable 要求 empty_or_unsupported=true")
    if q["category"] == "scope":
        within = q.get("within")
        if not isinstance(within, dict) or not within.get("section_refs"):
            raise ValueError(f"{q['id']}: scope 需要明确 within.section_refs")
        if not isinstance(expect.get("must_not_contain"), list):
            raise ValueError(f"{q['id']}: scope 需要 must_not_contain 列表")
    if q["category"] == "table_golden":
        if not isinstance(q.get("asset_ref"), str) or not q["asset_ref"]:
            raise ValueError(f"{q['id']}: 表格题需要 inspect 返回的 asset_ref")
        if not isinstance(expect.get("spec"), dict) or not expect["spec"]:
            raise ValueError(f"{q['id']}: 表格题需要非空 expect.spec")
        if "value" not in expect and "rows" not in expect:
            raise ValueError(f"{q['id']}: 表格题需要 expect.value 或 rows")
