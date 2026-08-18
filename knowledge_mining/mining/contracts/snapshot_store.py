"""Snapshot Store 契约（M4 WP9，SRS §3.3 / §8.3A / §9.4）.

``asset_document_snapshots`` 是唯一知识版本根（SRS 已固定决策 7）。本模块
定义 M4 转正链路的六边形接缝：

- :func:`snapshot_fingerprint` — Snapshot 唯一身份（§8.3A：
  raw hash + parser/model/config + workflow graph + IR schema + compiler
  指纹的合成；新链的 workflow 绑定用固定哨兵，M6 接入真实 workflow 后
  自然进入指纹）。
- :class:`SnapshotRecord` — READY 快照行的冻结投影（008 迁移已备列）。
- :class:`SnapshotSourceLink` — snapshot_links 行（§8.3A：来源对象 +
  content revision；legacy ``source_uri``/``relative_path`` 用对象 URI
  哨兵填充，不用 presigned URL）。
- :class:`SnapshotRepository` — 幂等提交 Protocol（memory / PG 双实现）。

提交语义（SRS §9.4 + §2.2 幂等）：
- 只有 PASS/WARN 能到达 ``commit``（FAIL 在编排层就被阻断——记录层同样
  拒绝，双保险）；
- 命中 ``UNIQUE(domain, snapshot_fingerprint)`` 的既有 READY 行 → 返回
  原行 + ``created=False``（相同内容与加工指纹幂等复用，不重复建行）；
- 提交过程状态机（STAGING_ARTIFACTS→COMPILING→READY）由服务层推进；
  失败由 Parse Run 记录，**不产生半成品快照行**（§9.4）。

设计（ADR-0003 D-001）：frozen dataclass + runtime_checkable Protocol，
纯 stdlib。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

#: 快照生命周期（§8.3A lifecycle_status）。READY 不可变；DEPRECATED/REVOKED
#: 是运维标记，不物理删除（被历史 Build 引用时仍可读，§9.3）。
SNAPSHOT_LIFECYCLE_STATES: frozenset[str] = frozenset({
    "READY", "DEPRECATED", "REVOKED",
})

#: 允许进入快照行的质量结论（§8.3A quality_status：FAIL 不创建 READY
#: Snapshot——FAIL 根本不落行）。
SNAPSHOT_QUALITY_STATUSES: frozenset[str] = frozenset({"PASS", "WARN"})

#: 新链 workflow 绑定哨兵：M4 快照由解析链直接产生，尚不挂 workflow 算子
#: （M6 WP11）。哨兵进指纹而非留 NULL，保证指纹输入确定。
NEW_CHAIN_WORKFLOW_BINDING = "new-parse-chain@1"


def snapshot_fingerprint(
    *,
    domain: str,
    source_raw_hash: str,
    effective_pipeline_fingerprint: str,
    compiler_fingerprint: str | None = None,
    workflow_binding: str = NEW_CHAIN_WORKFLOW_BINDING,
) -> str:
    """Snapshot 唯一身份指纹（SRS §8.3A）.

    输入任一成分变化（文档内容 / 解析管线 / 切片策略 / workflow 绑定）
    必然产生新指纹 → 新 Snapshot；完全相同的输入幂等复用同一指纹。
    """
    payload = json.dumps(
        {
            "domain": domain,
            "source_raw_hash": source_raw_hash,
            "pipeline": effective_pipeline_fingerprint,
            "compiler": compiler_fingerprint or "",
            "workflow": workflow_binding,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"snap-{digest}"


@dataclass(frozen=True)
class SnapshotRecord:
    """``asset_document_snapshots`` 一行的冻结投影（008 目标列 + legacy 必填列）.

    legacy 必填：domain / raw_content_hash / normalized_content_hash /
    mime_type / created_at（001 DDL NOT NULL）。新链无旧预处理归一层，
    ``normalized_content_hash`` 与 raw 一致——语义是「该解析管线的输入
    内容 hash」，如实不伪造。
    """

    id: str
    domain: str
    snapshot_fingerprint: str
    raw_content_hash: str
    normalized_content_hash: str
    mime_type: str
    parse_ir_storage_object_id: str
    parse_ir_schema_version: str
    parser_fingerprint: str
    quality_status: str
    created_by_run_id: str | None = None
    created_at: str = ""
    title: str | None = None
    compiler_fingerprint: str | None = None  # M5 Segment Compiler 起填充
    lifecycle_status: str = "READY"
    parser_profile_json: str = "{}"
    metadata_json: str = "{}"

    def __post_init__(self) -> None:
        if self.quality_status not in SNAPSHOT_QUALITY_STATUSES:
            raise ValueError(
                f"quality_status must be one of "
                f"{sorted(SNAPSHOT_QUALITY_STATUSES)} (FAIL never commits a "
                f"snapshot), got {self.quality_status!r}"
            )
        if self.lifecycle_status not in SNAPSHOT_LIFECYCLE_STATES:
            raise ValueError(
                f"lifecycle_status must be one of "
                f"{sorted(SNAPSHOT_LIFECYCLE_STATES)}, got "
                f"{self.lifecycle_status!r}"
            )


@dataclass(frozen=True)
class SnapshotSourceLink:
    """``asset_document_snapshot_links` 一行（§8.3：哪个文档用哪个对象形成该快照）.

    legacy ``source_uri``/``relative_path`` NOT NULL——用对象 URI 哨兵
    （``minio://{bucket}/{key}``，**不是** presigned URL，§2.1 不得持久化
    短期凭证）填充，由仓储实现负责生成。
    """

    id: str
    document_id: str
    document_snapshot_id: str
    source_storage_object_id: str
    source_content_revision: int
    title: str | None = None
    linked_at: str = ""
    source_uri: str = ""
    relative_path: str = ""


@dataclass(frozen=True)
class SnapshotCommitResult:
    """``SnapshotRepository.commit`` 的返回值.

    ``created=False`` 表示命中既有指纹（幂等复用），``snapshot`` 为已存在
    的 READY 行；``reused_reason`` 说明复用来源（审计用）。
    """

    snapshot: SnapshotRecord
    created: bool
    reused_reason: str = ""


@runtime_checkable
class SnapshotRepository(Protocol):
    """快照幂等提交 Protocol（memory / PG 双实现，SRS §2.2/§9.4）."""

    async def commit(
        self, snapshot: SnapshotRecord, link: SnapshotSourceLink
    ) -> SnapshotCommitResult:
        """幂等提交一个 READY 快照 + 来源 link.

        命中 ``(domain, snapshot_fingerprint)`` 的既有行时返回原行
        （``created=False``），不重复插入；新指纹插入快照行与 link 行。
        """
        ...

    async def get(self, snapshot_id: str) -> SnapshotRecord | None:
        """按 id 取快照行，不存在返回 None."""
        ...

    async def find_by_fingerprint(
        self, domain: str, fingerprint: str
    ) -> SnapshotRecord | None:
        """幂等探针：按 ``(domain, snapshot_fingerprint)`` 查行."""
        ...

    async def mark_lifecycle(
        self, snapshot_id: str, lifecycle_status: str
    ) -> SnapshotRecord:
        """READY → DEPRECATED/REVOKED 运维标记（不可逆回 READY）."""
        ...


__all__ = [
    "NEW_CHAIN_WORKFLOW_BINDING",
    "SNAPSHOT_LIFECYCLE_STATES",
    "SNAPSHOT_QUALITY_STATUSES",
    "SnapshotCommitResult",
    "SnapshotRecord",
    "SnapshotRepository",
    "SnapshotSourceLink",
    "snapshot_fingerprint",
]
