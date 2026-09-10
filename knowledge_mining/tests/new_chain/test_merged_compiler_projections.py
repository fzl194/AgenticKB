"""Merged A2/A3 and snapshot isolation must use the same compiled identity."""
import pytest
from knowledge_mining.tests.new_chain.test_multi_format_pipeline import (
    FakeObjectStore, MemoryStorageObjectRepository, MemoryDocumentCurrentContentRepository,
    MemorySnapshotRepository, MemorySegmentStore, build_new_chain_services,
    _seed_document,
)
from knowledge_mining.mining.retrieval_projection.persist import MemoryAssetWriter
from knowledge_mining.tests.parse_adapters.test_xlsx_typed_cells import _build_typed_xlsx


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["md", "xlsx"])
async def test_compiled_snapshot_preserves_ir_based_projections(fmt, tmp_path):
    store = FakeObjectStore(str(tmp_path / "objects"))
    objects = MemoryStorageObjectRepository()
    documents = MemoryDocumentCurrentContentRepository()
    snapshots = MemorySnapshotRepository()
    writer = MemoryAssetWriter()
    services = build_new_chain_services(bucket_prefix="merged-", object_store=store,
        storage_objects=objects, documents=documents, snapshots=snapshots,
        segment_store=MemorySegmentStore(), asset_writer=writer)
    data = ("# Parent\n\n## Child\n\n" + "Long content. " * 100).encode() if fmt == "md" else _build_typed_xlsx()
    raw = await _seed_document(store, objects, documents, fmt=fmt, data=data)
    parsed = services.document_parse_service.parse_document(raw, params={}, domain="e2e", run_document_id="rd")
    compiled = services.segment_compile_service.compile_for_snapshot(
        snapshot_id=parsed.snapshot_id, parse_ir_storage_object_id=parsed.parse_ir_storage_object_id,
        frozen_input=parsed.frozen_input, params={"tableView": "rows"})
    assert compiled.snapshot_id != parsed.snapshot_id
    services.retrieval_project_service.project_for_snapshot(snapshot_id=compiled.snapshot_id,
        document_ref=raw.document_key, params={"includeSections": True})
    persisted = services.asset_persist_service.persist_for_snapshot(
        snapshot_id=compiled.snapshot_id, document_ref=raw.document_key)
    assert persisted.snapshot_id == compiled.snapshot_id
    assert parsed.snapshot_id not in writer.snapshots
    faces = writer.snapshots[compiled.snapshot_id]
    if fmt == "md":
        sections = [n for n in faces["structure_nodes"] if n["node_type"] == "section"]
        assert {n["title"] for n in sections} == {"Parent", "Child"}
        assert all(n["element_id"] for n in sections)
        assert len({n["element_id"] for n in sections}) == 2
        section_refs = {n["ref"] for n in sections}
        assert all(r.get("section_ref") in section_refs for r in faces["representations"]
            if r["representation_type"] != "document")
    else:
        assert faces["table_cells"]
        assert {c["value_type"] for c in faces["table_cells"]} >= {"text", "number", "date"}
        assert all(a["sheet_name"] == "台账" for a in faces["table_assets"])
