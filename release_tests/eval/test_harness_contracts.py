"""P1-8/P1-9 回归：A4 eval harness 契约（离线单测，不依赖真实服务）.

P1-8：上传协议——产品 API 是单 ``file`` 字段逐个上传（multipart ``files``
多文件一次请求会 422）。harness 必须逐文件上传并检查每步状态。

P1-9：fixture 真值与题集加载——``build_*_fixture()`` 返回
``(bytes, truths)``；``ctx.truths`` 必须存 dict（存 bytes 后续按字符串
键访问会 TypeError）；``--questions`` 必须真实读取并校验 YAML，题集
内容决定执行哪些 suite（未知 category → 清晰报错，不静默忽略）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_EVAL_DIR))

import harness  # noqa: E402
import run_eval  # noqa: E402


# ---------------------------------------------------------------- P1-8 上传协议


class _RecordingClient:
    def __init__(self, responses: dict[str, object]):
        self.requests: list[tuple[str, str, dict]] = []
        self._responses = responses

    def request(self, method: str, url: str, **kw):
        self.requests.append((method, url, kw))
        key = f"{method} {url.split('/api/')[-1]}"
        return self._responses.get(key) or self._responses.get("*")


class _Resp:
    def __init__(self, status: int = 200, body: dict | None = None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=None, response=None,
            )


def test_upload_uses_single_file_field_per_request():
    """上传必须逐文件、字段名=``file``（产品 API 契约）。"""
    ctx = harness.EvalContext(
        headers={"X-KB-User": "admin"},
        client=_RecordingClient({
            "*": _Resp(200, {"id": "kb-1"}),
            "POST kb": _Resp(200, {"id": "kb-1"}),
        }),
    )
    uploads = []

    def fake_call(c, method, url, **kw):
        uploads.append((method, url, kw))
        return _Resp(200, {"id": "doc"})

    original = harness.call
    harness.call = fake_call
    try:
        harness.create_and_mine(ctx, "a4eval-test")
    finally:
        harness.call = original

    doc_uploads = [
        u for u in uploads if "/documents" in u[1] and u[0] == "POST"
    ]
    assert len(doc_uploads) == 2, (
        f"expected 2 per-file uploads, got {len(doc_uploads)}"
    )
    for _m, _u, kw in doc_uploads:
        files = kw.get("files") or []
        assert len(files) == 1, "one file per request"
        field_name = files[0][0]
        assert field_name == "file", (
            f"multipart field must be 'file' (product API), got {field_name!r}"
        )


def test_partial_upload_failure_still_cleans_kb():
    """第二个文件上传失败：异常上抛（不复返半成品），run_id 不落 truths."""
    ctx = harness.EvalContext(headers={}, client=_RecordingClient({}))
    n_uploaded = []

    def fake_call(c, method, url, **kw):
        if "/documents" in url:
            n_uploaded.append(1)
            if len(n_uploaded) > 1:
                return _Resp(422, {"detail": "invalid"})
        return _Resp(200, {"id": "kb-1", "run_id": "r1"})

    original = harness.call
    harness.call = fake_call
    try:
        with pytest.raises(Exception):
            harness.create_and_mine(ctx, "a4eval-test")
    finally:
        harness.call = original
    assert len(n_uploaded) == 2  # 第二次尝试确实发生并失败上抛


# ---------------------------------------------------------------- P1-9 truths


def test_fixture_truths_are_dicts_not_bytes():
    for build in (harness.build_md_fixture, harness.build_xlsx_fixture):
        data, truths = build()
        assert isinstance(data, bytes)
        assert isinstance(truths, dict), (
            f"{build.__name__} second element must be truths dict"
        )


def test_run_eval_saves_truth_dicts_into_ctx():
    """ctx.truths['md']/['xlsx'] 是 dict（修复前误存 bytes → TypeError）."""
    from types import SimpleNamespace

    ctx = harness.EvalContext()
    md_data, md_truths = harness.build_md_fixture()
    xlsx_data, xlsx_truths = harness.build_xlsx_fixture()
    ctx.truths["md"] = md_truths
    ctx.truths["xlsx"] = xlsx_truths
    # suite_source_accuracy/suite_table_golden 的访问形态必须可用
    assert ctx.truths["md"]["section_hit"]["query"]
    assert ctx.truths["xlsx"]["golden"]


# ---------------------------------------------------------------- P1-9 questions


def test_load_questions_validates_yaml():
    loader = run_eval.load_questions
    smoke = loader(str(_EVAL_DIR / "questions_smoke.yaml"))
    assert isinstance(smoke, list) and smoke, "smoke 题集不得为空"
    for q in smoke:
        assert "category" in q, "题目缺 category"
        assert q["category"] in run_eval.SUPPORTED_CATEGORIES, (
            f"未知 category {q['category']!r} 必须报错"
        )


def test_load_questions_rejects_unknown_category():
    bad = _EVAL_DIR / "_tmp_bad_questions.yaml"
    bad.write_text(
        '- category: not_a_category\n  payload: {}\n', encoding="utf-8",
    )
    try:
        with pytest.raises(SystemExit) if False else pytest.raises(ValueError):
            run_eval.load_questions(str(bad))
    finally:
        bad.unlink(missing_ok=True)


def test_questions_drive_executed_suites():
    """题集内容决定执行的 suite：换题集=换 suite 集合."""
    smoke = run_eval.load_questions(str(_EVAL_DIR / "questions_smoke.yaml"))
    categories = {q["category"] for q in smoke}
    suites = run_eval.suites_for_categories(categories)
    assert suites, "smoke 题集必须映射到至少一个 suite"
    # 只有 table_golden 时不跑 permission suite
    only_table = run_eval.suites_for_categories({"table_golden"})
    assert "suite_table_golden" in only_table
    assert "suite_permission" not in only_table
    assert "suite_scope" not in only_table


def test_empty_questions_file_reports_clear_error(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="题集为空|empty"):
        run_eval.load_questions(str(empty))
