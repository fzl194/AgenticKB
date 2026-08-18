"""Contracts for the Shadow Parse layer (M2 → M4, SRS §2.2 / §C08 / §4.6 / §9.2).

影子解析（Shadow Parse）是文档解析平台化 M2 的写入链路：把一次解析执行产出的
Parse IR 制品落到对象存储的 parse bucket，并在 ``asset_parse_runs`` 投影一行
运行摘要。它与现有发布链路**硬隔离**——M2 阶段绝不写
``asset_document_snapshots`` / ``asset_raw_segments`` / ``mining_run_documents``。

M4 起（SRS §9.2）：

- ``ParseRunRecord.status`` 承载**完整 Parse Run 状态机**（QUEUED → … →
  EVALUATING → SUCCEEDED/FAILED/CANCELLED/**SUPERSEDED**），状态集合的
  单一事实源在 ``contracts/state_machines.py``（010 DDL CHECK 对齐）；
- ``set_status`` 是**执行内**状态推进（走 LEGAL_TRANSITIONS 校验），
  ``upsert`` 仍是**跨执行**投影幂等写入（FAILED 行被同键新执行覆盖）；
- ``ParseAttemptRecord`` / ``ParseAttemptRepository`` 记录每个 backend
  尝试（primary/fallback/repair/replay）——SRS §2.2「fallback 必须留下
  原因」、§9.2「重试创建新的 attempt event」。

设计（ADR-0003 D-001 / D-022）：
- 纯 stdlib frozen dataclass + ``runtime_checkable Protocol``，与
  ``contracts/file_management.py`` 同风格。
- 幂等键 ``UNIQUE(document_id, source_raw_hash, parser_fingerprint)``
  （SRS §2.2 幂等复用）：同输入同 parser 复用已有制品与投影行。

References:
- SRS §2.2（幂等复用）、§4.6（一次解析执行）、§9.2（状态机）、§9.5
  （SUPERSEDED 恢复行）、§C08（Shadow Parse）。
- ADR-0003 D-001（frozen dataclass + Protocol）、D-003（表进 asset_core）、
  D-022（Repository Protocol + service layering）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from knowledge_mining.mining.contracts.state_machines import (
    VALID_PARSE_RUN_STATES,
)

#: Parse Run 状态集合（M4 起为完整状态机；单一事实源在
#: ``contracts/state_machines.py``，与 010 DDL CHECK 对齐）。
PARSE_RUN_STATUSES: frozenset[str] = VALID_PARSE_RUN_STATES

#: M2 兼容别名：影子运行一次执行直接落的两个终态。
SHADOW_PARSE_STATUSES: frozenset[str] = frozenset({"SUCCEEDED", "FAILED"})

#: attempt_kind 词表（010 DDL CHECK 对齐）：primary=计划首选 / fallback=
#: 质量或失败触发的备选 / repair=页级修复重试 / replay=用已存 raw
#: artifact 重放 normalize（§9.5 A09）。
PARSE_ATTEMPT_KINDS: frozenset[str] = frozenset(
    {"primary", "fallback", "repair", "replay"}
)

#: attempt outcome 词表（尝试本身的结局；Run 级终态另算）。
PARSE_ATTEMPT_OUTCOMES: frozenset[str] = frozenset({"SUCCEEDED", "FAILED"})


@dataclass(frozen=True)
class ParseRunRecord:
    """``asset_parse_runs`` 一行（009+010 DDL，SRS §2.2 / §C08 / §9.2）.

    M4 起承载完整状态机（QUEUED → … → EVALUATING → SUCCEEDED/FAILED/
    CANCELLED/SUPERSEDED）。成功行携带 ``parse_ir_storage_object_id``
    （指向 parse bucket 中注册的 IR 制品）与计数摘要；``snapshot_id``
    在转正后回填；SUPERSEDED/FAILED 恒无快照。
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
    snapshot_id: str | None = None
    error_message: str | None = None
    started_at: str = ""
    finished_at: str | None = None
    metadata_json: str = "{}"

    def __post_init__(self) -> None:
        if self.status not in PARSE_RUN_STATUSES:
            raise ValueError(
                f"unknown parse run status: {self.status!r}; expected one of "
                f"{sorted(PARSE_RUN_STATUSES)}"
            )


@dataclass(frozen=True)
class ParseAttemptRecord:
    """``asset_parse_run_attempts`` 一行（010 DDL，SRS §4.6/§9.2）.

    一次 backend 尝试的审计事件：哪个 parser、什么角色（primary/fallback/
    repair/replay）、结局如何、错误是什么。重试产生新序号，不覆盖旧事件
    （§9.2「重试创建新的 attempt event」）。
    """

    id: str
    parse_run_id: str
    attempt_index: int
    parser_id: str
    parser_fingerprint: str
    attempt_kind: str
    outcome: str
    started_at: str
    finished_at: str | None = None
    error_message: str | None = None
    metadata_json: str = "{}"

    def __post_init__(self) -> None:
        if self.attempt_index < 0:
            raise ValueError(
                f"attempt_index must be >= 0, got {self.attempt_index}"
            )
        if self.attempt_kind not in PARSE_ATTEMPT_KINDS:
            raise ValueError(
                f"attempt_kind must be one of {sorted(PARSE_ATTEMPT_KINDS)}, "
                f"got {self.attempt_kind!r}"
            )
        if self.outcome not in PARSE_ATTEMPT_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {sorted(PARSE_ATTEMPT_OUTCOMES)}, "
                f"got {self.outcome!r}"
            )


@dataclass(frozen=True)
class ShadowParseResult:
    """``ShadowParseService.run`` 的返回值.

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
    """``asset_parse_runs`` 仓储 Protocol（SRS §2.2 幂等复用）.

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

    async def insert(self, record: ParseRunRecord) -> ParseRunRecord:
        """**追加**一行新执行记录（不做幂等合并，M4 Operator 用）.

        Run 是执行历史：同 ``(document, raw_hash, parser_fingerprint)``
        可有多行（FAILED 重跑、A09 重放、A07 升级）。幂等复用的锚点是
        Snapshot 指纹（§2.2），不是 Run 行——010 起 idem 唯一索引已让位
        为普通索引。
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
        """执行内状态推进：按 ``LEGAL_TRANSITIONS`` 校验后原地更新.

        非法跳转（跳阶段/终态回退）抛 :class:`IllegalTransition`；
        未知 run id 抛 ``KeyError``。``snapshot_id`` 仅在 SUCCEEDED
        转正后传入；``error_message`` 在 FAILED/CANCELLED 传入；计数与
        IR 指针在 EVALUATING（制品已落）时传入——None 表示保留原值。
        """
        ...


@runtime_checkable
class ParseAttemptRepository(Protocol):
    """``asset_parse_run_attempts`` 仓储 Protocol（SRS §4.6/§9.2）."""

    async def append(self, record: ParseAttemptRecord) -> ParseAttemptRecord:
        """追加一次尝试事件；``(parse_run_id, attempt_index)`` 重复即拒."""
        ...

    async def list_by_run(
        self, parse_run_id: str
    ) -> tuple[ParseAttemptRecord, ...]:
        """按 attempt_index 升序列出该 run 的全部尝试（审计用）."""
        ...


__all__ = [
    "PARSE_ATTEMPT_KINDS",
    "PARSE_ATTEMPT_OUTCOMES",
    "PARSE_RUN_STATUSES",
    "ParseAttemptRecord",
    "ParseAttemptRepository",
    "ParseRunRecord",
    "ParseRunRepository",
    "SHADOW_PARSE_STATUSES",
    "ShadowParseResult",
]
