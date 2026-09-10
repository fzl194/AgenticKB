"""Review 44: runnable question contracts, native dates and failure cleanup."""
import io
import sys
from pathlib import Path

import httpx
import pytest
import yaml
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness
import run_eval


def test_business_template_is_accepted():
    questions = run_eval.load_questions(str(Path(__file__).with_name("questions_business_TEMPLATE.yaml")))
    assert questions.kind == "business"
    assert len(questions) == 4


def test_date_fixture_has_native_excel_dates():
    data, _ = harness.build_xlsx_fixture()
    wb = load_workbook(io.BytesIO(data))
    assert wb.active["D2"].is_date
    assert wb.active["D3"].value.date().isoformat() == "2026-09-07"


def test_setup_records_owned_kb_before_upload_failure(monkeypatch):
    def call(ctx, method, url, **kwargs):
        request = httpx.Request(method, url)
        return httpx.Response(422 if url.endswith("/documents") else 200,
                              json={"id": "owned-kb"}, request=request)
    monkeypatch.setattr(harness, "call", call)
    ctx = harness.EvalContext()
    with pytest.raises(httpx.HTTPStatusError):
        harness.create_and_mine(ctx, "a4eval")
    assert ctx.kb_id == "owned-kb"


def test_typed_golden_values():
    from business_questions import values_match
    assert values_match("2026-09-07", "2026-09-07")
    assert not values_match("2026-09-06", "2026-09-07")
    assert values_match(1244.916666, 1244.92)
    assert not values_match(None, 0)
    assert not values_match(True, 1)


def test_same_category_questions_execute_individually():
    from business_questions import run_business_questions
    queries = []
    def search(ctx, paradigm, query, **kwargs):
        queries.append(query)
        return {"evidence": [{"source": {"file_name": query + ".pdf", "section": "Intro"}}]}
    qs = [{"id": q, "category": "source_accuracy", "question": q,
           "expect": {"document": q + ".pdf"}} for q in ["first", "second"]]
    report = run_eval.Report()
    run_business_questions(harness.EvalContext(kb_id="existing"), report, "p", qs, search=search)
    assert queries == ["first", "second"]
    assert {str(c["name"]).split("/")[0] for c in report.checks} == {"first", "second"}
    assert all(c["ok"] for c in report.checks)


def test_failed_question_does_not_skip_the_rest():
    from business_questions import run_business_questions
    def search(ctx, paradigm, query, **kwargs):
        if query == "bad":
            raise RuntimeError("unavailable")
        return {"evidence": []}
    qs = [{"id": q, "category": "unanswerable", "question": q,
           "expect": {"empty_or_unsupported": True}} for q in ["bad", "good"]]
    report = run_eval.Report()
    results = run_business_questions(harness.EvalContext(), report, "p", qs, search=search)
    assert any(c["name"] == "bad/error" and not c["ok"] for c in report.checks)
    assert any(str(c["name"]).startswith("good/") and c["ok"] for c in report.checks)
    assert results == [{"id": "bad", "passed": False, "checks": 1},
                       {"id": "good", "passed": True, "checks": 1}]


def test_main_writes_report_and_cleans_on_setup_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(run_eval, "headers_for", lambda: {})
    def create(ctx, prefix):
        ctx.kb_id = "owned"
        raise RuntimeError("setup failed")
    deleted = []
    monkeypatch.setattr(run_eval, "create_and_mine", create)
    monkeypatch.setattr(run_eval, "soft_delete", lambda ctx, kb: deleted.append(kb))
    result = run_eval.main(["--output-dir", str(tmp_path)])
    assert result == 1 and deleted == ["owned"]
    assert (tmp_path / "a4eval-report.json").exists()
    assert (tmp_path / "a4eval-report.md").exists()


def test_business_mode_never_creates_or_deletes_existing_kb(tmp_path, monkeypatch):
    path = tmp_path / "business.yaml"
    path.write_text(yaml.safe_dump({"kind": "business", "questions": [
        {"id": "q1", "category": "unanswerable", "question": "unknown",
         "expect": {"empty_or_unsupported": True}}]}), encoding="utf-8")
    monkeypatch.setattr(run_eval, "headers_for", lambda: {})
    monkeypatch.setattr(run_eval, "create_and_mine", lambda *a: pytest.fail("must not create"))
    monkeypatch.setattr(run_eval, "soft_delete", lambda *a: pytest.fail("must not delete business KB"))
    monkeypatch.setattr(run_eval, "resolve_paradigm", lambda ctx: "p")
    monkeypatch.setattr(run_eval, "search", lambda *a, **kw: {"evidence": []})
    assert run_eval.main(["--questions", str(path), "--kb-id", "existing",
                          "--output-dir", str(tmp_path)]) == 0


def test_smoke_table_uses_inspect_ref_and_compares_date_without_float(monkeypatch):
    ctx = harness.EvalContext(kb_id="k", truths={"xlsx": {"table_search": "date",
        "golden": [{"spec": {"aggregate": {"op": "max", "field": "投产日期"}},
                    "expect": "2026-09-07"}]}})
    monkeypatch.setattr(run_eval, "search", lambda *a: {"evidence": [
        {"ref": "ev_evidence", "source": {"document_ref": "doc_opaque"}}]})
    def call(ctx, method, url, **kwargs):
        request = httpx.Request(method, url)
        if url.endswith("/inspect"):
            assert kwargs["params"]["ref"] == "ev_evidence"
            return httpx.Response(200, request=request, json={"assets": [
                {"ref": "st_table", "columns": ["告警码", "投产日期"]}]})
        assert kwargs["json"]["ref"] == "st_table"
        op = kwargs["json"]["query"]["aggregate"]["op"]
        return httpx.Response(400 if op == "sum" else 200, request=request,
                              json={"aggregate": {"value": "2026-09-07"}})
    monkeypatch.setattr(run_eval, "call", call)
    report = run_eval.Report()
    run_eval.suite_table_golden(ctx, report, "p")
    assert all(check["ok"] for check in report.checks)


def test_business_table_executes_given_query_and_expected_value(monkeypatch):
    import business_questions as business
    def call(ctx, method, url, **kwargs):
        assert kwargs["json"]["ref"] == "st_actual"
        assert kwargs["json"]["kbId"] == "existing"
        assert kwargs["json"]["query"]["aggregate"]["op"] == "max"
        return httpx.Response(200, request=httpx.Request(method, url),
                              json={"aggregate": {"value": "2026-09-07"}})
    monkeypatch.setattr(business, "call", call)
    report = run_eval.Report()
    business.run_business_questions(harness.EvalContext(kb_id="existing"), report, "p", [
        {"id": "date", "category": "table_golden", "question": "latest date",
         "asset_ref": "st_actual", "expect": {"spec": {"aggregate": {"op": "max", "field": "date"}},
                                                  "value": "2026-09-07"}}], search=lambda *a: None)
    assert report.checks == [{"name": "date/value", "ok": True, "detail": "'2026-09-07'"}]


def test_business_scope_uses_requested_boundary_and_detects_leaks():
    from business_questions import run_business_questions
    def search(ctx, paradigm, query, **kwargs):
        assert kwargs["within"] == {"section_refs": ["section-1"]}
        return {"evidence": [{"content": "outside-marker", "source": {"section": "outside"}}]}
    report = run_eval.Report()
    run_business_questions(harness.EvalContext(), report, "p", [
        {"id": "scope", "category": "scope", "question": "check scope",
         "within": {"section_refs": ["section-1"]},
         "expect": {"must_not_contain": ["outside-marker"]}}], search=search)
    assert any(c["name"] == "scope/scope" and not c["ok"] for c in report.checks)


def test_expected_source_and_manual_label_are_not_treated_as_actual_results():
    from business_questions import run_business_questions
    report = run_eval.Report()
    run_business_questions(harness.EvalContext(), report, "p", [
        {"id": "source", "category": "source_accuracy", "question": "q",
         "expect": {"document": "expected.pdf", "section": "wanted", "locator_kind": "page",
                    "supports_conclusion": True}}],
        search=lambda *a, **kw: {"evidence": [{"source": {"file_name": "wrong.pdf"}}]})
    assert any(c["name"] == "source/source" and not c["ok"] for c in report.checks)
    assert any(c["name"] == "source/manual-review" and not c["ok"] for c in report.checks)


@pytest.mark.parametrize("changes", [
    {"id": ""}, {"question": ""}, {"expect": {}}, {"expect": {"unknown_assertion": True}},
    {"category": "unanswerable", "expect": {"empty_or_unsupported": False}},
    {"category": "scope", "expect": {"must_not_contain": []}},
    {"category": "table_golden", "expect": {"spec": {}, "value": 1}},
])
def test_business_contract_rejects_silently_unchecked_questions(tmp_path, changes):
    q = {"id": "q1", "category": "source_accuracy", "question": "q", "expect": {"document": "d"}}
    path = tmp_path / "questions.yaml"
    path.write_text(yaml.safe_dump({"kind": "business", "questions": [{**q, **changes}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        run_eval.load_questions(str(path))


@pytest.mark.parametrize("body", [{"error": "unavailable"}, {}, {"evidenceResponse": {}},
                                 {"evidenceResponse": {"evidence": None}}])
def test_malformed_search_response_is_not_successful_unanswerable(monkeypatch, body):
    response = httpx.Response(200, request=httpx.Request("POST", "http://test/search"), json=body)
    monkeypatch.setattr(run_eval, "call", lambda *args, **kwargs: response)
    with pytest.raises(ValueError):
        run_eval.search(harness.EvalContext(kb_id="existing"), "p", "unknown")


@pytest.mark.parametrize("expect", [{"document": None}, {"document": ""},
                                   {"section": True}, {"supports_conclusion": "true"}])
def test_source_question_requires_a_real_typed_assertion(tmp_path, expect):
    path = tmp_path / "questions.yaml"
    path.write_text(yaml.safe_dump({"kind": "business", "questions": [
        {"id": "q", "category": "source_accuracy", "question": "q", "expect": expect}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        run_eval.load_questions(str(path))
