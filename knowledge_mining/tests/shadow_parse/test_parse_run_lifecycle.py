"""M4.3 WP7 切片：Parse Run 完整状态机接线。

- ParseRunRecord.status 接受 ``contracts/state_machines.py`` 的全部状态
  （单一事实源），含 SUPERSEDED；新增 ``snapshot_id`` 关联列。
- ``set_status`` 按 LEGAL_TRANSITIONS 前进；非法跳转（终态回退/跳阶段）
  抛 ``IllegalTransition``。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

from knowledge_mining.mining.contracts.state_machines import (
    IllegalTransition,
    VALID_PARSE_RUN_STATES,
)
from knowledge_mining.mining.shadow_parse.contracts import ParseRunRecord
from knowledge_mining.mining.shadow_parse.repositories_memory import MemoryParseRunRepository


def _run(**overrides) -> ParseRunRecord:
    defaults = dict(
        id="run_1",
        document_id="doc1",
        source_storage_object_id="so1",
        source_raw_hash="raw-1",
        source_content_revision=1,
        parser_id="native_pdf",
        parser_fingerprint="fp-1",
        status="QUEUED",
        started_at="2026-08-18T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ParseRunRecord(**defaults)


async def test_run_record_accepts_all_state_machine_statuses() -> None:
    repo = MemoryParseRunRepository()
    for status in sorted(VALID_PARSE_RUN_STATES):
        await repo.upsert(_run(id=f"r-{status}", status=status))


async def test_run_record_rejects_unknown_status() -> None:
    repo = MemoryParseRunRepository()
    with pytest.raises(ValueError, match="status"):
        await repo.upsert(_run(status="EXPLODED"))


async def test_run_record_has_snapshot_id_field() -> None:
    assert _run(snapshot_id="snap_1").snapshot_id == "snap_1"
    assert _run().snapshot_id is None


async def test_set_status_advances_along_legal_spine() -> None:
    repo = MemoryParseRunRepository()
    await repo.upsert(_run(status="EVALUATING"))
    record = await repo.set_status("run_1", "SUCCEEDED", snapshot_id="snap_9")
    assert record.status == "SUCCEEDED"
    assert record.snapshot_id == "snap_9"
    assert (await repo.get("run_1")).status == "SUCCEEDED"


async def test_set_status_rejects_illegal_jump() -> None:
    repo = MemoryParseRunRepository()
    await repo.upsert(_run(status="QUEUED"))
    with pytest.raises(IllegalTransition):
        await repo.set_status("run_1", "SUCCEEDED")  # 跳过整条主干


async def test_set_status_rejects_terminal_regression() -> None:
    repo = MemoryParseRunRepository()
    await repo.upsert(_run(status="SUCCEEDED"))
    with pytest.raises(IllegalTransition):
        await repo.set_status("run_1", "EVALUATING")


async def test_set_status_unknown_run_raises() -> None:
    repo = MemoryParseRunRepository()
    with pytest.raises(KeyError):
        await repo.set_status("missing", "SUCCEEDED")
