"""同一 Run 内共享 snapshot 的 project/persist 必须只生产一次。"""
from __future__ import annotations

import asyncio

from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment


class _SegmentStore:
    async def list_for_snapshot(self, snapshot_id):
        return (CompiledSegment(
            segment_index=0, block_type="paragraph", raw_text="same content",
        ),)


class _RepresentationStore:
    def __init__(self):
        self.rows = ()
        self.replaces = 0

    async def replace_for_snapshot(
        self, snapshot_id, representations, fingerprint, *, document_key,
    ):
        self.replaces += 1
        self.rows = tuple(representations)
        return len(self.rows)

    async def list_for_snapshot(self, snapshot_id):
        return self.rows


def test_projection_shared_snapshot_keeps_first_consistent_representation():
    from knowledge_mining.mining.workflow.new_chain_services import RetrieProjectFacade

    representations = _RepresentationStore()
    facade = RetrieProjectFacade(_SegmentStore(), representations)

    first = facade.project_for_snapshot(
        snapshot_id="shared", document_ref="doc:/a.md", params={},
    )
    second = facade.project_for_snapshot(
        snapshot_id="shared", document_ref="doc:/b.md", params={},
    )

    assert representations.replaces == 1
    assert second.representations == first.representations


class _EmbeddingStore:
    async def list_for_snapshot(self, snapshot_id):
        return ()


class _Writer:
    def __init__(self):
        self.calls = 0

    def replace_for_snapshot(self, snapshot_id, faces):
        self.calls += 1
        return int(faces.get("representation_count", 0))


def test_persist_shared_snapshot_does_not_overwrite_first_projection():
    from knowledge_mining.mining.retrieval_projection.persist import AssetPersistService

    representations = _RepresentationStore()
    asyncio.run(representations.replace_for_snapshot(
        "shared", (), "v1", document_key="doc:/a.md",
    ))
    writer = _Writer()
    service = AssetPersistService(
        segment_store=_SegmentStore(), representation_store=representations,
        embedding_store=_EmbeddingStore(), writer=writer,
    )

    first = service.persist_for_snapshot(
        snapshot_id="shared", document_ref="doc:/a.md",
    )
    second = service.persist_for_snapshot(
        snapshot_id="shared", document_ref="doc:/b.md",
    )

    assert writer.calls == 1
    assert second == first
