"""36号 §五：KB 增量判定状态机（NEW/SKIP/RETRY/UPDATE，集合化）.

产品语义：用户每次点击普通「挖掘」，系统自动做正确的增量判定——
- 已成功入库且未变化的文档直接 SKIP（不进 parse/segment/persist 链）；
- 内容未变但上次失败/未入 Build/readiness 不完整的文档 RETRY；
- 内容或范式变化的文档 UPDATE；新文档 NEW。

判定是**纯函数**：输入全部为批量预取的事实（KB Build 成员、快照签名、
readiness、最近尝试），对 19,789 篇文档不做任何 N+1 查询——查询编排在
:func:`fetch_kb_increment_context`（3 个集合查询 + 既有批量 readiness）。

动作映射（mining_run_documents.action 的 DB CHECK 约束为
NEW/UPDATE/SKIP/REMOVE）：RETRY 落库为 UPDATE，差异原因写进
``metadata_json.incremental_decision``（brief §五：必须能区分原因）。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class KbDocInput:
    """KB 文档当前身份（来自 asset_documents + 当前对象指针）。"""

    document_id: str
    storage_object_id: str
    content_revision: int


@dataclass(frozen=True)
class KbSnapshotFact:
    """serving 快照事实（最新 validated KB Build 的 active 成员）.

    ``workflow_version_id`` / ``workflow_graph_hash`` 是**挖掘范式绑定**
    （把该快照提交进 Build 的 Run 的签名）——不是
    ``asset_document_snapshots.workflow_*``（那几列存的是解析链标识
    ``new-parse-chain@N``，与挖掘工作流的 version_id（UUID）/graph_hash
    属于两套标识体系，按字面比较必然不等）。
    """

    document_id: str
    snapshot_id: str
    workflow_version_id: str | None
    workflow_graph_hash: str | None
    source_storage_object_id: str | None
    source_content_revision: int | None
    build_finished_at: Any | None = None


@dataclass(frozen=True)
class KbLastAttempt:
    """该 KB 中此文档最近一次挖掘尝试（跨 Run）。"""

    status: str
    started_at: str | None


@dataclass(frozen=True)
class KbIncrementDecision:
    document_id: str
    #: 落库动作（DB 约束集）：NEW / UPDATE / SKIP（RETRY 映射 UPDATE）
    action: str
    #: 差异化原因（机器码，写 metadata.incremental_decision）：
    #: new_document / skip_unchanged / retry_after_failure /
    #: retry_readiness_incomplete / update_content_changed /
    #: update_workflow_changed
    decision: str
    reason: str
    #: SKIP 时的 carry-forward 目标（serving 快照）
    serving_snapshot_id: str | None = None


def readiness_complete(
    row: Mapping[str, Any] | None, *, require_dense: bool,
) -> bool:
    """文档级 readiness 完整（brief §四）——**单一真相源**.

    增量 SKIP 判定（本模块）与 finalize 文档级分区
    （jobs/run.py `_document_rejection_reason`）共用同一谓词——两处手抄
    必然漂移（36号审查 MED-4），漂移的直接后果是「入库时判完整、下轮
    挖掘判不完整」的永久重挖循环。

    - 行缺失 → False（未知按不完整处理，宁可重试不可漏检）；
    - 基础要求 ``search_ready``；
    - 含 embedding 算子的范式（require_dense）额外要求 dense 覆盖完整：
      ``dense_units > 0 AND dense_covered == dense_units``——部分向量文档
      不得被判定为完整标准混合资产。
    """
    if not isinstance(row, Mapping):
        return False
    if not row.get("search_ready"):
        return False
    if not require_dense:
        return True
    counts = row.get("counts") or {}
    dense_units = int(counts.get("dense_units") or 0)
    dense_covered = int(counts.get("dense_covered") or 0)
    return dense_units > 0 and dense_covered == dense_units


def classify_kb_documents(
    *,
    docs: Iterable[KbDocInput],
    build_facts: Mapping[str, KbSnapshotFact],
    readiness_by_snapshot: Mapping[str, Mapping[str, Any]],
    last_attempts: Mapping[str, KbLastAttempt],
    workflow_version_id: str | None,
    workflow_graph_hash: str | None,
    build_finished_at: str | None,
    require_dense: bool,
) -> dict[str, KbIncrementDecision]:
    """集合化增量判定（纯函数）.

    判定优先级（每文档）：
    1. 不在 KB Build：有过失败/中断尝试 → RETRY；否则 NEW；
    2. 在 Build：内容（对象指针+revision）变化 → UPDATE；
    3. 范式签名（version_id/graph_hash）变化 → UPDATE；
    4. 比更新的失败/中断尝试（同内容重试未成功）→ RETRY；
    5. readiness 不完整 → RETRY；
    6. 其余 → SKIP（carry-forward serving 快照）。
    """
    decisions: dict[str, KbIncrementDecision] = {}
    for doc in docs:
        fact = build_facts.get(doc.document_id)
        attempt = last_attempts.get(doc.document_id)

        def _decide(
            action: str, decision: str, reason: str,
            serving: str | None = None,
        ) -> KbIncrementDecision:
            return KbIncrementDecision(
                document_id=doc.document_id, action=action,
                decision=decision, reason=reason,
                serving_snapshot_id=serving,
            )

        if fact is None:
            if attempt is not None and attempt.status in ("failed", "processing"):
                decisions[doc.document_id] = _decide(
                    "UPDATE", "retry_after_failure",
                    "content unchanged but last mining attempt "
                    f"ended as {attempt.status}",
                )
            else:
                decisions[doc.document_id] = _decide(
                    "NEW", "new_document",
                    "document has never entered a validated KB build",
                )
            continue

        # 注意 revision 0 是合法值（asset_documents.content_revision 默认 0），
        # 不得用 `or -1` 兜底——那会把 revision-0 文档永久判成内容变化。
        fact_revision = (
            fact.source_content_revision
            if fact.source_content_revision is not None else -1
        )
        content_same = (
            fact.source_storage_object_id == doc.storage_object_id
            and int(fact_revision) == int(doc.content_revision)
        )
        workflow_same = (
            fact.workflow_version_id == workflow_version_id
            and fact.workflow_graph_hash == workflow_graph_hash
        )
        readiness_ok = readiness_complete(
            readiness_by_snapshot.get(fact.snapshot_id),
            require_dense=require_dense,
        )
        # 比 Build 更新的失败/中断尝试：同内容重试仍未成功 → 不得 SKIP
        failed_attempt_newer = (
            attempt is not None
            and attempt.status in ("failed", "processing")
            and (attempt.started_at or "")
            > (fact.build_finished_at or build_finished_at or "")
        )

        if not content_same:
            decisions[doc.document_id] = _decide(
                "UPDATE", "update_content_changed",
                "source object/revision differs from serving snapshot",
            )
        elif not workflow_same:
            decisions[doc.document_id] = _decide(
                "UPDATE", "update_workflow_changed",
                "workflow signature differs from serving snapshot",
            )
        elif failed_attempt_newer:
            decisions[doc.document_id] = _decide(
                "UPDATE", "retry_after_failure",
                f"last attempt ({attempt.status}) is newer than the build",
            )
        elif not readiness_ok:
            decisions[doc.document_id] = _decide(
                "UPDATE", "retry_readiness_incomplete",
                "serving snapshot readiness incomplete for current paradigm",
            )
        else:
            decisions[doc.document_id] = _decide(
                "SKIP", "skip_unchanged",
                "serving snapshot matches content, workflow and readiness",
                serving=fact.snapshot_id,
            )
    return decisions


def fetch_kb_last_attempts(
    runtime_db: Any, *, kb_id: str, document_ids: list[str],
) -> dict[str, KbLastAttempt]:
    """每文档最近一次尝试（跨本 KB 的全部 Run；DISTINCT ON 集合查询）."""
    if not document_ids:
        return {}
    rows = runtime_db._fetchall(
        """SELECT DISTINCT ON (rd.document_id)
                  rd.document_id, rd.status, rd.started_at
             FROM mining_run_documents AS rd
             JOIN mining_runs AS r ON r.id = rd.run_id
            WHERE r.kb_id = %s
              AND rd.document_id = ANY(%s)
            ORDER BY rd.document_id, r.started_at DESC, rd.id DESC""",
        (kb_id, document_ids),
    )
    return {
        str(row["document_id"]): KbLastAttempt(
            status=str(row["status"]), started_at=row.get("started_at"),
        )
        for row in rows
    }


def fetch_kb_committed_signatures(
    runtime_db: Any, *, kb_id: str,
    document_snapshots: Mapping[str, str],
) -> dict[tuple[str, str], dict[str, Any]]:
    """每个 Build 文档/快照最近 committed Run 的范式签名（集合查询）.

    snapshot 行的 workflow_* 列存解析链标识（new-parse-chain@N），与挖掘
    工作流的 version_id（UUID）/graph_hash 是两套体系——范式签名必须从
    「把该具体快照提交进 Build 的 Run」取。v1.0.1 失败 Run 可能把另一
    个、从未入 Build 的快照提前标 committed；因此 document_id 单键不足。
    """
    if not document_snapshots:
        return {}
    document_ids = list(document_snapshots)
    snapshot_ids = [document_snapshots[doc_id] for doc_id in document_ids]
    rows = runtime_db._fetchall(
        """SELECT DISTINCT ON (rd.document_id, rd.document_snapshot_id)
                  rd.document_id, rd.document_snapshot_id,
                  r.workflow_version_id, r.workflow_graph_hash
             FROM mining_run_documents rd
             JOIN mining_runs AS r ON r.id = rd.run_id
             JOIN asset_builds AS b
               ON b.mining_run_id = r.id
              AND b.kb_id = r.kb_id
              AND b.status IN ('validated', 'published')
             JOIN asset_build_document_snapshots AS bs
               ON bs.build_id = b.id
              AND bs.document_id = rd.document_id
              AND bs.document_snapshot_id = rd.document_snapshot_id
              AND bs.selection_status = 'active'
            WHERE r.kb_id = %s
              AND rd.document_id = ANY(%s)
              AND rd.document_snapshot_id = ANY(%s)
              AND rd.status = 'committed'
              AND rd.document_snapshot_id IS NOT NULL
            ORDER BY rd.document_id, rd.document_snapshot_id,
                     r.started_at DESC, rd.id DESC""",
        (kb_id, document_ids, snapshot_ids),
    )
    return {
        (str(row["document_id"]), str(row["document_snapshot_id"])): dict(row)
        for row in rows
    }


def fetch_kb_increment_context(
    asset_db: Any,
    runtime_db: Any,
    *,
    kb_id: str,
    document_ids: list[str],
    require_dense: bool,
) -> tuple[
    dict[str, KbSnapshotFact],
    dict[str, Mapping[str, Any]],
    dict[str, KbLastAttempt],
    str | None,
]:
    """批量预取增量判定所需的全部事实（常数个集合查询）.

    返回 (build_facts, readiness_by_snapshot, last_attempts,
    build_finished_at)。19,789 篇文档同样只有常数次查询——严禁在此
    引入逐文档循环查询。范式签名取自「最近一次 committed 该文档的 Run」
    （见 fetch_kb_committed_signatures），不用 snapshot 行的解析链标识。
    """
    build_facts: dict[str, KbSnapshotFact] = {}
    readiness: dict[str, Mapping[str, Any]] = {}
    build_finished_at: str | None = None

    build = asset_db.get_latest_validated_kb_build(kb_id)
    if build is not None:
        build_finished_at = build.get("finished_at") or build.get("created_at")
        current_loader = getattr(
            asset_db, "get_current_kb_build_snapshots", None,
        )
        current_rows = (
            current_loader(kb_id)
            if current_loader is not None
            else asset_db.get_build_snapshots(build["id"])
        )
        active = [
            row for row in (current_rows or [])
            if row.get("selection_status") == "active"
        ]
        document_snapshots = {
            str(row["document_id"]): str(row["document_snapshot_id"])
            for row in active
        }
        facts_by_identity = asset_db.fetch_kb_snapshot_facts(
            document_snapshots,
        )
        signatures = fetch_kb_committed_signatures(
            runtime_db, kb_id=kb_id,
            document_snapshots=document_snapshots,
        )
        for row in active:
            doc_id = str(row["document_id"])
            snapshot_id = str(row["document_snapshot_id"])
            identity = (doc_id, snapshot_id)
            fact = facts_by_identity.get(identity)
            if fact is None:
                continue
            signature = signatures.get(identity) or {}
            build_facts[doc_id] = KbSnapshotFact(
                document_id=doc_id,
                snapshot_id=snapshot_id,
                workflow_version_id=signature.get("workflow_version_id"),
                workflow_graph_hash=signature.get("workflow_graph_hash"),
                source_storage_object_id=fact.get("source_storage_object_id"),
                source_content_revision=fact.get("source_content_revision"),
                build_finished_at=(
                    row.get("build_finished_at")
                    or row.get("build_created_at")
                    or build_finished_at
                ),
            )
        if build_facts:
            frozen = asset_db.fetch_snapshot_readiness(
                sorted({f.snapshot_id for f in build_facts.values()}),
            )
            readiness = {
                str(sid): dict(row) for sid, row in (frozen or {}).items()
            }

    attempts = fetch_kb_last_attempts(
        runtime_db, kb_id=kb_id, document_ids=document_ids,
    )
    return build_facts, readiness, attempts, build_finished_at


__all__ = [
    "KbDocInput",
    "KbIncrementDecision",
    "KbLastAttempt",
    "KbSnapshotFact",
    "classify_kb_documents",
    "fetch_kb_committed_signatures",
    "fetch_kb_increment_context",
    "fetch_kb_last_attempts",
    "readiness_complete",
]
