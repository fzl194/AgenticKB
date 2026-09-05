"""36号：文档级原子入库——staged≠committed + finalize 分区 + 状态透传.

锁定契约（brief §六/§十一/§八）：
- asset_persist 成功只 stage（identity 写入、status 保持 processing），
  不再提前 committed；
- document_persist_marker 按 identity（committed|processing）判定——
  crash resume 依赖 identity+node event，不信 status='committed'；
- _rebuild_from_run_documents 产出三分区（candidates/skip/failed），
  v1.0.1 历史 committed 行按 staged 候选处理（兼容恢复）；
- _finalize_run 文档级 readiness 分区：单篇失败只拒该篇，其余照常晋升
  建 Build；rejected 写明确原因；全失败无 parent → 不建 Build、run failed；
- finalize handler 按 summary 真实透传 SUCCESS/FAILED；
- resume 强制 replay mining_finalize（不复用旧 completed 事件）。
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

# ───────────────────── 1. asset_persist 只 stage 不 commit ─────────────────────


def test_restarting_failed_document_clears_previous_terminal_error() -> None:
    from knowledge_mining.mining.runtime import RuntimeTracker

    class _DB:
        def __init__(self):
            self.kwargs = {}

        def update_run_document(self, rd_id, **kwargs):
            assert rd_id == "rd-retry"
            self.kwargs = kwargs

    db = _DB()
    RuntimeTracker(db).start_document("rd-retry", retry_required=True)

    assert db.kwargs["status"] == "processing"
    assert db.kwargs["clear_error_message"] is True
    assert db.kwargs["clear_finished_at"] is True
    assert db.kwargs["metadata_patch"] == {"retry_required": True}


class _FakeRuntime:
    def __init__(self, services, repository, manifest=None):
        self.services = services
        self.runtime_repository = repository
        self.manifest = manifest or {"runId": "run-1"}


class _FakePersistOutcome:
    document_id = "doc-1"
    snapshot_ref = "snap-1"
    readiness = {"search_ready": True}
    schema_version = "v1"
    tokenizer_version = "t1"


class _FakePersistService:
    def __init__(self):
        self.calls: list[str] = []

    def persist_for_snapshot(self, *, snapshot_id, document_ref):
        self.calls.append(snapshot_id)
        return _FakePersistOutcome()


class _RecordingServices:
    def __init__(self):
        self.asset_persist_service = _FakePersistService()
        self.staged: list[tuple[str, str, str]] = []
        self.committed: list[tuple[str, str, str]] = []

    def stage_document(self, rd_id, document_id, snapshot_id):
        self.staged.append((rd_id, document_id, snapshot_id))

    def commit_document(self, rd_id, document_id, snapshot_id):
        self.committed.append((rd_id, document_id, snapshot_id))


class _MarkerRepo:
    def __init__(self, marker=None):
        self._marker = marker

    def document_persist_marker(self, rd_id):
        return self._marker


def test_asset_persist_stages_instead_of_committing():
    from knowledge_mining.mining.workflow.core import DocumentState
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle
    from knowledge_mining.mining.workflow.handlers.persist import (
        asset_persist_handler,
    )

    services = _RecordingServices()
    bundle = MiningDocumentBundle(
        document_ref="doc.md", run_document_id="rd-1",
        snapshot_ref="snap-1", document_id="doc-1",
    )
    state = DocumentState("rd-1", "doc:/doc.md", bundle)
    runtime = _FakeRuntime(services, _MarkerRepo())

    result = asset_persist_handler(state, {}, runtime)

    assert result.status.name == "SUCCESS"
    assert services.staged == [("rd-1", "doc-1", "snap-1")]
    # 36号根因 2：staging 完成不等于入库——不提前 committed
    assert services.committed == []


def test_marker_identity_sql_accepts_processing_rows():
    from knowledge_mining.mining.workflow.repositories.domain_run_repository import (
        DomainRunRepository,
    )

    pool = _RecordingPool()
    repo = DomainRunRepository(pool)
    repo.document_persist_marker("rd-1")
    sql = pool.log[-1][0]
    assert "document_id IS NOT NULL" in sql
    assert "document_snapshot_id IS NOT NULL" in sql
    assert "status IN ('committed', 'processing', 'skipped')" in sql


# ───────────────────── 2. _rebuild 三分区（staged 语义） ─────────────────────


def _rd(rd_id, status, *, action="UPDATE", document_id=None, snapshot_id=None,
        key=None, error=None, meta=None):
    return {
        "id": rd_id,
        "status": status,
        "action": action,
        "document_id": document_id,
        "document_snapshot_id": snapshot_id,
        "document_key": key or f"doc:/{rd_id}",
        "error_message": error,
        "metadata_json": meta or {},
    }


class _FakeRuntimeDB:
    def __init__(self, rows):
        self._rows = rows

    def get_run_documents(self, run_id):
        return list(self._rows)


def test_rebuild_partitions_staged_skip_failed():
    from knowledge_mining.mining.jobs.run import _rebuild_from_run_documents

    rows = [
        # staged（新语义：processing + identity）
        _rd("rd-1", "processing", document_id="doc-1", snapshot_id="snap-1",
            action="UPDATE"),
        # v1.0.1 历史行：committed + identity（非 SKIP）→ 同样是 staged 候选
        _rd("rd-2", "committed", document_id="doc-2", snapshot_id="snap-2",
            action="NEW"),
        # ingest SKIP（skipped + identity + action SKIP）→ skip 决策
        _rd("rd-3", "skipped", action="SKIP", document_id="doc-3",
            snapshot_id="snap-3", meta={"lifecycle_action": "SKIP"}),
        # 处理中但无 identity（persist 前崩溃）→ 僵尸行（MED-3：落终态）
        _rd("rd-4", "processing"),
        # 失败
        _rd("rd-5", "failed", document_id="doc-5", error="embedding failed"),
        # 真跳过（解析跳过等）
        _rd("rd-6", "skipped"),
    ]
    index = _rebuild_from_run_documents(_FakeRuntimeDB(rows), "run-1")

    candidate_docs = {c["document_id"] for c in index.candidates}
    assert candidate_docs == {"doc-1", "doc-2"}
    assert {d["document_id"] for d in index.skip_decisions} == {"doc-3"}
    assert {f["document_id"] for f in index.failed} == {"doc-5", None}
    assert {z["run_document_id"] for z in index.zombies} == {"rd-4"}
    assert index.counts["failed_count"] == 3  # rd-5 + 僵尸 rd-4 + 必需链 SKIPPED rd-6
    assert index.counts["skipped_count"] == 1  # 仅 rd-3（SKIP committed carry）
    assert index.counts["new_count"] == 1
    assert index.counts["updated_count"] == 1


def test_legacy_finalize_inputs_adapt_partition_index():
    """HIGH-1（36号审查）：_resume_legacy 不得再按二元组解包 _rebuild
    的返回值——legacy 适配函数把三分区 index 转回旧输入形状。"""
    from knowledge_mining.mining.jobs.run import (
        _legacy_finalize_inputs,
        _rebuild_from_run_documents,
    )

    rows = [
        _rd("rd-1", "committed", document_id="doc-1", snapshot_id="snap-1",
            action="NEW"),
        _rd("rd-2", "committed", action="SKIP", document_id="doc-2",
            snapshot_id="snap-2"),
    ]
    index = _rebuild_from_run_documents(_FakeRuntimeDB(rows), "run-1")
    decisions, counts = _legacy_finalize_inputs(index)
    assert [d["document_snapshot_id"] for d in decisions] == ["snap-1", "snap-2"]
    assert counts["committed_count"] == 1
    assert counts["new_count"] == 1


# ───────────────────── 3. _finalize_run 文档级分区 ─────────────────────


class _PartitionAssetDB:
    """分区 finalize 所需的 AssetCoreDB 最小双打."""

    def __init__(self, *, readiness=None, staging_readiness=None, kb_build=None,
                 current_run_build=None, build_snapshots=None, segments=2,
                 units=1, embeddings=1, current_document_ids=None):
        self.readiness_rows = readiness or {}
        self.staging_readiness_rows = (
            dict(self.readiness_rows)
            if staging_readiness is None else dict(staging_readiness)
        )
        self.kb_build = kb_build
        self.current_run_build = current_run_build
        self.current_document_ids = set(current_document_ids or ())
        self._build_snapshots = build_snapshots or {}
        self._segments = segments
        self._units = units
        self._embeddings = embeddings
        self.promoted: list[list[str]] = []
        self.inserted_builds: list[dict] = []
        self.status_updates: list[tuple[str, str]] = []
        self._next_snap: dict[str, list[dict]] = {}
        self.builds: dict[str, dict] = {}

    @contextmanager
    def transaction(self):
        yield

    def acquire_domain_publish_lock(self, domain):
        return None

    def fetch_snapshot_readiness(self, snapshot_ids):
        return {
            sid: self.readiness_rows[sid]
            for sid in snapshot_ids if sid in self.readiness_rows
        }

    def fetch_snapshot_readiness_staging(self, snapshot_ids):
        return {
            sid: self.staging_readiness_rows[sid]
            for sid in snapshot_ids if sid in self.staging_readiness_rows
        }

    def promote_snapshot_assets(self, snapshot_ids):
        self.promoted.append(list(snapshot_ids))

    def get_latest_validated_kb_build(self, kb_id):
        return self.kb_build

    def get_validated_build_for_run(self, run_id):
        return self.current_run_build

    def get_current_kb_document_ids(self, kb_id):
        return set(self.current_document_ids)

    def get_active_build(self, *, domain, channel):
        return None

    def get_build_snapshots(self, build_id):
        if build_id in self._next_snap:
            return self._next_snap[build_id]
        return self._build_snapshots.get(build_id, [])

    def get_build(self, build_id):
        if build_id in self.builds:
            return self.builds[build_id]
        if self.kb_build and self.kb_build.get("id") == build_id:
            return self.kb_build
        if self.current_run_build and self.current_run_build.get("id") == build_id:
            return self.current_run_build
        return None

    def insert_build(self, **kwargs):
        self.inserted_builds.append(kwargs)
        self.builds[kwargs["build_id"]] = {
            "id": kwargs["build_id"],
            "build_mode": kwargs.get("build_mode", "full"),
            "parent_build_id": kwargs.get("parent_build_id"),
            "status": kwargs.get("status", "building"),
            "summary_json": kwargs.get("summary_json") or {},
            "kb_id": kwargs.get("kb_id"),
            "domain": kwargs.get("domain"),
        }
        return kwargs["build_id"]

    def upsert_build_document_snapshot(self, **kwargs):
        self._next_snap.setdefault(kwargs["build_id"], []).append(kwargs)

    def update_build_status(self, build_id, status):
        self.status_updates.append((build_id, status))
        if build_id in self.builds:
            self.builds[build_id]["status"] = status

    def count_segments_by_snapshot(self, snapshot_id):
        return self._segments

    def count_retrieval_units_by_snapshot(self, snapshot_id):
        return self._units

    def count_embeddings_by_snapshot(self, snapshot_id):
        return self._embeddings

    def get_source_batch(self, *, domain, batch_id):
        return {"id": batch_id, "domain": domain}


class _PartitionTracker:
    def __init__(self):
        self.phases: list[str] = []
        self.stages: list[str] = []
        self.committed: list[tuple[str, str, str]] = []
        self.failed_docs: list[tuple[str, str]] = []
        self.run_status: dict[str, Any] = {}
        self.completed: list[dict] = {}
        self.failed_runs: list[dict] = {}

    def set_run_phase(self, run_id, domain, stage, status="running"):
        self.phases.append(stage)
        return True

    def start_stage(self, run_id, stage, rd_id=None):
        self.stages.append(stage)
        return f"evt-{len(self.stages)}"

    def end_stage(self, evt, run_id, stage, status="completed",
                  output_summary=None, error_message=None):
        return None

    def commit_document(self, rd_id, document_id, snapshot_id):
        self.committed.append((rd_id, document_id, snapshot_id))

    def fail_document(self, rd_id, message):
        self.failed_docs.append((rd_id, message))

    def complete_run(self, run_id, **kwargs):
        self.completed = kwargs
        return True

    def fail_run(self, run_id, error_summary, **kwargs):
        self.failed_runs = {"error_summary": error_summary, **kwargs}
        return True


class _RunRowDB:
    def __init__(self, row, operator_statuses=()):
        self._row = row
        self._operator_statuses = list(operator_statuses)

    def get_run(self, run_id):
        return dict(self._row)

    def _fetchone(self, sql, params=()):
        if "SELECT status FROM mining_runs" in sql:
            return {"status": "running"}
        return None

    def operator_statuses_for_run(self, run_id):
        return list(self._operator_statuses)

    def commit(self):
        return None

    def update_run_status(self, *args, **kwargs):
        return True


def _run_finalize(asset_db, *, document_index, run_meta=None,
                  manifest_nodes=("asset_persist", "embedding"),
                  phase1_only=False, operator_statuses=()):
    """组装 _finalize_run 的最小调用环境（workflow/KB 场景）."""
    from types import SimpleNamespace

    from knowledge_mining.mining.jobs.run import _finalize_run

    profile = SimpleNamespace(domain_id="odn")
    run_row = {
        "id": "run-1",
        "metadata_json": {"publish": False, **(run_meta or {})},
        "workflow_manifest_json": {
            "nodes": [{"type": t} for t in manifest_nodes],
        },
        "workflow_version_id": "wfv-1",
        "workflow_graph_hash": "gh-1",
    }
    runtime_db = _RunRowDB(run_row, operator_statuses)
    tracker = _PartitionTracker()
    summary = _finalize_run(
        asset_db,
        runtime_db,
        tracker,
        "run-1",
        "batch-1",
        document_index.skip_decisions if document_index else [],
        (document_index.counts if document_index else {
            "committed_count": 0, "new_count": 0, "updated_count": 0,
            "failed_count": 0, "skipped_count": 0,
        }),
        total_documents=3,
        phase1_only=phase1_only,
        publish_on_partial_failure=False,
        profile=profile,
        channel="prod",
        document_index=document_index,
    )
    return summary, tracker


def _index(candidates, *, skips=(), failed=(), zombies=(), new=0, updated=0):
    from types import SimpleNamespace

    return SimpleNamespace(
        candidates=candidates,
        skip_decisions=list(skips),
        failed=list(failed),
        zombies=list(zombies),
        counts={
            "new_count": new, "updated_count": updated,
            "failed_count": len(failed) + len(zombies),
            "skipped_count": len(skips),
        },
    )


def _candidate(rd, doc, snap, action="UPDATE"):
    return {
        "run_document_id": rd, "document_id": doc,
        "document_snapshot_id": snap, "action": action,
        "document_key": f"doc:/{doc}",
    }


_READY = {"search_ready": True, "counts": {"dense_units": 2, "dense_covered": 2}}


def test_partition_promotes_ready_and_rejects_incomplete():
    asset_db = _PartitionAssetDB(readiness={
        "snap-1": _READY, "snap-2": _READY,
        # snap-3：dense 未覆盖 → 该篇拒绝
        "snap-3": {"search_ready": True,
                   "counts": {"dense_units": 5, "dense_covered": 2}},
    })
    index = _index(
        [
            _candidate("rd-1", "doc-1", "snap-1"),
            _candidate("rd-2", "doc-2", "snap-2", action="NEW"),
            _candidate("rd-3", "doc-3", "snap-3"),
        ],
        new=1, updated=2,
    )
    summary, tracker = _run_finalize(asset_db, document_index=index)

    # 只有 ready 快照被晋升
    assert asset_db.promoted == [["snap-1", "snap-2"]]
    # rejected 文档：fail_document + 明确原因；不 committed
    failed_docs = dict(tracker.failed_docs)
    assert "rd-3" in failed_docs
    assert "embedding_incomplete" in failed_docs["rd-3"]
    assert all(rd != "rd-3" for rd, _, _ in tracker.committed)
    # ready 文档在 Build 事务成功后才 committed
    assert {(rd, doc) for rd, doc, _ in tracker.committed} == {
        ("rd-1", "doc-1"), ("rd-2", "doc-2"),
    }
    # Build 建了，包含两篇
    build = asset_db.inserted_builds[0]
    members = {
        row["document_id"]
        for row in asset_db.get_build_snapshots(build["build_id"])
    }
    assert members == {"doc-1", "doc-2"}
    # Run 部分成功：completed + has_failures
    assert summary["status"] == "completed"
    assert summary["build_id"] == build["build_id"]
    assert summary["committed_count"] == 2
    assert summary["failed_count"] == 1
    assert summary["partial_success"] is True
    assert summary["rejection_summary"][0]["reason"] == "embedding_incomplete"


def test_partition_all_rejected_without_parent_fails_without_build():
    asset_db = _PartitionAssetDB(readiness={})  # readiness 全缺
    index = _index([_candidate("rd-1", "doc-1", "snap-1")], updated=1)
    summary, tracker = _run_finalize(asset_db, document_index=index)

    assert summary["status"] == "failed"
    assert summary["build_id"] is None
    assert asset_db.inserted_builds == []
    assert asset_db.promoted == []
    assert dict(tracker.failed_docs)["rd-1"]
    assert "readiness_missing" in dict(tracker.failed_docs)["rd-1"]


def test_partition_all_rejected_with_parent_keeps_previous_version():
    asset_db = _PartitionAssetDB(
        readiness={},
        current_document_ids={"doc-1"},
        kb_build={"id": "build-old", "kb_id": "kb-a", "status": "validated",
                  "build_mode": "full", "finished_at": "2026-09-01T00:00:00+00:00"},
        build_snapshots={"build-old": [{
            "document_id": "doc-1", "document_snapshot_id": "snap-old",
            "selection_status": "active", "source_batch_id": "b0",
            "reason": "retain", "metadata_json": None,
        }]},
    )
    index = _index([_candidate("rd-1", "doc-1", "snap-1")], updated=1)
    summary, tracker = _run_finalize(
        asset_db, document_index=index, run_meta={"kb_id": "kb-a"},
    )

    # 已有 parent：不新建 Build（旧 Build 保持当前可用），run 失败但检索不破坏
    assert summary["status"] == "failed"
    assert asset_db.inserted_builds == []
    assert asset_db.promoted == []
    assert dict(tracker.failed_docs)["rd-1"]


def test_partition_readiness_missing_reason_distinguished():
    asset_db = _PartitionAssetDB(readiness={
        "snap-1": {"search_ready": False,
                   "counts": {"dense_units": 1, "dense_covered": 1}},
    })
    index = _index([_candidate("rd-1", "doc-1", "snap-1")], updated=1)
    summary, tracker = _run_finalize(asset_db, document_index=index)
    assert summary["rejection_summary"][0]["reason"] == "search_not_ready"


def test_final_only_readiness_is_not_promotable_staging():
    """final readiness proves an older activation, not that this Run still owns
    promotable staging.  Resume must reject safely instead of deleting final and
    copying zero staging rows over it."""
    asset_db = _PartitionAssetDB(
        readiness={"snap-1": _READY},
        staging_readiness={},
    )
    index = _index([_candidate("rd-1", "doc-1", "snap-1")], updated=1)

    summary, tracker = _run_finalize(asset_db, document_index=index)

    assert summary["status"] == "failed"
    assert summary["build_id"] is None
    assert asset_db.promoted == []
    assert summary["rejection_summary"][0]["reason"] == "readiness_missing"
    assert "readiness_missing" in dict(tracker.failed_docs)["rd-1"]


def test_existing_validated_build_for_run_only_reconciles_document_statuses():
    """Crash after the asset transaction but before run-document updates: resume
    must use the already validated Build as its idempotency record and never
    promote the now-empty staging tables again."""
    current = {
        "id": "build-current", "kb_id": "kb-a", "status": "validated",
        "mining_run_id": "run-1",
    }
    asset_db = _PartitionAssetDB(
        readiness={"snap-ready": _READY},
        staging_readiness={},
        current_run_build=current,
        kb_build=current,
        build_snapshots={"build-current": [
            {"document_id": "doc-ready", "document_snapshot_id": "snap-ready",
             "selection_status": "active", "source_batch_id": "batch-1",
             "reason": "update",
             "metadata_json": {"produced_by_run_id": "run-1"}},
        ]},
    )
    index = _index([
        _candidate("rd-ready", "doc-ready", "snap-ready"),
        _candidate("rd-rejected", "doc-rejected", "snap-rejected"),
    ], updated=2)

    summary, tracker = _run_finalize(
        asset_db, document_index=index, run_meta={"kb_id": "kb-a"},
    )

    assert summary["status"] == "completed"
    assert summary["build_id"] == "build-current"
    assert asset_db.promoted == []
    assert asset_db.inserted_builds == []
    assert tracker.committed == [("rd-ready", "doc-ready", "snap-ready")]
    assert "not_in_validated_build" in dict(tracker.failed_docs)["rd-rejected"]


def test_reconcile_does_not_commit_rejected_snapshot_carried_from_parent():
    """相同 snapshot 被 parent carry 不代表本轮候选通过了 readiness。"""
    current = {
        "id": "build-current", "kb_id": "kb-a", "status": "validated",
        "mining_run_id": "run-1",
    }
    asset_db = _PartitionAssetDB(
        current_run_build=current,
        kb_build=current,
        build_snapshots={"build-current": [{
            "document_id": "doc-1", "document_snapshot_id": "snap-shared",
            "selection_status": "active", "source_batch_id": "batch-old",
            "reason": "retain", "metadata_json": {},
        }]},
    )
    index = _index([
        _candidate("rd-1", "doc-1", "snap-shared"),
    ], updated=1)

    summary, tracker = _run_finalize(
        asset_db, document_index=index, run_meta={"kb_id": "kb-a"},
    )

    assert tracker.committed == []
    assert "not_in_validated_build" in dict(tracker.failed_docs)["rd-1"]


def test_partition_rejection_lookup_does_not_compare_every_candidate_pair():
    """Rejected candidates are already known while partitioning; a later nested
    scan over rejection_summary is both unused and quadratic at 19,789 docs."""
    class _NoEqualityId:
        def __init__(self, value):
            self.value = value

        def __str__(self):
            return self.value

        def __hash__(self):
            return hash(self.value)

        def __eq__(self, other):
            raise AssertionError("quadratic document-id comparison")

    doc_id = _NoEqualityId("doc-1")
    asset_db = _PartitionAssetDB(readiness={}, staging_readiness={})
    index = _index([_candidate("rd-1", doc_id, "snap-1")], updated=1)

    summary, tracker = _run_finalize(asset_db, document_index=index)

    assert summary["failed_count"] == 1


def test_partition_all_skipped_without_new_build_completes_cleanly():
    asset_db = _PartitionAssetDB(readiness={})
    index = _index([], skips=[{
        "document_id": "doc-1", "document_snapshot_id": "snap-1",
        "document_key": "doc:/doc-1", "lifecycle_action": "SKIP",
    }])

    summary, tracker = _run_finalize(asset_db, document_index=index)

    assert summary["status"] == "completed"
    assert summary["build_id"] is None
    assert summary["committed_count"] == 0
    assert summary["failed_count"] == 0
    assert summary["skipped_count"] == 1
    assert asset_db.promoted == []
    assert tracker.failed_runs == {}


def test_required_operator_skipped_without_build_fails_run():
    from knowledge_mining.mining.jobs.run import _rebuild_from_run_documents

    runtime_db = _FakeRuntimeDB([
        _rd("rd-1", "skipped", action="NEW", key="doc:/empty.md",
            error="document finished without asset persistence"),
    ])
    index = _rebuild_from_run_documents(runtime_db, "run-1")
    asset_db = _PartitionAssetDB(readiness={})

    summary, tracker = _run_finalize(asset_db, document_index=index)

    assert summary["status"] == "failed"
    assert summary["build_id"] is None
    assert summary["failed_count"] == 1
    assert summary["skipped_count"] == 0
    assert "rd-1" in dict(tracker.failed_docs)


def test_full_kb_build_marks_parent_documents_absent_from_input_removed():
    parent = {"id": "build-old", "kb_id": "kb-a", "status": "validated",
              "build_mode": "full"}
    asset_db = _PartitionAssetDB(
        readiness={"snap-new": _READY}, kb_build=parent,
        current_document_ids={"doc-keep"},
        build_snapshots={"build-old": [
            {"document_id": "doc-keep", "document_snapshot_id": "snap-old",
             "selection_status": "active", "source_batch_id": "batch-old",
             "reason": "retain", "metadata_json": {}},
            {"document_id": "doc-removed", "document_snapshot_id": "snap-removed",
             "selection_status": "active", "source_batch_id": "batch-old",
             "reason": "retain", "metadata_json": {}},
        ]},
    )
    index = _index([
        _candidate("rd-keep", "doc-keep", "snap-new"),
    ], updated=1)

    summary, _ = _run_finalize(
        asset_db, document_index=index, run_meta={"kb_id": "kb-a"},
    )

    members = asset_db.get_build_snapshots(summary["build_id"])
    removed = next(row for row in members if row["document_id"] == "doc-removed")
    assert removed["selection_status"] == "removed"


def test_full_kb_deletion_only_run_creates_removal_build():
    parent = {"id": "build-old", "kb_id": "kb-a", "status": "validated",
              "build_mode": "full"}
    asset_db = _PartitionAssetDB(
        readiness={}, kb_build=parent, current_document_ids=set(),
        build_snapshots={"build-old": [{
            "document_id": "doc-deleted", "document_snapshot_id": "snap-old",
            "selection_status": "active", "source_batch_id": "batch-old",
            "reason": "retain", "metadata_json": {},
        }]},
    )
    index = _index([])

    summary, _ = _run_finalize(
        asset_db, document_index=index, run_meta={"kb_id": "kb-a"},
    )

    assert summary["build_id"] is not None
    members = asset_db.get_build_snapshots(summary["build_id"])
    removed = next(row for row in members if row["document_id"] == "doc-deleted")
    assert removed["selection_status"] == "removed"
    assert removed["reason"] == "remove"


def test_selective_kb_build_carries_unselected_parent_document_active():
    parent = {"id": "build-old", "kb_id": "kb-a", "status": "validated",
              "build_mode": "full"}
    asset_db = _PartitionAssetDB(
        readiness={"snap-new": _READY}, kb_build=parent,
        build_snapshots={"build-old": [
            {"document_id": "doc-selected", "document_snapshot_id": "snap-old",
             "selection_status": "active", "source_batch_id": "batch-old",
             "reason": "retain", "metadata_json": {}},
            {"document_id": "doc-unselected", "document_snapshot_id": "snap-unselected",
             "selection_status": "active", "source_batch_id": "batch-old",
             "reason": "retain", "metadata_json": {}},
        ]},
    )
    index = _index([
        _candidate("rd-selected", "doc-selected", "snap-new"),
    ], updated=1)

    summary, _ = _run_finalize(
        asset_db, document_index=index,
        run_meta={"kb_id": "kb-a", "document_ids": ["doc-selected"]},
    )

    members = asset_db.get_build_snapshots(summary["build_id"])
    unselected = next(
        row for row in members if row["document_id"] == "doc-unselected"
    )
    assert unselected["selection_status"] == "active"


def test_full_kb_build_carries_rejected_update_parent_snapshot_active():
    parent = {"id": "build-old", "kb_id": "kb-a", "status": "validated",
              "build_mode": "full"}
    asset_db = _PartitionAssetDB(
        readiness={
            "snap-ready": _READY,
            "snap-bad": {"search_ready": True,
                         "counts": {"dense_units": 2, "dense_covered": 0}},
        },
        kb_build=parent,
        current_document_ids={"doc-ready", "doc-rejected"},
        build_snapshots={"build-old": [
            {"document_id": "doc-ready", "document_snapshot_id": "snap-ready-old",
             "selection_status": "active", "source_batch_id": "batch-old",
             "reason": "retain", "metadata_json": {}},
            {"document_id": "doc-rejected", "document_snapshot_id": "snap-old-good",
             "selection_status": "active", "source_batch_id": "batch-old",
             "reason": "retain", "metadata_json": {}},
        ]},
    )
    index = _index([
        _candidate("rd-ready", "doc-ready", "snap-ready"),
        _candidate("rd-rejected", "doc-rejected", "snap-bad"),
    ], updated=2)

    summary, _ = _run_finalize(
        asset_db, document_index=index, run_meta={"kb_id": "kb-a"},
    )

    members = asset_db.get_build_snapshots(summary["build_id"])
    rejected = next(
        row for row in members if row["document_id"] == "doc-rejected"
    )
    assert rejected["document_snapshot_id"] == "snap-old-good"
    assert rejected["selection_status"] == "active"


def test_assets_only_keeps_ready_documents_staged_not_committed():
    asset_db = _PartitionAssetDB(readiness={"snap-1": _READY})
    index = _index([_candidate("rd-1", "doc-1", "snap-1")], updated=1)

    summary, tracker = _run_finalize(
        asset_db, document_index=index, phase1_only=True,
    )

    assert summary["status"] == "completed"
    assert summary["build_id"] is None
    assert summary["staged_count"] == 1
    assert summary["committed_count"] == 0
    assert tracker.committed == []
    assert asset_db.promoted == []


def test_rejected_embedding_fallback_does_not_taint_ready_build_summary():
    asset_db = _PartitionAssetDB(readiness={
        "snap-ready": _READY,
        "snap-bad": {"search_ready": True,
                     "counts": {"dense_units": 2, "dense_covered": 0}},
    })
    index = _index([
        _candidate("rd-ready", "doc-ready", "snap-ready"),
        _candidate("rd-bad", "doc-bad", "snap-bad"),
    ], updated=2)

    summary, _ = _run_finalize(
        asset_db,
        document_index=index,
        operator_statuses=[{"operator_type": "embedding", "status": "fallback"}],
    )

    assert summary["build_id"] is not None
    assert asset_db.inserted_builds[0]["summary_json"]["embedding_fallback"] is False


def test_partition_skip_carry_with_all_candidates_rejected_stays_completed():
    """MED-2（36号审查）：SKIP carry 与失败并存 → run completed（旧 Build
    持续可用），与 finalize handler 判定一致——不得出现「run 行 failed、
    finalize 节点绿」的矛盾。"""
    asset_db = _PartitionAssetDB(readiness={})  # 候选全拒
    index = _index(
        [_candidate("rd-1", "doc-1", "snap-1")],
        skips=[{
            "document_id": "doc-2", "document_snapshot_id": "snap-keep",
            "document_key": "doc:/doc-2", "lifecycle_action": "SKIP",
        }],
        updated=1,
    )
    summary, tracker = _run_finalize(asset_db, document_index=index)
    assert summary["status"] == "completed"
    assert summary["build_id"] is None  # 无 ready → 不新建 Build
    assert summary["partial_success"] is True
    assert summary["failed_count"] == 1
    assert summary["skipped_count"] == 1


def test_partition_zombie_documents_marked_failed():
    """MED-3（36号审查）：persist 前崩溃的僵尸行落 interrupted_before_persist
    终态——不留「永远处理中」。"""
    asset_db = _PartitionAssetDB(readiness={"snap-1": _READY})
    from types import SimpleNamespace as _NS
    index = _NS(
        candidates=[_candidate("rd-1", "doc-1", "snap-1")],
        skip_decisions=[],
        failed=[],
        zombies=[{"run_document_id": "rd-9", "document_id": None,
                  "document_key": "doc:/zombie"}],
        counts={"new_count": 0, "updated_count": 1, "failed_count": 0,
                "skipped_count": 0},
    )
    summary, tracker = _run_finalize(asset_db, document_index=index)
    failed_msgs = dict(tracker.failed_docs)
    assert "rd-9" in failed_msgs
    assert "interrupted_before_persist" in failed_msgs["rd-9"]
    assert any(
        item["reason"] == "interrupted_before_persist"
        for item in summary["rejection_summary"]
    )


# ───────────────────── 4. finalize handler 状态透传 ─────────────────────


class _FinalizeRuntime:
    def __init__(self, services, manifest=None):
        self.services = services
        self.manifest = manifest or {
            "runId": "run-1",
            "executionPlan": {"requiredCompletion": ["assets_persisted"]},
            "runtimeBinding": {},
        }


class _FinalizeServices:
    def __init__(self, summary, execution_mode="publish"):
        self._summary = summary
        self.execution_mode = execution_mode

    def finalize_mining(self, run_id, *, execution_mode,
                        publish_on_partial_failure):
        return self._summary


def test_finalize_handler_success_when_build_created():
    from knowledge_mining.mining.workflow.handlers.finalize import (
        mining_finalize_handler,
    )

    state = SimpleNamespace(capabilities=frozenset({"assets_persisted"}))
    summary = {"status": "completed", "build_id": "b-1", "release_id": None,
               "partial_success": True, "failed_count": 1,
               "committed_count": 2, "skipped_count": 0}
    result = mining_finalize_handler(
        state, {}, _FinalizeRuntime(_FinalizeServices(summary)),
    )
    assert result.status.name == "SUCCESS"
    assert "finalized" in result.capabilities


def test_finalize_handler_failed_when_no_build_with_candidates():
    from knowledge_mining.mining.workflow.handlers.finalize import (
        mining_finalize_handler,
    )

    state = SimpleNamespace(capabilities=frozenset({"assets_persisted"}))
    summary = {"status": "failed", "build_id": None, "release_id": None,
               "committed_count": 0, "failed_count": 9, "skipped_count": 0,
               "rejection_summary": [{"reason": "embedding_incomplete"}]}
    result = mining_finalize_handler(
        state, {}, _FinalizeRuntime(_FinalizeServices(summary)),
    )
    assert result.status.name == "FAILED"
    assert result.error_code == "finalize_no_build"


def test_finalize_handler_assets_only_accepts_staged_partial_result():
    from knowledge_mining.mining.workflow.handlers.finalize import (
        mining_finalize_handler,
    )

    state = SimpleNamespace(capabilities=frozenset({"assets_persisted"}))
    summary = {
        "status": "completed", "build_id": None, "release_id": None,
        "staged_count": 2, "committed_count": 0, "failed_count": 1,
        "skipped_count": 0, "partial_success": True,
        "rejection_summary": [{"reason": "embedding_incomplete"}],
    }
    result = mining_finalize_handler(
        state, {},
        _FinalizeRuntime(_FinalizeServices(summary, execution_mode="assets_only")),
    )

    assert result.status.name == "SUCCESS"
    assert "finalized" in result.capabilities


def test_finalize_handler_does_not_turn_cancelled_summary_green():
    from knowledge_mining.mining.workflow.handlers.finalize import (
        mining_finalize_handler,
    )

    state = SimpleNamespace(capabilities=frozenset({"assets_persisted"}))
    summary = {"status": "cancelled", "build_id": None}
    result = mining_finalize_handler(
        state, {}, _FinalizeRuntime(_FinalizeServices(summary)),
    )

    assert result.status.name == "FAILED"
    assert result.error_code == "finalize_cancelled"


# ───────────────────── 5. resume 强制 replay finalize ─────────────────────


def test_resume_replays_finalize_node():
    from knowledge_mining.mining.workflow.runtime import MiningWorkflowRuntime

    class _Plan:
        global_order = ("n_review", "n_finalize")
        document_order = ()
        input_order = ()

        @staticmethod
        def node(node_id):
            return SimpleNamespace(
                operator_type=(
                    "mining_finalize" if node_id == "n_finalize" else "other"
                ),
            )

    class _Ctx:
        class services:
            run_id = "run-1"

    runtime = MiningWorkflowRuntime(_Ctx(), run_id="run-1")
    replay = runtime._resume_replay_nodes(_Plan())
    assert replay == frozenset({"n_finalize"})


# ───────────────────── 工具 ─────────────────────


class _RecordingPool:
    def __init__(self) -> None:
        self.log: list[tuple[str, tuple]] = []

    @contextmanager
    def connection(self):
        outer = self

        class _Cur:
            def execute(self, sql, params):
                outer.log.append((sql, tuple(params) if params else ()))

            def fetchone(self):
                return None

            def fetchall(self):
                return []

            rowcount = 0

        class _Conn:
            # DomainRunRepository 走 conn.execute(...) 直连风格：execute
            # 即执行——在此记录并返回可 fetchone 的游标。
            def execute(self, sql, params):
                outer.log.append((sql, tuple(params) if params else ()))
                return _Cur()

        yield _Conn()
