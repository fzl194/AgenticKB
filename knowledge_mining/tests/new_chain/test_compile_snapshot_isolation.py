"""Production composition root: compiler policy must isolate snapshot assets."""
import pytest
from knowledge_mining.tests.new_chain.test_multi_format_pipeline import (
    FakeObjectStore, MemoryStorageObjectRepository, MemoryDocumentCurrentContentRepository,
    MemorySnapshotRepository, MemorySegmentStore, build_new_chain_services, _seed_document, _md_bytes,
)

@pytest.mark.asyncio
async def test_policy_change_keeps_old_snapshot_and_shared_document_intact(tmp_path):
    store = FakeObjectStore(str(tmp_path / "objects"))
    objects = MemoryStorageObjectRepository()
    documents = MemoryDocumentCurrentContentRepository()
    snapshots = MemorySnapshotRepository()
    segments = MemorySegmentStore()
    services = build_new_chain_services(bucket_prefix="test-", object_store=store,
        storage_objects=objects, documents=documents, snapshots=snapshots, segment_store=segments)
    outcomes = []
    for name in ("a", "b"):
        raw = await _seed_document(store, objects, documents, fmt="md", data=_md_bytes(),
            doc_id=name, document_key=name + ".md")
        outcomes.append(services.document_parse_service.parse_document(raw, params={},
            domain="e2e", run_document_id="rd-" + name))
    def compile(outcome, policy):
        return services.segment_compile_service.compile_for_snapshot(
            snapshot_id=outcome.snapshot_id,
            parse_ir_storage_object_id=outcome.parse_ir_storage_object_id,
            params={"tableView": policy},
            frozen_input=outcome.frozen_input)
    whole = compile(outcomes[0], "whole")
    original = tuple(await segments.list_for_snapshot(whole.snapshot_id))
    shared = compile(outcomes[1], "whole")
    rows = compile(outcomes[0], "rows")
    assert rows.snapshot_id != whole.snapshot_id
    assert shared.snapshot_id == whole.snapshot_id
    assert tuple(await segments.list_for_snapshot(whole.snapshot_id)) == original
    assert compile(outcomes[0], "rows").snapshot_id == rows.snapshot_id
    assert (await snapshots.get(rows.snapshot_id)).compiler_fingerprint == rows.compiler_fingerprint
    assert rows.segment_count != whole.segment_count
    assert segments._by_snapshot[whole.snapshot_id].document_key == whole.snapshot_id


@pytest.mark.asyncio
async def test_compile_failure_and_stale_source_preserve_previous_segments(tmp_path, monkeypatch):
    from knowledge_mining.mining.segment_compiler.service import SegmentCompileService
    from knowledge_mining.mining.frozen_input.contracts import FrozenInputStale

    store = FakeObjectStore(str(tmp_path / "objects"))
    objects = MemoryStorageObjectRepository()
    documents = MemoryDocumentCurrentContentRepository()
    snapshots = MemorySnapshotRepository()
    segments = MemorySegmentStore()
    services = build_new_chain_services(bucket_prefix="test-", object_store=store,
        storage_objects=objects, documents=documents, snapshots=snapshots, segment_store=segments)
    raw = await _seed_document(store, objects, documents, fmt="md", data=_md_bytes())
    parsed = services.document_parse_service.parse_document(raw, params={}, domain="e2e", run_document_id="rd")
    def compile(policy):
        return services.segment_compile_service.compile_for_snapshot(
            snapshot_id=parsed.snapshot_id, parse_ir_storage_object_id=parsed.parse_ir_storage_object_id,
            frozen_input=parsed.frozen_input, params={"tableView": policy})
    whole = compile("whole")
    original = tuple(await segments.list_for_snapshot(whole.snapshot_id))
    async def fail(*args, **kwargs):
        raise RuntimeError("injected compile failure")
    with monkeypatch.context() as patch:
        patch.setattr(SegmentCompileService, "compile", fail)
        with pytest.raises(RuntimeError, match="injected"):
            compile("rows")
    assert tuple(await segments.list_for_snapshot(whole.snapshot_id)) == original
    frozen = parsed.frozen_input
    await documents.set_current_content(raw.document_id, frozen.source_storage_object_id,
        frozen.source_raw_hash, expected_revision=frozen.source_content_revision)
    with pytest.raises(FrozenInputStale):
        compile("rows")
    assert tuple(await segments.list_for_snapshot(whole.snapshot_id)) == original


@pytest.mark.asyncio
async def test_handlers_pass_compiled_identity_into_persist_and_run_document(tmp_path):
    from types import SimpleNamespace
    from knowledge_mining.mining.pipeline import DocumentContext
    from knowledge_mining.mining.workflow.handlers.document import document_parse_handler, segment_compile_handler
    from knowledge_mining.mining.workflow.handlers.persist import asset_persist_handler
    from knowledge_mining.tests.test_m6_handlers import _state

    store = FakeObjectStore(str(tmp_path / "objects"))
    objects = MemoryStorageObjectRepository()
    documents = MemoryDocumentCurrentContentRepository()
    snapshots = MemorySnapshotRepository()
    services = build_new_chain_services(bucket_prefix="test-", object_store=store,
        storage_objects=objects, documents=documents, snapshots=snapshots, segment_store=MemorySegmentStore())
    raw = await _seed_document(store, objects, documents, fmt="md", data=_md_bytes())
    runtime = SimpleNamespace(services=services)
    parsed = document_parse_handler(_state(DocumentContext(raw_file=raw)), {}, runtime)
    assert parsed.status.value == "success"
    compiled = segment_compile_handler(_state(parsed.outputs.context), {"tableView": "rows"}, runtime)
    assert compiled.status.value == "success"
    old = parsed.outputs.context.snapshot_ref
    new = compiled.outputs.context.snapshot_ref
    assert old != new
    captured = []
    staged = []
    class Persist:
        def persist_for_snapshot(self, *, snapshot_id, document_ref):
            captured.append(snapshot_id)
            return SimpleNamespace()
    runtime = SimpleNamespace(manifest={"runId": "run-test"},
        runtime_repository=SimpleNamespace(document_persist_marker=lambda _: None),
        services=SimpleNamespace(asset_persist_service=Persist(),
            stage_document=lambda *args: staged.append(args)))
    persisted = asset_persist_handler(_state(compiled.outputs.context), {}, runtime)
    assert persisted.status.value == "success"
    assert captured == [new]
    assert staged == [("rd-1", raw.document_id, new)]
    assert persisted.outputs.context.snapshot_ref == new


@pytest.mark.asyncio
async def test_production_compiler_rejects_missing_frozen_binding(tmp_path):
    services = build_new_chain_services(bucket_prefix="test-", object_store=FakeObjectStore(str(tmp_path / "objects")))
    with pytest.raises(ValueError, match="original frozen input"):
        services.segment_compile_service.compile_for_snapshot(
            snapshot_id="snapshot", parse_ir_storage_object_id="ir", params={})
