"""44 review: dict-row count, XLSX identity upgrade, IR-backed section anchors."""
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest


@pytest.mark.asyncio
async def test_count_documents_reads_dictionary_count():
    from knowledge_mining.mining.kb.db import KbDB
    class Cursor:
        async def fetchone(self):
            return {"count": 3}
    class Connection:
        async def execute(self, *args):
            return Cursor()
    class Pool:
        @asynccontextmanager
        async def connection(self):
            yield Connection()
    assert await KbDB(Pool()).count_documents_in_kb(kb_id="kb") == 3


def _nested_document():
    from knowledge_mining.tests.segment_compiler.test_projection_and_store import _doc
    from knowledge_mining.mining.contracts.parse_ir.types import Element
    return replace(_doc(), elements=(
        Element(element_id="parent", element_type="heading", order_index=0,
                text="Parent", style={"level": 1}),
        Element(element_id="child", element_type="heading", order_index=1,
                text="Child", style={"level": 2}),
        Element(element_id="body", element_type="paragraph", order_index=2,
                text="Long body. " * 100),
    ))


def test_real_compiler_parent_and_child_have_distinct_ir_anchors():
    from knowledge_mining.mining.contracts.segment_compiler import SegmentPolicy
    from knowledge_mining.mining.segment_compiler.compiler import compile_segments
    from knowledge_mining.mining.retrieval_projection.persist import AssetPersistService, MemoryAssetWriter
    from knowledge_mining.tests.retrieval_projection.test_persist_ir_failures import _ListStore
    document = _nested_document()
    segments = compile_segments(document, SegmentPolicy())
    assert segments[0].element_ids == ("parent", "child", "body")
    async def load_ir(_):
        return document
    writer = MemoryAssetWriter()
    service = AssetPersistService(segment_store=_ListStore(segments),
        representation_store=_ListStore(), embedding_store=_ListStore(),
        writer=writer, ir_loader=load_ir)
    service.persist_for_snapshot(snapshot_id="s", document_ref="doc")
    nodes = writer.snapshots["s"]["structure_nodes"]
    sections = {n["title"]: n["element_id"] for n in nodes if n["node_type"] == "section"}
    assert sections == {"Parent": "parent", "Child": "child"}


def test_without_ir_heading_facts_does_not_invent_an_anchor():
    from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment
    from knowledge_mining.mining.retrieval_projection.section_identity import build_section_identities
    index = build_section_identities((CompiledSegment(segment_index=0,
        block_type="paragraph", raw_text="body", heading_chain=((1, "Title"),),
        element_ids=("body",)),), document_ref="doc")
    assert index.identities[0].element_id is None


@pytest.mark.asyncio
async def test_xlsx_upgrade_commits_new_snapshot_and_retains_old_ir(tmp_path):
    from knowledge_mining.tests.parse_adapters.test_xlsx_typed_cells import (
        _build_typed_xlsx, XLSX_MIME, RAW_HASH,
    )
    from knowledge_mining.mining.parse_adapters.native.native_xlsx import NativeXlsxParser, XlsxNormalizer
    from knowledge_mining.mining.snapshot_store.repositories_memory import MemorySnapshotRepository
    from knowledge_mining.mining.snapshot_store.service import SnapshotCommitService
    from knowledge_mining.tests.snapshot_store.test_commit_service import (
        _frozen, _decision, FakeObjectStore, MemoryStorageObjectRepository,
    )
    doc = XlsxNormalizer().normalize(
        NativeXlsxParser().parse(_build_typed_xlsx(), mime=XLSX_MIME), source_raw_hash=RAW_HASH)
    old_identity = replace(doc.source_identity,
        parser_fingerprint=doc.source_identity.parser_fingerprint.replace("@2.1.0", "@2.0.0"),
        normalizer_version="native-xlsx@2")
    old_doc = replace(doc, source_identity=old_identity,
        structured_assets={key: replace(asset, cells=tuple(replace(cell,
            value_type=None, normalized_value=None) for cell in asset.cells))
            for key, asset in doc.structured_assets.items()})
    store = FakeObjectStore(str(tmp_path / "objects"))
    objects = MemoryStorageObjectRepository()
    import hashlib
    import json
    from knowledge_mining.mining.contracts.file_management import StorageObjectRecord
    from knowledge_mining.mining.contracts.storage.types import ObjectLocation, PutOptions
    for object_id, parsed in (("old-ir", old_doc), ("new-ir", doc)):
        payload = json.dumps(parsed.to_dict()).encode()
        location = ObjectLocation(bucket="parse", object_key=object_id)
        await store.put_bytes(location, payload, PutOptions(artifact_class="parse_ir"))
        await objects.register(StorageObjectRecord(id=object_id, provider="fake",
            bucket="parse", object_key=object_id, object_version_id=None,
            sha256=hashlib.sha256(payload).hexdigest(), size=len(payload),
            mime="application/json", artifact_class="parse_ir", state="AVAILABLE",
            created_at="2026-09-10T00:00:00+00:00"))
    snapshots = MemorySnapshotRepository()
    service = SnapshotCommitService(snapshots=snapshots, storage_objects=objects, object_store=store)
    frozen = replace(_frozen(), source_raw_hash=RAW_HASH, mime=XLSX_MIME)
    old = await service.commit(frozen=frozen, document=old_doc,
        parse_ir_storage_object_id="old-ir", quality_decision=_decision(), run_id="old", domain="d")
    new = await service.commit(frozen=frozen, document=doc,
        parse_ir_storage_object_id="new-ir", quality_decision=_decision(), run_id="new", domain="d")
    assert new.snapshot.id != old.snapshot.id
    assert new.snapshot.parse_ir_storage_object_id == "new-ir"
    assert (await snapshots.get(old.snapshot.id)).parse_ir_storage_object_id == "old-ir"

    from knowledge_mining.mining.snapshot_store.ir_access import load_parsed_document
    old_ir = await load_parsed_document(snapshots=snapshots, storage_objects=objects,
        object_store=store, snapshot_id=old.snapshot.id)
    new_ir = await load_parsed_document(snapshots=snapshots, storage_objects=objects,
        object_store=store, snapshot_id=new.snapshot.id)
    assert all(cell.value_type is None for asset in old_ir.structured_assets.values() for cell in asset.cells)
    assert any(cell.value_type == "number" for asset in new_ir.structured_assets.values() for cell in asset.cells)
