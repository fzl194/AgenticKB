"""Publishing stage: build + release for v1.1.

Two-phase:
- classify_documents: compare snapshots against previous active build → NEW/UPDATE/SKIP/REMOVE
- assemble_build: select snapshots, merge with previous active build (incremental or full)
- publish_release: activate a build as the current active release
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
    """
    prev_build = asset_db.get_active_build(domain=domain, channel=channel)
    prev_snapshots: dict[str, str] = {}  # document_id -> snapshot_id

    if prev_build:
        for ps in asset_db.get_build_snapshots(prev_build["id"]):
            if ps["selection_status"] == "active":
                prev_snapshots[ps["document_id"]] = ps["document_snapshot_id"]

    # Detect REMOVE: documents in prev build but not in current run
    # Skip when running incremental batches (each run = partial corpus)
    if detect_remove:
        current_doc_ids = {d["document_id"] for d in snapshot_decisions}
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
) -> str:
    """Assemble a new build from snapshot decisions with merge semantics.

    snapshot_decisions: list of dicts with keys:
        document_id, document_snapshot_id, action (NEW/UPDATE/SKIP/REMOVE),
        selection_status (active/removed), reason (add/update/retain/remove)

    Build mode is determined automatically:
    - "full" when no previous active build exists for this domain
    - "incremental" when merging with previous active build for this domain

    Returns build_id.
    """
    with asset_db.transaction():
        if (
            batch_id is not None
            and asset_db.get_source_batch(domain=domain, batch_id=batch_id) is None
        ):
            raise ValueError("domain_mismatch")

        prev_build = asset_db.get_active_build(domain=domain, channel=channel)
        has_prev = prev_build is not None
        build_mode = determine_build_mode(has_prev)
        parent_build_id = prev_build["id"] if has_prev else None
        parent_snapshots = (
            asset_db.get_build_snapshots(parent_build_id)
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

        asset_db.insert_build(
            build_id=build_id,
            build_code=build_code,
            status="building",
            build_mode=build_mode,
            domain=domain,
            source_batch_id=batch_id,
            parent_build_id=parent_build_id,
            mining_run_id=run_id,
            summary_json={
                "snapshot_count": len([d for d in snapshot_decisions if d.get("selection_status") == "active"]),
                "removed_count": len([d for d in snapshot_decisions if d.get("selection_status") == "removed"]),
                "action_counts": action_counts,
            },
            kb_id=kb_id,
        )

        # Incremental merge: carry forward parent selections not in the run
        # exactly as published, including removed and legacy-NULL provenance.
        decided_doc_ids = {d["document_id"] for d in snapshot_decisions}
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
        validate_build(asset_db, build_id)
        asset_db.update_build_status(build_id, "validated")
    return build_id


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
        summary = build.get("summary_json") or {}
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except json.JSONDecodeError:
                summary = {}
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


def publish_release(
    asset_db: AssetCoreDB,
    build_id: str,
    *,
    domain: str,
    channel: str = "prod",
    released_by: str | None = None,
    release_notes: str | None = None,
) -> str:
    """Publish a validated build as the active release.

    Returns release_id.
    """
    with asset_db.transaction():
        asset_db.acquire_domain_publish_lock(domain)

        # Re-read both build and parent release after acquiring the domain lock.
        build = asset_db.get_build(build_id)
        if build is None:
            raise ValueError(f"Build {build_id} not found")
        if build["status"] not in ("validated", "published"):
            raise ValueError(
                f"Build {build_id} status is {build['status']}, "
                "expected validated/published"
            )
        if build["domain"] != domain:
            raise ValueError(
                f"Build {build_id} belongs to domain {build['domain']!r}, "
                f"cannot publish under domain {domain!r}"
            )

        # KB-scoped builds must never enter the domain-level active release：active release
        # 是域级「一域至多一个」，发布 KB build 会 retire 掉同域其它 KB 的 release。
        # KB 知识走 KB 作用域检索（直接从 build 读），不经过这里。护栏原本只在
        # kb/routes/mining.py 入口一行 metadata.publish=false，无测试锁；此处下推到
        # 发布边界强制，任何调用方都无法绕过。
        if build.get("kb_id"):
            raise ValueError(
                f"Build {build_id} is KB-scoped (kb_id={build['kb_id']!r}); "
                "KB builds must not be published to the domain-level active release."
            )

        prev_release = asset_db.get_active_release(domain, channel)
        prev_release_id = prev_release["id"] if prev_release else None

        release_id = uuid.uuid4().hex
        release_code = f"R-{uuid.uuid4().hex[:8].upper()}"

        asset_db.insert_release(
            release_id=release_id,
            release_code=release_code,
            build_id=build_id,
            domain=domain,
            channel=channel,
            status="staging",
            previous_release_id=prev_release_id,
            released_by=released_by,
            release_notes=release_notes,
        )

        # Retire the old release and activate the new one on the same connection.
        asset_db.activate_release(release_id)

        return release_id


def demo_quality_summary(asset_db: AssetCoreDB, build_id: str) -> dict[str, Any]:
    """Generate a demo quality summary for a build.

    Checks:
    - generated_question count (warns if 0)
    - Whether question titles still carry Qn prefix
    - RST discourse relation count and type distribution

    Does NOT block release. Returns a dict to merge into build metadata.
    """
    snapshots = asset_db.get_build_snapshots(build_id)
    active_snap_ids = [s["document_snapshot_id"] for s in snapshots if s["selection_status"] == "active"]

    warnings: list[str] = []
    summary: dict[str, Any] = {"build_id": build_id}

    # 1. Count retrieval units by type
    unit_counts: dict[str, int] = {}
    for snap_id in active_snap_ids:
        rows = asset_db._fetchall(
            "SELECT unit_type, COUNT(*) as cnt FROM asset_retrieval_units "
            "WHERE document_snapshot_id = %s GROUP BY unit_type",
            (snap_id,),
        )
        for r in rows:
            unit_counts[r["unit_type"]] = unit_counts.get(r["unit_type"], 0) + r["cnt"]

    summary["unit_type_counts"] = unit_counts
    q_count = unit_counts.get("generated_question", 0)
    if q_count == 0:
        warnings.append("No generated_question units found")
    summary["generated_question_count"] = q_count

    # 2. Check for Qn-prefixed question titles
    q_prefix_count = 0
    for snap_id in active_snap_ids:
        rows = asset_db._fetchall(
            "SELECT COUNT(*) as cnt FROM asset_retrieval_units "
            "WHERE document_snapshot_id = %s AND unit_type = 'generated_question' "
            "AND title LIKE 'Q%%'",
            (snap_id,),
        )
        for r in rows:
            q_prefix_count += r["cnt"]
    if q_prefix_count > 0:
        warnings.append(f"{q_prefix_count} question titles still have Qn prefix")
    summary["qn_prefix_count"] = q_prefix_count

    # 3. RST discourse relation distribution
    discourse_counts: dict[str, int] = {}
    for snap_id in active_snap_ids:
        rows = asset_db._fetchall(
            "SELECT relation_type, COUNT(*) as cnt FROM asset_raw_segment_relations "
            "WHERE document_snapshot_id = %s "
            "AND (metadata_json::jsonb)->>'source' = 'discourse_llm' "
            "GROUP BY relation_type",
            (snap_id,),
        )
        for r in rows:
            discourse_counts[r["relation_type"]] = discourse_counts.get(r["relation_type"], 0) + r["cnt"]

    summary["discourse_relation_counts"] = discourse_counts
    total_discourse = sum(discourse_counts.values())
    if total_discourse == 0:
        warnings.append("No discourse relations found")

    summary["warnings"] = warnings
    if warnings:
        logger.warning("Demo quality summary for build %s: %s", build_id[:8], warnings)

    return summary
