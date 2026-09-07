"""A1 物化服务与受控重放（38 号 §5：staging 语义/幂等/降级/晋升隔离）."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.contracts.parse_ir.enums import (
    PARSE_IR_SCHEMA_VERSION,
)
from knowledge_mining.mining.contracts.parse_ir.types import (
    Container,
    Element,
    EvidenceSpan,
    ParseIdentity,
    ParsedDocument,
)
from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrievalRepresentation,
)
from knowledge_mining.mining.source_locator.extract import LOCATOR_VERSION
from knowledge_mining.mining.source_locator.repositories_memory import (
    MemoryLocatorStore,
)
from knowledge_mining.mining.source_locator.replay import replay
from knowledge_mining.mining.source_locator.service import (
    SourceLocatorFacade,
    SourceLocatorService,
)


def _ir_doc() -> ParsedDocument:
    return ParsedDocument(
        schema_version=PARSE_IR_SCHEMA_VERSION,
        source_identity=ParseIdentity(
            source_raw_hash="raw-1",
            parser_fingerprint="legacy_markdown@1",
            parse_ir_schema_version=PARSE_IR_SCHEMA_VERSION,
        ),
        containers=(Container(container_id="c0", container_type="page",
                              order_index=0, page_number=1),),
        elements=(
            Element(
                element_id="p1", element_type="paragraph", order_index=0,
                source_spans=(EvidenceSpan(
                    span_id="s1",
                    source_locator={"line_start": 3, "line_end": 8},
                ),),
            ),
        ),
    )


def _representation(rep_id: str = "snap-1:segment:0") -> RetrievalRepresentation:
    return RetrievalRepresentation(
        representation_id=rep_id,
        representation_type="segment",
        content_type="prose",
        content_text="正文",
        target_type="segment",
        target_ref="doc-x#seg:0",
        canonical_evidence_id=rep_id,
        source_refs=(
            {"element_id": "p1", "evidence_span_ids": ("s1",)},
        ),
        facets={"document": "doc-x", "section_path": "第一章"},
    )


class _FakeSnapshots:
    def __init__(self, *, snapshot_id: str, object_id: str | None) -> None:
        self._record = SimpleNamespace(
            id=snapshot_id, parse_ir_storage_object_id=object_id,
        )

    async def get(self, snapshot_id: str):
        return self._record if snapshot_id == self._record.id else None


class _FakeStorageObjects:
    def __init__(self, record) -> None:
        self._record = record

    async def get(self, object_id: str):
        return self._record if object_id == self._record.id else None


class _FakeObjectStore:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def get_stream(self, location):
        yield self._payload[:4]
        yield self._payload[4:]


class _FakePool:
    """replay 的候选快照查询桩：只提供 connection().execute().fetchall()."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def connection(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        return self

    async def fetchall(self):
        return self._rows


class _FakeRepresentationStore:
    """主链门面的 staging units 来源."""

    def __init__(self) -> None:
        self.by_snapshot: dict[str, tuple] = {}

    async def list_for_snapshot(self, snapshot_id: str) -> tuple:
        return self.by_snapshot.get(snapshot_id, ())


def _build(
    object_id: str = "obj-1", *, snapshot_id: str = "snap-1",
    locator_store=None,
):
    payload = json.dumps(_ir_doc().to_dict()).encode("utf-8")
    record = SimpleNamespace(
        id=object_id, provider="fake", bucket="agentickb-dev-parse",
        object_key="v1/ab/cd/sha", object_version_id=None,
        sha256=hashlib.sha256(payload).hexdigest(), size=len(payload),
        artifact_class="parse_ir",
    )
    store = locator_store if locator_store is not None else MemoryLocatorStore()
    service = SourceLocatorService(
        snapshots=_FakeSnapshots(snapshot_id=snapshot_id, object_id=object_id),
        storage_objects=_FakeStorageObjects(record),
        object_store=_FakeObjectStore(payload),
        locator_store=store,
    )
    return service, store


@pytest.mark.asyncio
async def test_materialize_writes_staging_records() -> None:
    service, store = _build()
    outcome = await service.materialize(
        "snap-1", representations=(_representation(),)
    )
    assert outcome.status == "ok"
    assert outcome.record_count == 1
    staged = await store.list_for_snapshot("snap-1")
    assert len(staged) == 1
    assert staged[0].locator_kind == "line_range"
    assert staged[0].section_path == "第一章"
    # final 尚无行——晋升前不可见
    assert await store.list_final_for_snapshot("snap-1") == ()


@pytest.mark.asyncio
async def test_materialize_skips_without_ir_or_units() -> None:
    service, store = _build()
    no_units = await service.materialize("snap-1", representations=())
    assert no_units.status == "skipped_no_units"

    service_no_ir, _ = _build()
    missing = SourceLocatorService(
        snapshots=_FakeSnapshots(snapshot_id="snap-2", object_id=None),
        storage_objects=_FakeStorageObjects(SimpleNamespace(id="obj-1")),
        object_store=_FakeObjectStore(b"{}"),
        locator_store=MemoryLocatorStore(),
    )
    assert (await missing.materialize("snap-2")).status == "skipped_no_ir"


@pytest.mark.asyncio
async def test_materialize_rejects_sha_mismatch() -> None:
    service, _ = _build()
    record = SimpleNamespace(
        id="obj-1", provider="fake", bucket="b", object_key="k",
        object_version_id=None, sha256="0" * 64, size=1,
        artifact_class="parse_ir",
    )
    broken = SourceLocatorService(
        snapshots=_FakeSnapshots(snapshot_id="snap-1", object_id="obj-1"),
        storage_objects=_FakeStorageObjects(record),
        object_store=_FakeObjectStore(b"not-the-registered-content"),
        locator_store=MemoryLocatorStore(),
    )
    from knowledge_mining.mining.contracts.storage.errors import (
        StorageObjectCorrupt,
    )

    with pytest.raises(StorageObjectCorrupt):
        await broken.materialize("snap-1", representations=(_representation(),))


@pytest.mark.asyncio
async def test_facade_reads_staging_representations() -> None:
    service, store = _build()
    representations = _FakeRepresentationStore()
    representations.by_snapshot["snap-1"] = (_representation(),)
    facade = SourceLocatorFacade(service, representations)
    outcome = facade.materialize_for_snapshot(snapshot_id="snap-1")
    assert outcome.status == "ok"
    assert outcome.record_count == 1
    assert facade.materialize_for_snapshot(snapshot_id=None).status == (
        "skipped_no_snapshot"
    )


# ---------------------------------------------------------------------------
# 受控重放：幂等 / dry-run / 专用晋升隔离
# ---------------------------------------------------------------------------


class _ReplayLocatorStore(MemoryLocatorStore):
    """补 final units 读（重放路径读 final units）."""

    def __init__(self, final_units: dict[str, tuple]) -> None:
        super().__init__()
        self._final_units = final_units
        self.promoted: list[list[str]] = []

    async def list_final_representations(self, snapshot_id: str):
        return self._final_units.get(snapshot_id, ())

    async def promote_locators(self, snapshot_ids):
        self.promoted.append(list(snapshot_ids))
        return await super().promote_locators(snapshot_ids)


@pytest.mark.asyncio
async def test_replay_promotes_and_is_idempotent() -> None:
    locator_store = _ReplayLocatorStore({"snap-1": (_representation(),)})
    service, _ = _build(locator_store=locator_store)
    pool = _FakePool([
        {"id": "snap-1", "domain": "default",
         "parse_ir_storage_object_id": "obj-1"},
    ])

    first = await replay(
        pool=pool, snapshots=_FakeSnapshots(snapshot_id="snap-1", object_id="obj-1"),
        storage_objects=service._storage_objects,
        object_store=service._object_store,
        locator_store=locator_store,
        service=service,
    )
    assert first.materialized == 1
    assert first.records == 1
    assert locator_store.promoted == [["snap-1"]]
    final_rows = await locator_store.list_final_for_snapshot("snap-1")
    assert len(final_rows) == 1
    assert final_rows[0].representation_id == "snap-1:segment:0"

    # 二跑：同输入同结果（staging 先删后插 + final 晋升，幂等）
    second = await replay(
        pool=pool, snapshots=_FakeSnapshots(snapshot_id="snap-1", object_id="obj-1"),
        storage_objects=service._storage_objects,
        object_store=service._object_store,
        locator_store=locator_store,
        service=service,
    )
    assert second.materialized == 1
    assert await locator_store.list_final_for_snapshot("snap-1") == final_rows
    assert await locator_store.list_for_snapshot("snap-1") == ()  # staging 清空


@pytest.mark.asyncio
async def test_replay_dry_run_does_not_promote() -> None:
    locator_store = _ReplayLocatorStore({"snap-1": (_representation(),)})
    service, _ = _build(locator_store=locator_store)
    stats = await replay(
        pool=_FakePool([
            {"id": "snap-1", "domain": "default",
             "parse_ir_storage_object_id": "obj-1"},
        ]),
        snapshots=_FakeSnapshots(snapshot_id="snap-1", object_id="obj-1"),
        storage_objects=service._storage_objects,
        object_store=service._object_store,
        locator_store=locator_store,
        service=service,
        dry_run=True,
    )
    assert stats.materialized == 0
    assert locator_store.promoted == []
    # dry-run 物化到了 staging，final 不动
    assert len(await locator_store.list_for_snapshot("snap-1")) == 1
    assert await locator_store.list_final_for_snapshot("snap-1") == ()


@pytest.mark.asyncio
async def test_replay_single_failure_does_not_block_batch() -> None:
    service_ok, _ = _build(object_id="obj-1", snapshot_id="snap-1")
    # snap-2 的 IR 内容 sha 不匹配 → 单快照失败，snap-1 仍完成
    broken_service, _ = _build(object_id="obj-2", snapshot_id="snap-2")
    record = SimpleNamespace(
        id="obj-2", provider="fake", bucket="b", object_key="k2",
        object_version_id=None, sha256="1" * 64, size=1,
        artifact_class="parse_ir",
    )
    broken_service = SourceLocatorService(
        snapshots=_FakeSnapshots(snapshot_id="snap-2", object_id="obj-2"),
        storage_objects=_FakeStorageObjects(record),
        object_store=_FakeObjectStore(b"mismatched"),
        locator_store=MemoryLocatorStore(),
    )
    locator_store = _ReplayLocatorStore({
        "snap-1": (_representation(),),
        "snap-2": (_representation("snap-2:segment:0"),),
    })
    pool = _FakePool([
        {"id": "snap-1", "domain": "default",
         "parse_ir_storage_object_id": "obj-1"},
        {"id": "snap-2", "domain": "default",
         "parse_ir_storage_object_id": "obj-2"},
    ])

    class _RoutingSnapshots:
        async def get(self, snapshot_id):
            if snapshot_id == "snap-1":
                return SimpleNamespace(
                    id="snap-1", parse_ir_storage_object_id="obj-1"
                )
            return SimpleNamespace(
                id="snap-2", parse_ir_storage_object_id="obj-2"
            )

    class _RoutingObjects:
        def __init__(self):
            self._ok = service_ok._storage_objects
            self._broken = broken_service._storage_objects

        async def get(self, object_id):
            source = self._ok if object_id == "obj-1" else self._broken
            return await source.get(object_id)

    stats = await replay(
        pool=pool,
        snapshots=_RoutingSnapshots(),
        storage_objects=_RoutingObjects(),
        object_store=service_ok._object_store,
        locator_store=locator_store,
    )
    assert stats.materialized == 1
    assert len(stats.failed) == 1
    assert stats.failed[0][0] == "snap-2"


@pytest.mark.asyncio
async def test_locator_version_frozen_into_records() -> None:
    """locator_version 落行（重放幂等键成分）——由 store 写入，此处验证契约存在."""
    assert LOCATOR_VERSION == "source-locator@1"
