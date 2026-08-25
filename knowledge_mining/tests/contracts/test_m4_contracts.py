"""M4.1 契约层测试（RED 先行）：

1. Parse Run 状态机补 ``SUPERSEDED`` 终态（SRS §9.5「解析期间文档被编辑 →
   提交前 revision 校验失败，Run 标记 SUPERSEDED，不创建 Snapshot」；
   §9.2 状态图未画该态——按 D-015「显式枚举」原则补边）。
2. ``ParsePlan``（SRS §4.5/§4.9）：primary + 有序 fallback + 尝试预算
   （防死循环：max backend attempts / repair attempts / 总时长）。
3. ``snapshot_fingerprint``（SRS §8.3A）：domain + 内容 hash + 管线指纹
   → Snapshot 唯一身份。
4. ``SnapshotRecord`` / ``SnapshotRepository`` Protocol（WP9）：READY 快照
   的冻结投影与幂等提交语义。
"""
from __future__ import annotations

import dataclasses

import pytest

from knowledge_mining.mining.contracts.state_machines import (
    VALID_PARSE_RUN_STATES,
    assert_transition,
    is_legal_transition,
    is_terminal,
)


# ---------------------------------------------------------------------------
# 1. SUPERSEDED 终态
# ---------------------------------------------------------------------------


def test_parse_run_superseded_is_valid_state() -> None:
    assert "SUPERSEDED" in VALID_PARSE_RUN_STATES


def test_evaluating_to_superseded_is_legal() -> None:
    """pre-commit revision check 失败发生在评估之后、提交之前."""
    assert is_legal_transition("parse_run", "EVALUATING", "SUPERSEDED")


def test_superseded_is_terminal() -> None:
    assert is_terminal("parse_run", "SUPERSEDED")


def test_succeeded_cannot_become_superseded() -> None:
    """已成功的 Run 不回退为过期（过期只发生在提交前）."""
    with pytest.raises(Exception):
        assert_transition("parse_run", "SUCCEEDED", "SUPERSEDED")


# ---------------------------------------------------------------------------
# 2. ParsePlan
# ---------------------------------------------------------------------------


def test_parse_plan_backend_chain_and_defaults() -> None:
    from knowledge_mining.mining.contracts.parse_plan import AttemptBudget, ParsePlan

    plan = ParsePlan(
        plan_id="plan_1",
        primary_parser_id="native_pdf",
        fallback_parser_ids=("docling_standard",),
    )
    assert plan.backend_chain() == ("native_pdf", "docling_standard")
    # 预算默认值存在且为正（SRS §4.9 防死循环）。
    assert plan.budget.max_backend_attempts == 3
    assert plan.budget.max_repair_attempts >= 0
    assert plan.budget.max_duration_seconds > 0
    assert isinstance(plan.budget, AttemptBudget)


def test_parse_plan_is_frozen() -> None:
    from knowledge_mining.mining.contracts.parse_plan import ParsePlan

    plan = ParsePlan(plan_id="p", primary_parser_id="a")
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.primary_parser_id = "b"  # type: ignore[misc]


def test_parse_plan_rejects_empty_primary() -> None:
    from knowledge_mining.mining.contracts.parse_plan import ParsePlan

    with pytest.raises(ValueError, match="primary"):
        ParsePlan(plan_id="p", primary_parser_id="")


def test_parse_plan_rejects_unknown_quality_profile() -> None:
    from knowledge_mining.mining.contracts.parse_plan import ParsePlan

    with pytest.raises(ValueError, match="quality profile"):
        ParsePlan(
            plan_id="p", primary_parser_id="a", quality_profile="experimental"
        )


def test_parse_plan_rejects_duplicate_backends() -> None:
    from knowledge_mining.mining.contracts.parse_plan import ParsePlan

    with pytest.raises(ValueError, match="duplicate"):
        ParsePlan(
            plan_id="p",
            primary_parser_id="a",
            fallback_parser_ids=("b", "a"),
        )


def test_parse_plan_rejects_chain_longer_than_budget() -> None:
    """后端链长度不得超出尝试预算（链本身就不会被执行完）."""
    from knowledge_mining.mining.contracts.parse_plan import (
        AttemptBudget,
        ParsePlan,
    )

    with pytest.raises(ValueError, match="budget"):
        ParsePlan(
            plan_id="p",
            primary_parser_id="a",
            fallback_parser_ids=("b", "c"),
            budget=AttemptBudget(max_backend_attempts=2),
        )


def test_parse_plan_rejects_non_positive_budget() -> None:
    from knowledge_mining.mining.contracts.parse_plan import (
        AttemptBudget,
        ParsePlan,
    )

    with pytest.raises(ValueError):
        AttemptBudget(max_backend_attempts=0)
    with pytest.raises(ValueError):
        ParsePlan(
            plan_id="p",
            primary_parser_id="a",
            budget=AttemptBudget(max_duration_seconds=0),
        )


# ---------------------------------------------------------------------------
# 3. snapshot_fingerprint
# ---------------------------------------------------------------------------


def test_snapshot_fingerprint_deterministic_and_shaped() -> None:
    from knowledge_mining.mining.contracts.snapshot_store import (
        snapshot_fingerprint,
    )

    a = snapshot_fingerprint(
        domain="default",
        source_raw_hash="raw-1",
        effective_pipeline_fingerprint="pipe-abc",
    )
    b = snapshot_fingerprint(
        domain="default",
        source_raw_hash="raw-1",
        effective_pipeline_fingerprint="pipe-abc",
    )
    assert a == b
    assert a.startswith("snap-") and len(a) >= 8


def test_snapshot_fingerprint_sensitive_to_each_component() -> None:
    from knowledge_mining.mining.contracts.snapshot_store import (
        snapshot_fingerprint,
    )

    base = snapshot_fingerprint(
        domain="d1", source_raw_hash="raw-1",
        effective_pipeline_fingerprint="pipe-1",
    )
    assert base != snapshot_fingerprint(  # domain
        domain="d2", source_raw_hash="raw-1",
        effective_pipeline_fingerprint="pipe-1",
    )
    assert base != snapshot_fingerprint(  # 内容
        domain="d1", source_raw_hash="raw-2",
        effective_pipeline_fingerprint="pipe-1",
    )
    assert base != snapshot_fingerprint(  # 解析管线
        domain="d1", source_raw_hash="raw-1",
        effective_pipeline_fingerprint="pipe-2",
    )
    # compiler 指纹参与（M5 起切片策略变化 → 新 Snapshot，SRS §8.3A）。
    assert base != snapshot_fingerprint(
        domain="d1", source_raw_hash="raw-1",
        effective_pipeline_fingerprint="pipe-1", compiler_fingerprint="comp-9",
    )


# ---------------------------------------------------------------------------
# 4. SnapshotRecord / SnapshotRepository
# ---------------------------------------------------------------------------


def _make_snapshot(**overrides):
    from knowledge_mining.mining.contracts.snapshot_store import SnapshotRecord

    defaults = dict(
        id="snap_1",
        domain="default",
        snapshot_fingerprint="snap-abc",
        raw_content_hash="raw-1",
        normalized_content_hash="raw-1",
        mime_type="application/pdf",
        parse_ir_storage_object_id="so_1",
        parse_ir_schema_version="0.2",
        parser_fingerprint="native_pdf@2.0.0",
        quality_status="PASS",
        created_by_run_id="parse_1",
        created_at="2026-08-18T00:00:00+00:00",
    )
    defaults.update(overrides)
    return SnapshotRecord(**defaults)


def test_snapshot_record_accepts_pass_or_warn_only() -> None:
    """FAIL 不产生 Snapshot（M4 退出条件）——记录层直接拒绝."""
    assert _make_snapshot(quality_status="WARN").quality_status == "WARN"
    with pytest.raises(ValueError):
        _make_snapshot(quality_status="FAIL")


def test_snapshot_record_lifecycle_default_ready_and_validated() -> None:
    assert _make_snapshot().lifecycle_status == "READY"
    with pytest.raises(ValueError):
        _make_snapshot(lifecycle_status="BROKEN")
    # READY 之外的合法生命周期值。
    assert _make_snapshot(lifecycle_status="DEPRECATED").lifecycle_status == (
        "DEPRECATED"
    )


def test_snapshot_repository_protocol_shape() -> None:
    """memory fake 可满足 Protocol（runtime_checkable）."""
    from knowledge_mining.mining.contracts.snapshot_store import (
        SnapshotCommitResult,
        SnapshotRepository,
        SnapshotSourceLink,
    )

    class _FakeRepo:
        async def commit(self, snapshot, link):  # noqa: ANN001
            return SnapshotCommitResult(snapshot=snapshot, created=True)

        async def get(self, snapshot_id):  # noqa: ANN001
            return None

        async def find_by_fingerprint(self, domain, fingerprint):  # noqa: ANN001
            return None

        async def latest_for_document(self, document_id, domain):  # noqa: ANN001
            return None

        async def mark_lifecycle(self, snapshot_id, lifecycle_status):  # noqa: ANN001
            raise NotImplementedError

    assert isinstance(_FakeRepo(), SnapshotRepository)
    # link 携带来源对象与内容版本（SRS §8.3A snapshot_links 新列）。
    link = SnapshotSourceLink(
        id="link_1",
        document_id="doc1",
        document_snapshot_id="snap_1",
        source_storage_object_id="so_src",
        source_content_revision=3,
    )
    assert link.source_content_revision == 3


def test_snapshot_commit_result_carries_created_flag() -> None:
    from knowledge_mining.mining.contracts.snapshot_store import (
        SnapshotCommitResult,
    )

    snap = _make_snapshot()
    result = SnapshotCommitResult(snapshot=snap, created=False,
                                  reused_reason="fingerprint_hit")
    assert result.created is False
    assert result.reused_reason == "fingerprint_hit"
