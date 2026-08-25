"""In-memory fake repositories for the Shadow Parse layer (M2 → M4).

Implements ``ParseRunRepository`` 与 ``ParseAttemptRepository``
（``shadow_parse/contracts.py``），backed by plain ``dict`` stores，供服务
测试与本地开发使用——整套解析链路无需 PostgreSQL 即可跑通
（ADR-0003 D-006 / D-022，与 file_management 的 ``repositories_memory``
同风格）。

幂等模型（SRS §2.2）：
- 唯一键 ``(document_id, source_raw_hash, parser_fingerprint)`` 维护二级索引。
- ``upsert`` 命中唯一键时保留既有行 ``id``（稳定身份）：双 SUCCEEDED 视为
  等价重复写入直接返回原行；否则（FAILED 重跑翻转等）用新记录覆盖原行。
- ``set_status`` 是执行内状态推进（``assert_transition`` 校验），与 upsert
  的跨执行覆盖语义正交（M4）。
"""
from __future__ import annotations

from dataclasses import replace

from knowledge_mining.mining.contracts.state_machines import (
    IllegalTransition,
    assert_transition,
)
from knowledge_mining.mining.shadow_parse.contracts import (
    ParseAttemptRecord,
    ParseRunRecord,
)

_IdemKey = tuple[str, str, str]


def _idem_key(record: ParseRunRecord) -> _IdemKey:
    return (record.document_id, record.source_raw_hash, record.parser_fingerprint)


class MemoryParseRunRepository:
    """In-memory ``ParseRunRepository``（dict 双索引：id + 幂等键）."""

    def __init__(self) -> None:
        self._by_id: dict[str, ParseRunRecord] = {}
        self._by_idem: dict[_IdemKey, str] = {}

    async def upsert(self, record: ParseRunRecord) -> ParseRunRecord:
        existing_id = self._by_idem.get(_idem_key(record))
        if existing_id is not None:
            existing = self._by_id[existing_id]
            if existing.status == "SUCCEEDED" and record.status == "SUCCEEDED":
                # 等价的重复成功写入：幂等返回原行，不改写（SRS §2.2）。
                return existing
            # 覆盖更新：保留既有行 id（稳定身份），如 FAILED -> SUCCEEDED 翻转。
            updated = replace(record, id=existing_id)
            self._by_id[existing_id] = updated
            return updated
        self._by_id[record.id] = record
        self._by_idem[_idem_key(record)] = record.id
        return record

    async def get(self, parse_run_id: str) -> ParseRunRecord | None:
        return self._by_id.get(parse_run_id)

    async def insert(self, record: ParseRunRecord) -> ParseRunRecord:
        """追加新执行行（M4 Operator）；幂等映射只注册首个锚点行."""
        self._by_id[record.id] = record
        self._by_idem.setdefault(_idem_key(record), record.id)
        return record

    async def find_by_document_hash(
        self,
        document_id: str,
        source_raw_hash: str,
        parser_fingerprint: str,
    ) -> ParseRunRecord | None:
        key = (document_id, source_raw_hash, parser_fingerprint)
        matches = [
            r for r in self._by_id.values() if _idem_key(r) == key
        ]
        if not matches:
            return None
        # 幂等探针优先返回「已成功且转正」的行；否则返回幂等锚点行。
        for record in matches:
            if record.status == "SUCCEEDED" and record.snapshot_id:
                return record
        rid = self._by_idem.get(key)
        return self._by_id.get(rid) if rid else matches[-1]

    async def set_status(
        self,
        parse_run_id: str,
        new_status: str,
        *,
        error_message: str | None = None,
        snapshot_id: str | None = None,
        finished_at: str | None = None,
        parse_ir_storage_object_id: str | None = None,
        parse_ir_schema_version: str | None = None,
        element_count: int | None = None,
        container_count: int | None = None,
        relation_count: int | None = None,
    ) -> ParseRunRecord:
        existing = self._by_id.get(parse_run_id)
        if existing is None:
            raise KeyError(f"unknown parse run id: {parse_run_id!r}")
        assert_transition("parse_run", existing.status, new_status)

        def _keep(new: Any, old: Any) -> Any:
            return new if new is not None else old

        updated = replace(
            existing,
            status=new_status,
            snapshot_id=_keep(snapshot_id, existing.snapshot_id),
            error_message=_keep(error_message, existing.error_message),
            finished_at=_keep(finished_at, existing.finished_at),
            parse_ir_storage_object_id=_keep(
                parse_ir_storage_object_id, existing.parse_ir_storage_object_id
            ),
            parse_ir_schema_version=_keep(
                parse_ir_schema_version, existing.parse_ir_schema_version
            ),
            element_count=_keep(element_count, existing.element_count),
            container_count=_keep(container_count, existing.container_count),
            relation_count=_keep(relation_count, existing.relation_count),
        )
        self._by_id[parse_run_id] = updated
        return updated

    async def update_metadata(
        self, parse_run_id: str, metadata_json: str
    ) -> ParseRunRecord:
        existing = self._by_id.get(parse_run_id)
        if existing is None:
            raise KeyError(f"unknown parse run id: {parse_run_id!r}")
        updated = replace(existing, metadata_json=metadata_json)
        self._by_id[parse_run_id] = updated
        return updated

    # -- 测试辅助（非 Protocol 成员） ---------------------------------------

    def count(self) -> int:
        """当前投影行数（测试断言幂等不新增行）。"""
        return len(self._by_id)


class MemoryParseAttemptRepository:
    """In-memory ``ParseAttemptRepository``（list 存储，序号唯一）."""

    def __init__(self) -> None:
        self._events: list[ParseAttemptRecord] = []

    async def append(self, record: ParseAttemptRecord) -> ParseAttemptRecord:
        dup = any(
            e.parse_run_id == record.parse_run_id
            and e.attempt_index == record.attempt_index
            for e in self._events
        )
        if dup:
            raise ValueError(
                f"attempt_index {record.attempt_index} already exists for run "
                f"{record.parse_run_id!r}"
            )
        self._events.append(record)
        return record

    async def list_by_run(
        self, parse_run_id: str
    ) -> tuple[ParseAttemptRecord, ...]:
        return tuple(
            sorted(
                (e for e in self._events if e.parse_run_id == parse_run_id),
                key=lambda e: e.attempt_index,
            )
        )


__all__ = [
    "MemoryParseAttemptRepository",
    "MemoryParseRunRepository",
]
