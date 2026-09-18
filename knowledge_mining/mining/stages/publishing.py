"""KB Build assembly and validation.

Two-phase:
- classify_documents: compare snapshots against previous active build → NEW/UPDATE/SKIP/REMOVE
- assemble_build: select snapshots, merge with previous active build (incremental or full)
The latest validated Build of a KB is the serving view; there is no separate
domain/channel release pointer.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from knowledge_mining.mining.infra.db import AssetCoreDB

logger = logging.getLogger(__name__)


class PublishingStage:
    """Stage wrapper for publishing operations."""
    stage_name = "publishing"
    stage_version = "1"

    def execute(self, context: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return context


def classify_documents(
    asset_db: AssetCoreDB,
    snapshot_decisions: list[dict[str, Any]],
    *,
    domain: str,
    channel: str,
    detect_remove: bool = True,
    kb_id: str | None = None,
    present_document_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Classify each document action by comparing with previous active build.

    Input: snapshot_decisions with document_id, document_snapshot_id (current run).
    Output: enriched snapshot_decisions with action, selection_status, reason.

    Actions:
    - NEW: document not in previous build
    - UPDATE: document exists but snapshot changed
    - SKIP: document exists and snapshot unchanged
    - REMOVE: document in previous build but not in current run (deleted file)

    Args:
        domain: Scope comparison to this domain's previous active build.
        channel: Release channel whose active build is the comparison parent.
        detect_remove: When False, skip REMOVE detection. Use for incremental
            batch mining where each run only processes a subset of documents.
            Parent build snapshots are carried forward by assemble_build instead.
        kb_id: 36号 §七——非空时比较父本是「该 KB 最新 validated Build」而非
            域级 active release。KB 挖掘 publish=False，KB Build 不进域级
            release；用域级 parent 会把同域其它 KB 的文档误当 prev、且本 KB
            的 carry-forward 永远失效。
    """
    if not kb_id:
        raise ValueError("kb_id is required; domain release scope is retired")
    prev_build = asset_db.get_latest_validated_kb_build(kb_id)
    prev_snapshots: dict[str, str] = {}  # document_id -> snapshot_id

    if prev_build:
        current_loader = (
            getattr(asset_db, "get_current_kb_build_snapshots", None)
            if kb_id else None
        )
        previous_rows = (
            current_loader(kb_id)
            if current_loader is not None
            else asset_db.get_build_snapshots(prev_build["id"])
        )
        for ps in previous_rows:
            if ps["selection_status"] == "active":
                prev_snapshots[ps["document_id"]] = ps["document_snapshot_id"]

    # Detect REMOVE: documents in prev build but not in current run
    # Skip when running incremental batches (each run = partial corpus)
    if detect_remove:
        current_doc_ids = (
            set(present_document_ids)
            if present_document_ids is not None
            else {d["document_id"] for d in snapshot_decisions}
        )
        for doc_id, snap_id in prev_snapshots.items():
            if doc_id not in current_doc_ids:
                snapshot_decisions.append({
                    "document_id": doc_id,
                    "document_snapshot_id": snap_id,
                    "action": "REMOVE",
                    "reason": "remove",
                    "selection_status": "removed",
                    "document_key": "",
                })

    for decision in snapshot_decisions:
        # Skip already-classified REMOVE entries
        if decision.get("action") == "REMOVE":
            continue

        doc_id = decision["document_id"]
        snap_id = decision["document_snapshot_id"]

        if decision.get("selection_status") == "removed":
            decision["action"] = "REMOVE"
            decision["reason"] = "remove"
        elif doc_id not in prev_snapshots:
            decision["action"] = "NEW"
            decision["reason"] = "add"
            decision["selection_status"] = "active"
        elif prev_snapshots[doc_id] == snap_id:
            decision["action"] = "SKIP"
            decision["reason"] = "retain"
            decision["selection_status"] = "active"
        else:
            decision["action"] = "UPDATE"
            decision["reason"] = "update"
            decision["selection_status"] = "active"

    return snapshot_decisions


def determine_build_mode(has_prev_build: bool) -> str:
    """Determine build mode based on whether a previous active build exists.

    Returns "full" if no previous build exists, otherwise "incremental".
    """
    if not has_prev_build:
        return "full"
    return "incremental"


def assemble_build(
    asset_db: AssetCoreDB,
    *,
    domain: str,
    channel: str,
    run_id: str,
    batch_id: str | None,
    snapshot_decisions: list[dict[str, Any]],
    kb_id: str | None = None,
    capabilities: list[str] | None = None,
    embedding_fallback: bool = False,
    readiness_summary: dict[str, Any] | None = None,
    allow_empty: bool = False,
) -> str:
    """Assemble a new build from snapshot decisions with merge semantics.

    snapshot_decisions: list of dicts with keys:
        document_id, document_snapshot_id, action (NEW/UPDATE/SKIP/REMOVE),
        selection_status (active/removed), reason (add/update/retain/remove)

    Build mode is determined automatically:
    - "full" when no previous active build exists for this domain
    - "incremental" when merging with previous active build for this domain
      (kb_id 非空时，parent 是该 KB 最新 validated Build——36号 §七)

    Returns build_id.
    """
    with asset_db.transaction():
        if (
            batch_id is not None
            and asset_db.get_source_batch(domain=domain, batch_id=batch_id) is None
        ):
            raise ValueError("domain_mismatch")

        if not kb_id:
            raise ValueError("kb_id is required; domain release scope is retired")
        prev_build = asset_db.get_latest_validated_kb_build(kb_id)
        has_prev = prev_build is not None
        build_mode = determine_build_mode(has_prev)
        parent_build_id = prev_build["id"] if has_prev else None
        current_loader = getattr(asset_db, "get_current_kb_build_snapshots", None)
        parent_snapshots = (
            current_loader(kb_id)
            if current_loader is not None
            else asset_db.get_build_snapshots(parent_build_id)
            if parent_build_id is not None
            else []
        )
        parent_by_document = {
            row["document_id"]: row for row in parent_snapshots
        }

        build_id = uuid.uuid4().hex
        build_code = f"B-{uuid.uuid4().hex[:8].upper()}"

        action_counts = {}
        for d in snapshot_decisions:
            action = d.get("action", "NEW")
            action_counts[action] = action_counts.get(action, 0) + 1
        decided_doc_ids = {d["document_id"] for d in snapshot_decisions}
        has_active_selection = any(
            d.get("selection_status") == "active" for d in snapshot_decisions
        ) or any(
            parent.get("selection_status") == "active"
            and parent.get("document_id") not in decided_doc_ids
            for parent in parent_snapshots
        )

        summary: dict[str, Any] = {
            "snapshot_count": len([d for d in snapshot_decisions if d.get("selection_status") == "active"]),
            "removed_count": len([d for d in snapshot_decisions if d.get("selection_status") == "removed"]),
            "action_counts": action_counts,
        }
        if allow_empty and not has_active_selection:
            summary["operation"] = "withdrawal"
        # 批次4：把范式能力签名冻进 build——validate 据此按能力校验（只读 build 行，
        # 不再回查 run）。legacy 调用方不传 capabilities → 不冻结 → validate 降级旧检查。
        if capabilities is not None:
            summary["paradigm_capabilities"] = sorted(set(capabilities))
            summary["embedding_fallback"] = bool(embedding_fallback)
            # 能力校验只作用于本 run 产出的快照（NEW/UPDATE/RESTORE）。
            # 父 build carry-forward 的快照按其当时的能力集验收过，不重检——
            # 否则旧时代的无向量快照会把新 run 的建库整个拦死。
            summary["validated_snapshots"] = sorted({
                str(d["document_snapshot_id"])
                for d in snapshot_decisions
                if d.get("selection_status") == "active"
                and d.get("action") in ("NEW", "UPDATE", "RESTORE")
            })
        # 27号审查修复 B（24号 §7/L340）：readiness 聚合冻进 build 摘要——
        # 发布门禁与 UI 展示都以这份冻结事实为准，不回查运行态。
        if readiness_summary is not None:
            summary["readiness"] = dict(readiness_summary)

        asset_db.insert_build(
            build_id=build_id,
            build_code=build_code,
            status="building",
            build_mode=build_mode,
            domain=domain,
            source_batch_id=batch_id,
            parent_build_id=parent_build_id,
            mining_run_id=run_id,
            summary_json=summary,
            kb_id=kb_id,
        )

        # Incremental merge: carry forward parent selections not in the run
        # exactly as published, including removed and legacy-NULL provenance.
        for parent in parent_snapshots:
            if parent["document_id"] not in decided_doc_ids:
                asset_db.upsert_build_document_snapshot(
                    build_id=build_id,
                    document_id=parent["document_id"],
                    document_snapshot_id=parent["document_snapshot_id"],
                    source_batch_id=parent.get("source_batch_id"),
                    selection_status=parent["selection_status"],
                    reason=parent["reason"],
                    metadata_json=parent.get("metadata_json"),
                )
        # Add current run decisions with their effective source provenance.
        for decision in snapshot_decisions:
            parent = parent_by_document.get(decision["document_id"])
            lifecycle_action = decision.get("lifecycle_action")
            effective_action = lifecycle_action or decision.get("action", "NEW")
            if effective_action in {"NEW", "UPDATE", "RESTORE"}:
                source_batch_id = batch_id
            elif effective_action == "SKIP":
                source_batch_id = (
                    decision["source_batch_id"]
                    if "source_batch_id" in decision
                    else parent.get("source_batch_id") if parent is not None else None
                )
            elif effective_action == "REMOVE":
                source_batch_id = (
                    parent.get("source_batch_id")
                    if parent is not None
                    else decision.get("source_batch_id")
                )
            else:
                source_batch_id = batch_id

            raw_metadata = decision.get("metadata_json")
            metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
            if lifecycle_action:
                metadata.setdefault("lifecycle_action", lifecycle_action)

            asset_db.upsert_build_document_snapshot(
                build_id=build_id,
                document_id=decision["document_id"],
                document_snapshot_id=decision["document_snapshot_id"],
                source_batch_id=source_batch_id,
                selection_status=decision.get("selection_status", "active"),
                reason=decision.get("reason", "add"),
                metadata_json=metadata,
            )

        # Validate and mark as validated in the same transaction as assembly.
        validate_build(asset_db, build_id, allow_empty=allow_empty)
        asset_db.update_build_status(build_id, "validated")
    return build_id


def _build_summary(build: dict[str, Any]) -> dict[str, Any]:
    summary = build.get("summary_json") or {}
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except json.JSONDecodeError:
            summary = {}
    return summary if isinstance(summary, dict) else {}


def validate_build(
    asset_db: AssetCoreDB,
    build_id: str,
    *,
    allow_empty: bool = False,
) -> None:
    """Validate that a build meets quality requirements.

    Checks:
    1. Build has at least one active snapshot
    2. Each active snapshot has at least one segment
    3. Incremental builds must have a valid parent build
    4. 批次4 按范式能力校验：summary 冻结了 paradigm_capabilities 时——
       含 retrieval_unit_build → 每个活跃快照 ≥1 检索单元；
       含 embedding → 每个活跃快照 ≥1 向量，或 embedding_fallback 已留痕。
       legacy build（无该字段）降级为只做上面三条，行为不变。
    """
    build = asset_db.get_build(build_id)
    if build is None:
        raise ValueError(f"Build {build_id} not found")

    # Check parent build exists for incremental builds
    if build["build_mode"] == "incremental" and build["parent_build_id"]:
        parent = asset_db.get_build(build["parent_build_id"])
        if parent is None:
            raise ValueError(
                f"Incremental build {build_id} references missing parent {build['parent_build_id']}"
            )

    snapshots = asset_db.get_build_snapshots(build_id)
    active = [s for s in snapshots if s["selection_status"] == "active"]
    if not active:
        if not allow_empty:
            raise ValueError(f"Build {build_id} has no active snapshots")
        summary = _build_summary(build)
        if not isinstance(summary, dict) or summary.get("operation") != "withdrawal":
            raise ValueError(
                "Empty builds are only allowed for the withdrawal operation"
            )
        return
    for snap in active:
        count = asset_db.count_segments_by_snapshot(snap["document_snapshot_id"])
        if count == 0:
            raise ValueError(
                f"Snapshot {snap['document_snapshot_id']} has no segments"
            )

    summary = _build_summary(build)
    capabilities = summary.get("paradigm_capabilities")
    if not isinstance(capabilities, list):
        return
    validated = set(summary.get("validated_snapshots") or [])

    def _requires_check(snap: dict[str, Any]) -> bool:
        # 只校验本 run 产出的快照；carry-forward 的父快照按当时能力验收过。
        return str(snap["document_snapshot_id"]) in validated

    if "retrieval_unit_build" in capabilities:
        for snap in active:
            if not _requires_check(snap):
                continue
            snap_id = snap["document_snapshot_id"]
            if asset_db.count_retrieval_units_by_snapshot(snap_id) == 0:
                raise ValueError(
                    f"Snapshot {snap_id} has no retrieval units "
                    f"(paradigm requires retrieval_unit_build; "
                    f"validated-but-unsearchable builds are rejected)"
                )
    if "embedding" in capabilities and not summary.get("embedding_fallback"):
        for snap in active:
            if not _requires_check(snap):
                continue
            snap_id = snap["document_snapshot_id"]
            if asset_db.count_embeddings_by_snapshot(snap_id) == 0:
                raise ValueError(
                    f"Snapshot {snap_id} has no embeddings "
                    f"(paradigm requires embedding and no fallback trace "
                    f"is recorded; re-mine after restoring the embedding service)"
                )
