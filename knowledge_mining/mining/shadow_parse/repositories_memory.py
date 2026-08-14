"""In-memory fake repository for the Shadow Parse layer (M2).

Implements ``ParseRunRepository``（``shadow_parse/contracts.py``）backed by
plain ``dict`` stores，供服务测试与本地开发使用——整套影子解析链路无需
PostgreSQL 即可跑通（ADR-0003 D-006 / D-022，与 file_management 的
``repositories_memory`` 同风格）。

幂等模型（SRS §2.2）：
- 唯一键 ``(document_id, source_raw_hash, parser_fingerprint)`` 维护二级索引。
- ``upsert`` 命中唯一键时保留既有行 ``id``（稳定身份）：双 SUCCEEDED 视为
  等价重复写入直接返回原行；否则（FAILED 重跑翻转等）用新记录覆盖原行。
"""
from __future__ import annotations

from dataclasses import replace

from knowledge_mining.mining.shadow_parse.contracts import (
    ParseRunRecord,
    SHADOW_PARSE_STATUSES,
)

_IdemKey = tuple[str, str, str]


def _idem_key(record: ParseRunRecord) -> _IdemKey:
    return (record.document_id, record.source_raw_hash, record.parser_fingerprint)


class MemoryParseRunRepository:
    """In-memory ``ParseRunRepository``（dict 双索引：id + 幂等键）。"""

    def __init__(self) -> None:
        self._by_id: dict[str, ParseRunRecord] = {}
        self._by_idem: dict[_IdemKey, str] = {}

    async def upsert(self, record: ParseRunRecord) -> ParseRunRecord:
        if record.status not in SHADOW_PARSE_STATUSES:
            raise ValueError(f"unknown shadow parse status: {record.status!r}")
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

    async def find_by_document_hash(
        self,
        document_id: str,
        source_raw_hash: str,
        parser_fingerprint: str,
    ) -> ParseRunRecord | None:
        rid = self._by_idem.get((document_id, source_raw_hash, parser_fingerprint))
        return self._by_id[rid] if rid else None

    # -- 测试辅助（非 Protocol 成员） ---------------------------------------

    def count(self) -> int:
        """当前投影行数（测试断言幂等不新增行）。"""
        return len(self._by_id)


__all__ = ["MemoryParseRunRepository"]
