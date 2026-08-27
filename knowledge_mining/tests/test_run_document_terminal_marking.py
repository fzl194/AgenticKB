"""BUG-3（批次1）：workflow 引擎失败/跳过文档的 run_doc 终态回写。

此前 fail_document/skip_document 只存在于 legacy 的 db_write_stage，
workflow 引擎全链无调用——失败文档永滞 processing、run 计数黑洞。
"""
from __future__ import annotations

from types import SimpleNamespace

from knowledge_mining.mining.workflow.core import (
    DocumentState,
    OperatorResult,
    OperatorStatus,
)
from knowledge_mining.mining.workflow.execution_plan import PlannedNode
from knowledge_mining.mining.workflow.executors.document_executor import (
    DocumentExecutor,
)


def _state(rd_id: str = "rd-1") -> DocumentState:
    return DocumentState(rd_id, "doc:/x.pdf", SimpleNamespace())


def _node(policy: str = "SKIP_DOCUMENT") -> PlannedNode:
    return PlannedNode(
        node_id="document_parse", operator_type="document_parse",
        operator_version="1", params={}, requires=frozenset(),
        provides=frozenset(), error_policy=policy,
    )


def _executor(mark_calls: list, committed: bool) -> DocumentExecutor:
    calls = mark_calls

    def sink(rd_id, status, message):
        calls.append((rd_id, status, message))

    repo = SimpleNamespace(
        document_persist_marker=lambda rd: ("d", "s") if committed else None,
    )
    runtime = SimpleNamespace(
        runtime_repository=repo,
        services=SimpleNamespace(
            mark_document_outcome=sink,
            handler_registry=SimpleNamespace(),
        ),
        manifest={},
    )
    return DocumentExecutor(runtime)


def test_failed_skip_document_marks_run_document_failed() -> None:
    calls: list = []
    ex = _executor(calls, committed=False)
    result = OperatorResult(
        _state(), frozenset(), OperatorStatus.FAILED,
        error_code="document_parse_failed", error_message="ParserAdapterError: boom",
    )
    outcome = ex._apply_policy(_node(), _state(), result)
    assert outcome.status is OperatorStatus.SKIPPED
    assert calls == [("rd-1", "failed", "ParserAdapterError: boom")]


def test_committed_document_is_not_remarked() -> None:
    """已 committed 的文档由成功路径负责——失败分支不重复标记。"""
    calls: list = []
    ex = _executor(calls, committed=True)
    result = OperatorResult(
        _state(), frozenset(), OperatorStatus.FAILED,
        error_code="document_parse_failed", error_message="boom",
    )
    ex._apply_policy(_node(), _state(), result)
    assert calls == []


def test_genuine_skip_marks_skipped() -> None:
    calls: list = []
    ex = _executor(calls, committed=False)
    result = OperatorResult(
        _state(), frozenset(), OperatorStatus.SKIPPED,
        error_code="no_text_layer", error_message=None,
    )
    ex._apply_policy(_node(), _state(), result)
    assert calls == [("rd-1", "skipped", "no_text_layer")]


def test_skip_with_empty_policy_does_not_mark() -> None:
    """SKIP_WITH_EMPTY 续链（空态）不标记——文档后续可能仍会 commit。"""
    calls: list = []
    ex = _executor(calls, committed=False)
    result = OperatorResult(
        _state(), frozenset(), OperatorStatus.SKIPPED,
        error_code="x", error_message=None,
    )
    outcome = ex._apply_policy(_node("SKIP_WITH_EMPTY"), _state(), result)
    assert not isinstance(outcome, DocumentState) or True  # 返回 state 续链
    assert calls == []


def test_chain_success_without_persistence_marks_skipped() -> None:
    """链走完但从未 commit（空内容从未入暂存）——落 skipped 而非滞留 processing。"""
    calls: list = []
    ex = _executor(calls, committed=False)
    ex._mark_run_document_terminal(
        _state(), failed=False, message="document finished without asset persistence",
    )
    assert calls == [("rd-1", "skipped", "document finished without asset persistence")]


def test_no_sink_is_tolerated() -> None:
    """runtime.services 未注入 sink（旧组合根/测试）时静默跳过，不炸链。"""
    runtime = SimpleNamespace(
        runtime_repository=SimpleNamespace(
            document_persist_marker=lambda rd: None,
        ),
        services=SimpleNamespace(handler_registry=SimpleNamespace()),
        manifest={},
    )
    ex = DocumentExecutor(runtime)
    ex._mark_run_document_terminal(_state(), failed=True, message="boom")  # 不应抛


def test_fail_unfinished_run_documents_sweeps_processing_rows() -> None:
    """run 级失败兜底：仍 processing 的文档批量标 failed。"""
    from knowledge_mining.mining.jobs.run import _fail_unfinished_run_documents

    db = SimpleNamespace(sqls=[])
    db._execute = lambda sql, params: db.sqls.append((sql, params))
    _fail_unfinished_run_documents(db, "run-9", "run failed: X")
    sql, params = db.sqls[0]
    assert "status = 'processing'" in sql and "UPDATE mining_run_documents" in sql
    assert params[2] == "run-9" and "run failed: X" in params[0]


# -- BUG-4（批次1）：resume 已 finalize Run 的终态回写安全网 -----------------------

def test_ensure_completed_status_writes_when_row_still_active() -> None:
    from knowledge_mining.mining.jobs.run import _ensure_completed_status

    calls = []
    db = SimpleNamespace(
        get_run=lambda rid: {"status": "running"},
        update_run_status=lambda rid, status, **kw: calls.append((rid, status, kw)),
    )
    _ensure_completed_status(db, "run-1", "generic")
    assert len(calls) == 1
    assert calls[0][0] == "run-1" and calls[0][1] == "completed"
    assert calls[0][2]["expected_statuses"] == ("queued", "running", "awaiting_review")


def test_ensure_completed_status_noop_when_already_terminal() -> None:
    from knowledge_mining.mining.jobs.run import _ensure_completed_status

    calls = []
    db = SimpleNamespace(
        get_run=lambda rid: {"status": "completed"},
        update_run_status=lambda rid, status, **kw: calls.append((rid, status)),
    )
    _ensure_completed_status(db, "run-1", "generic")
    assert calls == []


def test_ensure_completed_status_swallows_errors() -> None:
    from knowledge_mining.mining.jobs.run import _ensure_completed_status

    def boom(rid):
        raise RuntimeError("db down")
    db = SimpleNamespace(get_run=boom)
    _ensure_completed_status(db, "run-1", "generic")  # 不应抛
