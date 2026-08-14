"""Contracts for the Shadow Parse layer (M2, SRS §2.2 / §C08 / §4.6).

影子解析（Shadow Parse）是文档解析平台化 M2 的写入链路：把一次解析执行产出的
Parse IR 制品落到对象存储的 parse bucket，并在 ``asset_parse_runs`` 投影一行
运行摘要。它与现有发布链路**硬隔离**——绝不写
``asset_document_snapshots`` / ``asset_raw_segments`` / ``mining_run_documents``
（M2 退出条件：不影响现有发布）。

本模块是编排服务（``shadow_parse.service.ShadowParseService``）与持久化层之间的
六边形接缝，定义：

- :class:`ParseRunRecord` — ``asset_parse_runs`` 一行的冻结投影（009 DDL）。
- :class:`ParseRunRepository` — 该表的仓储 Protocol（memory / PG 双实现）。
- :class:`ShadowParseResult` — ``ShadowParseService.run`` 的返回值。

设计（ADR-0003 D-001 / D-022）：
- 纯 stdlib frozen dataclass + ``runtime_checkable Protocol``，与
  ``contracts/file_management.py`` 同风格。
- 影子运行**无状态机**（M4 才引入完整 Parse Run 状态机）：一次执行直接落终态
  ``SUCCEEDED`` / ``FAILED``，因此 status 只有这两个值。
- 幂等键 ``UNIQUE(document_id, source_raw_hash, parser_fingerprint)``（SRS §2.2
  幂等复用）：同输入同 parser 复用已有制品与投影行。

References:
- SRS §2.2（幂等复用）、§4.6（一次解析执行）、§C08（Shadow Parse）。
- ADR-0003 D-001（frozen dataclass + Protocol）、D-003（表进 asset_core）、
  D-022（Repository Protocol + service layering）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# 影子运行终态集合（009 DDL CHECK 对齐；无状态机，M4 才扩展）。
SHADOW_PARSE_STATUSES: frozenset[str] = frozenset({"SUCCEEDED", "FAILED"})


@dataclass(frozen=True)
class ParseRunRecord:
    """``asset_parse_runs`` 一行（009 DDL，SRS §2.2 / §C08）。

    影子解析运行摘要投影。成功行携带 ``parse_ir_storage_object_id``（指向
    parse bucket 中注册的 IR 制品）与三个计数摘要；失败行只携带
    ``error_message``。一次执行直接落终态，无中间态。
    """

    id: str
    document_id: str
    source_storage_object_id: str
    source_raw_hash: str
    source_content_revision: int
    parser_id: str
    parser_fingerprint: str
    status: str
    parse_ir_storage_object_id: str | None = None
    parse_ir_schema_version: str | None = None
    element_count: int | None = None
    container_count: int | None = None
    relation_count: int | None = None
    error_message: str | None = None
    started_at: str = ""
    finished_at: str | None = None
    metadata_json: str = "{}"


@dataclass(frozen=True)
class ShadowParseResult:
    """``ShadowParseService.run`` 的返回值。

    ``reused`` 为 True 表示命中了已有 SUCCEEDED 投影行（幂等探针，SRS §2.2），
    本次调用没有重新解析、也没有写任何对象。
    """

    parse_run_id: str
    status: str
    parse_ir_storage_object_id: str | None = None
    element_count: int | None = None
    reused: bool = False


@runtime_checkable
class ParseRunRepository(Protocol):
    """``asset_parse_runs`` 仓储 Protocol（SRS §2.2 幂等复用）。

    幂等语义：唯一键为
    ``(document_id, source_raw_hash, parser_fingerprint)``。
    """

    async def upsert(self, record: ParseRunRecord) -> ParseRunRecord:
        """插入或按幂等键覆盖一行，返回存储后的记录。

        命中唯一键时在原行上更新（保留既有 ``id``，稳定身份）；若新旧记录
        均为 SUCCEEDED（内容等价的重复写入），返回已存在行不再改写。
        FAILED 行被同键的后续执行覆盖（重跑可翻转为 SUCCEEDED）。
        """
        ...

    async def get(self, parse_run_id: str) -> ParseRunRecord | None:
        """按 ``id`` 取一行，不存在返回 None。"""
        ...

    async def find_by_document_hash(
        self,
        document_id: str,
        source_raw_hash: str,
        parser_fingerprint: str,
    ) -> ParseRunRecord | None:
        """幂等探针：按唯一键查行，不存在返回 None（SRS §2.2）。"""
        ...


__all__ = [
    "ParseRunRecord",
    "ParseRunRepository",
    "SHADOW_PARSE_STATUSES",
    "ShadowParseResult",
]
