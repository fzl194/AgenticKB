from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.contracts.models import BatchParams
from knowledge_mining.mining.jobs import run as run_job
from knowledge_mining.mining.stages.publishing import (
    assemble_build,
    classify_documents,
)


def _seed_batch(asset_db, *, domain: str, batch_id: str) -> None:
    asset_db.upsert_source_batch(
        domain=domain,
        batch_id=batch_id,
        batch_code=f"code-{batch_id}",
        source_type="folder_scan",
    )


def _seed_snapshot(
    asset_db,
    *,
    domain: str,
    document_id: str,
    snapshot_id: str,
) -> None:
    asset_db.upsert_document(
        domain=domain,
        document_id=document_id,
        document_key=f"doc:/{document_id}.md",
        document_name=f"{document_id}.md",
    )
    asset_db.upsert_snapshot(
        domain=domain,
        snapshot_id=snapshot_id,
        normalized_content_hash=f"normalized-{snapshot_id}",
        raw_content_hash=f"raw-{snapshot_id}",
        mime_type="text/markdown",
    )
    asset_db.insert_raw_segment(
        segment_id=f"segment-{snapshot_id}",
        document_snapshot_id=snapshot_id,
        segment_key=f"doc:/{document_id}.md#0",
        segment_index=0,
        raw_text=snapshot_id,
        normalized_text=snapshot_id,
    )


def _selection_rows(asset_db, build_id: str) -> dict[str, dict]:
    return {
        row["document_id"]: row
        for row in asset_db.get_build_snapshots(build_id)
    }


def test_classify_uses_active_parent_for_requested_channel() -> None:
    calls: list[tuple[str, str]] = []

    class FakeAssetDB:
        def get_active_build(self, *, domain: str, channel: str):
            calls.append((domain, channel))
            return {"id": "build-preview"}

        def get_build_snapshots(self, build_id: str):
            assert build_id == "build-preview"
            return [{
                "document_id": "doc-a",
                "document_snapshot_id": "snap-old",
                "selection_status": "active",
            }]

    decisions = classify_documents(
        FakeAssetDB(),
        [{"document_id": "doc-a", "document_snapshot_id": "snap-old"}],
        domain="odn",
        channel="preview",
        detect_remove=False,
    )

    assert calls == [("odn", "preview")]
    assert decisions[0]["action"] == "SKIP"


def test_classify_does_not_treat_removed_parent_selection_as_active() -> None:
    class FakeAssetDB:
        def get_active_build(self, *, domain: str, channel: str):
            assert (domain, channel) == ("odn", "prod")
            return {"id": "build-prod"}

        def get_build_snapshots(self, build_id: str):
            assert build_id == "build-prod"
            return [{
                "document_id": "doc-removed",
                "document_snapshot_id": "snap-old",
                "selection_status": "removed",
            }]

    decisions = classify_documents(
        FakeAssetDB(),
        [{"document_id": "doc-removed", "document_snapshot_id": "snap-old"}],
        domain="odn",
        channel="prod",
        detect_remove=True,
    )

    assert len(decisions) == 1
    assert decisions[0]["action"] == "NEW"


def test_active_build_is_release_and_channel_scoped(asset_db) -> None:
    for build_id in ("build-prod", "build-preview", "build-unpublished"):
        asset_db.insert_build(
            build_id,
            f"code-{build_id}",
            status="validated",
            build_mode="full",
            domain="odn",
        )
    asset_db.insert_release(
        "release-prod",
        "code-release-prod",
        "build-prod",
        domain="odn",
        channel="prod",
        status="active",
    )
    asset_db.insert_release(
        "release-preview",
        "code-release-preview",
        "build-preview",
        domain="odn",
        channel="preview",
        status="active",
    )

    assert asset_db.get_active_build(domain="odn", channel="prod")["id"] == "build-prod"
    assert asset_db.get_active_build(domain="odn", channel="preview")["id"] == "build-preview"


def test_active_build_rejects_release_pointing_to_another_domain(asset_db) -> None:
    asset_db.insert_build(
        "build-other-domain",
        "code-build-other-domain",
        status="validated",
        build_mode="full",
        domain="civil_engineering",
    )
    asset_db.insert_release(
        "release-domain-poison",
        "code-release-domain-poison",
        "build-other-domain",
        domain="odn",
        channel="prod",
        status="active",
    )

    assert asset_db.get_active_build(domain="odn", channel="prod") is None


def test_source_batch_propagation_preserves_effective_parent_state(asset_db) -> None:
    for batch_id in ("batch-old", "batch-b", "batch-current"):
        _seed_batch(asset_db, domain="odn", batch_id=batch_id)

    snapshots = {
        "doc-update": ("snap-update-old", "snap-update-new"),
        "doc-skip": ("snap-skip",),
        "doc-restore": ("snap-restore",),
        "doc-new": ("snap-new",),
        "doc-carry-active": ("snap-carry-active",),
        "doc-carry-removed": ("snap-carry-removed",),
        "doc-remove": ("snap-remove",),
        "doc-legacy": ("snap-legacy",),
    }
    for document_id, snapshot_ids in snapshots.items():
        for snapshot_id in snapshot_ids:
            _seed_snapshot(
                asset_db,
                domain="odn",
                document_id=document_id,
                snapshot_id=snapshot_id,
            )

    asset_db.insert_build(
        "build-parent",
        "code-build-parent",
        status="validated",
        build_mode="full",
        domain="odn",
        source_batch_id="batch-old",
    )
    parent_rows = [
        ("doc-update", "snap-update-old", "batch-old", "active", "add", {"audit": "update"}),
        ("doc-skip", "snap-skip", "batch-old", "active", "add", {"audit": "skip"}),
        ("doc-restore", "snap-restore", "batch-old", "removed", "remove", {"audit": "restore"}),
        ("doc-carry-active", "snap-carry-active", "batch-b", "active", "update", {"audit": "carry"}),
        ("doc-carry-removed", "snap-carry-removed", "batch-old", "removed", "remove", {"audit": "removed"}),
        ("doc-remove", "snap-remove", "batch-old", "active", "add", {"audit": "remove"}),
        ("doc-legacy", "snap-legacy", None, "active", "retain", {"audit": "legacy"}),
    ]
    for document_id, snapshot_id, source_batch_id, status, reason, metadata in parent_rows:
        asset_db.upsert_build_document_snapshot(
            build_id="build-parent",
            document_id=document_id,
            document_snapshot_id=snapshot_id,
            source_batch_id=source_batch_id,
            selection_status=status,
            reason=reason,
            metadata_json=metadata,
        )
    asset_db.insert_release(
        "release-parent",
        "code-release-parent",
        "build-parent",
        domain="odn",
        channel="prod",
        status="active",
    )

    decisions = classify_documents(
        asset_db,
        [
            {
                "document_id": "doc-update",
                "document_snapshot_id": "snap-update-new",
                "lifecycle_action": "UPDATE",
                "source_batch_id": "batch-current",
            },
            {
                "document_id": "doc-skip",
                "document_snapshot_id": "snap-skip",
                "lifecycle_action": "SKIP",
            },
            {
                "document_id": "doc-restore",
                "document_snapshot_id": "snap-restore",
                "lifecycle_action": "RESTORE",
                "source_batch_id": "batch-current",
            },
            {
                "document_id": "doc-new",
                "document_snapshot_id": "snap-new",
                "lifecycle_action": "NEW",
                "source_batch_id": "batch-current",
            },
            {
                "document_id": "doc-remove",
                "document_snapshot_id": "snap-remove",
                "action": "REMOVE",
                "selection_status": "removed",
                "reason": "remove",
                "lifecycle_action": "REMOVE",
            },
        ],
        domain="odn",
        channel="prod",
        detect_remove=False,
    )
    build_id = assemble_build(
        asset_db,
        domain="odn",
        channel="prod",
        run_id="run-current",
        batch_id="batch-current",
        snapshot_decisions=decisions,
    )
    rows = _selection_rows(asset_db, build_id)

    assert rows["doc-new"]["source_batch_id"] == "batch-current"
    assert rows["doc-update"]["source_batch_id"] == "batch-current"
    assert rows["doc-restore"]["source_batch_id"] == "batch-current"
    assert rows["doc-restore"]["metadata_json"]["lifecycle_action"] == "RESTORE"
    assert rows["doc-skip"]["source_batch_id"] == "batch-old"
    assert rows["doc-remove"]["source_batch_id"] == "batch-old"

    assert rows["doc-carry-active"]["document_snapshot_id"] == "snap-carry-active"
    assert rows["doc-carry-active"]["source_batch_id"] == "batch-b"
    assert rows["doc-carry-active"]["selection_status"] == "active"
    assert rows["doc-carry-active"]["reason"] == "update"
    assert rows["doc-carry-active"]["metadata_json"] == {"audit": "carry"}
    assert rows["doc-carry-removed"]["document_snapshot_id"] == "snap-carry-removed"
    assert rows["doc-carry-removed"]["source_batch_id"] == "batch-old"
    assert rows["doc-carry-removed"]["selection_status"] == "removed"
    assert rows["doc-carry-removed"]["reason"] == "remove"
    assert rows["doc-carry-removed"]["metadata_json"] == {"audit": "removed"}
    assert rows["doc-legacy"]["source_batch_id"] is None
    assert rows["doc-legacy"]["reason"] == "retain"
    assert rows["doc-legacy"]["metadata_json"] == {"audit": "legacy"}


def test_selection_conflict_updates_snapshot_batch_status_reason_and_metadata(asset_db) -> None:
    _seed_batch(asset_db, domain="odn", batch_id="batch-a")
    _seed_batch(asset_db, domain="odn", batch_id="batch-b")
    _seed_snapshot(asset_db, domain="odn", document_id="doc-a", snapshot_id="snap-a")
    _seed_snapshot(asset_db, domain="odn", document_id="doc-a", snapshot_id="snap-b")
    asset_db.insert_build(
        "build-a",
        "code-build-a",
        status="building",
        build_mode="full",
        domain="odn",
    )

    asset_db.upsert_build_document_snapshot(
        build_id="build-a",
        document_id="doc-a",
        document_snapshot_id="snap-a",
        source_batch_id="batch-a",
        selection_status="active",
        reason="add",
        metadata_json={"version": 1},
    )
    asset_db.upsert_build_document_snapshot(
        build_id="build-a",
        document_id="doc-a",
        document_snapshot_id="snap-b",
        source_batch_id="batch-b",
        selection_status="removed",
        reason="remove",
        metadata_json={"version": 2},
    )

    row = _selection_rows(asset_db, "build-a")["doc-a"]
    assert row["document_snapshot_id"] == "snap-b"
    assert row["source_batch_id"] == "batch-b"
    assert row["selection_status"] == "removed"
    assert row["reason"] == "remove"
    assert row["metadata_json"] == {"version": 2}


@pytest.mark.parametrize("mismatch", ["document", "snapshot", "batch"])
def test_selection_rejects_each_cross_domain_reference_without_write(asset_db, mismatch) -> None:
    _seed_batch(asset_db, domain="odn", batch_id="batch-odn")
    _seed_batch(asset_db, domain="civil_engineering", batch_id="batch-civil")
    _seed_snapshot(asset_db, domain="odn", document_id="doc-odn", snapshot_id="snap-odn")
    _seed_snapshot(
        asset_db,
        domain="civil_engineering",
        document_id="doc-civil",
        snapshot_id="snap-civil",
    )
    asset_db.insert_build(
        "build-odn",
        "code-build-odn",
        status="building",
        build_mode="full",
        domain="odn",
    )
    document_id = "doc-civil" if mismatch == "document" else "doc-odn"
    snapshot_id = "snap-civil" if mismatch == "snapshot" else "snap-odn"
    batch_id = "batch-civil" if mismatch == "batch" else "batch-odn"

    with pytest.raises(ValueError, match="^domain_mismatch$"):
        asset_db.upsert_build_document_snapshot(
            build_id="build-odn",
            document_id=document_id,
            document_snapshot_id=snapshot_id,
            source_batch_id=batch_id,
            selection_status="active",
            reason="add",
        )

    assert asset_db.get_build_snapshots("build-odn") == []


@pytest.mark.parametrize("mismatch", ["document", "snapshot", "batch"])
def test_assemble_cross_domain_failure_leaves_no_partial_build(asset_db, mismatch) -> None:
    _seed_batch(asset_db, domain="odn", batch_id="batch-odn")
    _seed_batch(asset_db, domain="civil_engineering", batch_id="batch-civil")
    _seed_snapshot(asset_db, domain="odn", document_id="doc-odn", snapshot_id="snap-odn")
    _seed_snapshot(
        asset_db,
        domain="civil_engineering",
        document_id="doc-civil",
        snapshot_id="snap-civil",
    )
    document_id = "doc-civil" if mismatch == "document" else "doc-odn"
    snapshot_id = "snap-civil" if mismatch == "snapshot" else "snap-odn"
    batch_id = "batch-civil" if mismatch == "batch" else "batch-odn"

    with pytest.raises(ValueError, match="^domain_mismatch$"):
        assemble_build(
            asset_db,
            domain="odn",
            channel="prod",
            run_id=f"run-cross-{mismatch}",
            batch_id=batch_id,
            snapshot_decisions=[{
                "document_id": document_id,
                "document_snapshot_id": snapshot_id,
                "action": "NEW",
                "selection_status": "active",
                "reason": "add",
            }],
        )

    assert asset_db._fetchone(
        "SELECT COUNT(*) AS count FROM asset_builds WHERE mining_run_id = %s",
        (f"run-cross-{mismatch}",),
    )["count"] == 0


def test_run_pipeline_forwards_normalized_channel_to_finalize(monkeypatch) -> None:
    class AssetDB:
        pool = object()

        def upsert_source_batch(self, **kwargs):
            return kwargs["batch_id"]

        def commit(self):
            pass

    class RuntimeDB:
        def _fetchone(self, sql, params):
            return {"status": "running"}

        def _execute(self, sql, params):
            pass

    class Tracker:
        pass

    class OntologyStore:
        def __init__(self, pool):
            pass

        def active_version(self, domain):
            return None

    from knowledge_mining.mining.infra import ontology_store

    monkeypatch.setattr(run_job, "RuntimeTracker", lambda db: Tracker())
    monkeypatch.setattr(run_job, "PipelineConfig", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(ontology_store, "OntologyStore", OntologyStore)

    def finalize(*args, channel):
        return {"channel": channel}

    monkeypatch.setattr(run_job, "_finalize_run", finalize)

    result = run_job._run_pipeline(
        AssetDB(),
        RuntimeDB(),
        Path("."),
        BatchParams(),
        True,
        "run-preview",
        profile=SimpleNamespace(domain_id="odn"),
        channel="preview",
        docs=[],
    )

    assert result == {"channel": "preview"}


def test_precreated_run_uses_its_persisted_channel(monkeypatch) -> None:
    class AssetDB:
        pool = object()

        def close(self):
            pass

    class RuntimeDB:
        row = {
            "id": "queued-preview",
            "input_path": str(Path("C:/incoming")),
            "domain": "odn",
            "channel": "preview",
            "status": "queued",
        }

        def get_run(self, run_id):
            return dict(self.row)

        def _fetchone(self, sql, params):
            return {"status": self.row["status"]}

        def close(self):
            pass

    class Tracker:
        def set_run_phase(self, *args):
            return True

        def start_stage(self, *args):
            return "ingest-event"

        def end_stage(self, *args, **kwargs):
            pass

        def finish_ingest(self, *args, **kwargs):
            return True

        def fail_run(self, *args, **kwargs):
            raise AssertionError("run must not fail")

    asset_db = AssetDB()
    runtime_db = RuntimeDB()
    monkeypatch.setattr(run_job, "resolve_domain", lambda domain: {
        "id": domain,
        "default_channel": "prod",
    })
    monkeypatch.setattr(run_job, "resolve_domain_database", lambda *args: object())
    monkeypatch.setattr(run_job, "_create_dbs", lambda config: (asset_db, runtime_db))
    monkeypatch.setattr(run_job, "RuntimeTracker", lambda db: Tracker())
    monkeypatch.setattr(run_job, "load_domain_pack", lambda domain: SimpleNamespace(domain_id=domain))
    monkeypatch.setattr(run_job, "_init_llm", lambda *args, **kwargs: {})
    monkeypatch.setattr(run_job, "_init_embedding", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_job, "ingest_directory", lambda *args: ([], {}))
    monkeypatch.setattr(
        run_job,
        "_run_pipeline",
        lambda *args, **kwargs: {"channel": kwargs["channel"]},
    )

    result = run_job.run(
        "C:/incoming",
        domain="odn",
        run_id="queued-preview",
    )

    assert result == {"channel": "preview"}


def test_resume_rebuild_distinguishes_missing_batch_from_explicit_legacy_null() -> None:
    class RuntimeDB:
        def get_run_documents(self, run_id):
            assert run_id == "reviewed-run"
            return [
                {
                    "status": "committed",
                    "action": "SKIP",
                    "document_id": "doc-old-paused",
                    "document_snapshot_id": "snap-old-paused",
                    "document_key": "doc:/old-paused.md",
                    "metadata_json": {},
                },
                {
                    "status": "committed",
                    "action": "SKIP",
                    "document_id": "doc-legacy-null",
                    "document_snapshot_id": "snap-legacy-null",
                    "document_key": "doc:/legacy-null.md",
                    "metadata_json": {"source_batch_id": None},
                },
            ]

    decisions, _counts = run_job._rebuild_from_run_documents(
        RuntimeDB(), "reviewed-run"
    )

    assert "source_batch_id" not in decisions[0]
    assert "source_batch_id" in decisions[1]
    assert decisions[1]["source_batch_id"] is None


def test_finalize_forwards_channel_to_parent_selection_and_publish(monkeypatch) -> None:
    calls: dict[str, str] = {}

    class AssetDB:
        def commit(self):
            pass

    class RuntimeDB:
        def _fetchone(self, sql, params):
            return {"status": "running"}

        def commit(self):
            pass

        def get_run(self, run_id):
            return {"status": "running"}

    class Tracker:
        def set_run_phase(self, *args):
            return True

        def start_stage(self, run_id, stage):
            return stage

        def end_stage(self, *args, **kwargs):
            pass

        def complete_run(self, *args, **kwargs):
            return True

    def classify(db, decisions, *, domain, channel, detect_remove):
        calls["classify"] = channel
        return decisions

    def assemble(db, *, domain, channel, run_id, batch_id, snapshot_decisions, kb_id=None):
        calls["assemble"] = channel
        return "build-preview"

    def publish(db, build_id, *, domain, channel, released_by):
        calls["publish"] = channel
        return "release-preview"

    monkeypatch.setattr(run_job, "classify_documents", classify)
    monkeypatch.setattr(run_job, "assemble_build", assemble)
    monkeypatch.setattr(run_job, "publish_release", publish)
    monkeypatch.setattr(run_job, "demo_quality_summary", lambda *args: {})

    result = run_job._finalize_run(
        AssetDB(),
        RuntimeDB(),
        Tracker(),
        "run-preview",
        "batch-preview",
        [{"document_id": "doc-a", "document_snapshot_id": "snap-a"}],
        {
            "committed_count": 1,
            "new_count": 1,
            "updated_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
        },
        1,
        False,
        False,
        SimpleNamespace(domain_id="odn"),
        channel="preview",
    )

    assert calls == {
        "classify": "preview",
        "assemble": "preview",
        "publish": "preview",
    }
    assert result["release_id"] == "release-preview"


def test_resume_uses_channel_persisted_on_mining_run(monkeypatch) -> None:
    class AssetDB:
        def close(self):
            pass

    class RuntimeDB:
        row = {
            "id": "reviewed-run",
            "status": "awaiting_review",
            "subloop_stage": "ontology_review",
            "domain": "odn",
            "channel": "preview",
            "source_batch_id": "batch-preview",
            "total_documents": 1,
        }

        def get_run(self, run_id):
            return dict(self.row)

        def commit(self):
            pass

        def close(self):
            pass

    class Tracker:
        def resume_running(self, *args, **kwargs):
            return True

    asset_db = AssetDB()
    runtime_db = RuntimeDB()
    monkeypatch.setattr(run_job, "resolve_domain", lambda domain: {
        "id": domain,
        "default_channel": "prod",
    })
    monkeypatch.setattr(run_job, "resolve_domain_database", lambda *args: object())
    monkeypatch.setattr(run_job, "_create_dbs", lambda config: (asset_db, runtime_db))
    monkeypatch.setattr(run_job, "load_domain_pack", lambda domain: SimpleNamespace(domain_id=domain))
    monkeypatch.setattr(run_job, "RuntimeTracker", lambda db: Tracker())
    monkeypatch.setattr(run_job, "_has_pending_mentions", lambda *args: False)
    monkeypatch.setattr(run_job, "_has_proposed_candidates", lambda *args: False)
    monkeypatch.setattr(run_job, "_finalize_graph", lambda *args: None)
    monkeypatch.setattr(run_job, "_rebuild_from_run_documents", lambda *args: (
        [{"document_id": "doc-a", "document_snapshot_id": "snap-a"}],
        {
            "committed_count": 1,
            "new_count": 1,
            "updated_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
        },
    ))

    def finalize(*args, channel):
        return {"channel": channel}

    monkeypatch.setattr(run_job, "_finalize_run", finalize)

    result = run_job.resume("reviewed-run", domain="odn")

    assert result == {"channel": "preview"}
