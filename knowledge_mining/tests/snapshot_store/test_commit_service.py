"""M4.4 WP9：SnapshotCommitService + memory 仓储（RED 先行）.

覆盖（SRS §4.10 / §9.4 / §8.3A / §2.1）：
- PASS/WARN 幂等转正（指纹命中复用，created=False）；
- FAIL 决策被拒（低质量不形成 READY Snapshot——M4 退出条件）；
- pre-commit revision check（stale_checker 抛 FrozenInputStale → 透传、
  不写任何快照行——SUPERSEDED 语义的提交侧）；
- IR 制品完整性前置校验（§8.6：注册行缺失/对象缺失 = 完整性事故，阻断）；
- 来源 link 携带对象 URI 哨兵（非 presigned，§2.1）。
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.asyncio

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_mining.mining.contracts.parse_ir.enums import (  # noqa: E402
    PARSE_IR_SCHEMA_VERSION,
)
from knowledge_mining.mining.contracts.parse_ir.types import (  # noqa: E402
    Container,
    Element,
    ParseIdentity,
    ParsedDocument,
)
from knowledge_mining.mining.contracts.storage.errors import (  # noqa: E402
    StorageObjectMissing,
)
from knowledge_mining.mining.contracts.storage.types import (  # noqa: E402
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.frozen_input.contracts import FrozenInput  # noqa: E402
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402
from knowledge_mining.mining.infra.object_store.keys import (  # noqa: E402
    build_object_key,
)
from knowledge_mining.mining.file_management.repositories_memory import (  # noqa: E402
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.parse_quality.gate import QualityDecision  # noqa: E402


# ---------------------------------------------------------------------------
# 组装 fakes
# ---------------------------------------------------------------------------


def _frozen() -> FrozenInput:
    return FrozenInput(
        document_id="doc1",
        source_storage_object_id="so_src",
        source_raw_hash="raw-hash-1",
        source_content_revision=3,
        mime="application/pdf",
        size=100,
        original_filename="a.pdf",
        captured_at="2026-08-18T00:00:00+00:00",
        provider="minio",
        bucket="agentickb-dev-source",
        object_key="v1/ab/cd/src-1",
        object_version_id=None,
    )


def _doc(parser_fingerprint: str = "native_pdf@2.0.0") -> ParsedDocument:
    return ParsedDocument(
        schema_version=PARSE_IR_SCHEMA_VERSION,
        source_identity=ParseIdentity(
            source_raw_hash="raw-hash-1",
            parser_fingerprint=parser_fingerprint,
            parse_ir_schema_version=PARSE_IR_SCHEMA_VERSION,
            normalizer_version="native-base@2",
            rule_config_fingerprint="rc-1",
            reconciler_version="structural-reconciler@1",
        ),
        containers=(Container(container_id="c0", container_type="page", order_index=0),),
        elements=(Element(element_id="e1", element_type="paragraph", order_index=0, text="正文"),),
    )


def _decision(decision: str = "PASS") -> QualityDecision:
    return QualityDecision(decision=decision)


async def _register_ir_object(
    store: FakeObjectStore, objects: MemoryStorageObjectRepository, so_id: str
) -> None:
    payload = b'{"elements": []}'
    import hashlib

    location = ObjectLocation(
        bucket="agentickb-dev-parse",
        object_key=build_object_key(
            "parse_ir", hashlib.sha256(payload).hexdigest()
        ),
    )
    await store.put_bytes(
        location, payload, PutOptions(artifact_class="parse_ir")
    )
    from knowledge_mining.mining.contracts.file_management import (
        StorageObjectRecord,
    )

    await objects.register(StorageObjectRecord(
        id=so_id,
        provider="minio",
        bucket=location.bucket,
        object_key=location.object_key,
        object_version_id=None,
        sha256="deadbeef",
        size=len(payload),
        mime="application/json",
        artifact_class="parse_ir",
        state="AVAILABLE",
        created_at="2026-08-18T00:00:00+00:00",
    ))


def _service(snaps, objects, store, stale=None):  # noqa: ANN001
    from knowledge_mining.mining.snapshot_store.service import (
        SnapshotCommitService,
    )

    async def _no_stale(frozen: FrozenInput) -> None:
        return None

    return SnapshotCommitService(
        snapshots=snaps,
        storage_objects=objects,
        object_store=store,
        stale_checker=stale or _no_stale,
    )


# ---------------------------------------------------------------------------
# memory 仓储
# ---------------------------------------------------------------------------


async def test_memory_commit_inserts_then_reuses() -> None:
    from knowledge_mining.mining.snapshot_store.repositories_memory import (
        MemorySnapshotRepository,
    )
    from knowledge_mining.mining.contracts.snapshot_store import (
        SnapshotSourceLink,
    )

    repo = MemorySnapshotRepository()
    snap = _make_snapshot()
    link = _make_link(snap.id)
    first = await repo.commit(snap, link)
    assert first.created is True
    second = await repo.commit(_make_snapshot(id="snap_other"), _make_link("snap_other"))
    assert second.created is False
    assert second.snapshot.id == snap.id
    assert second.reused_reason


def _make_snapshot(**overrides):
    from knowledge_mining.mining.contracts.snapshot_store import SnapshotRecord

    defaults = dict(
        id="snap_1",
        domain="default",
        snapshot_fingerprint="snap-abc",
        raw_content_hash="raw-hash-1",
        normalized_content_hash="raw-hash-1",
        mime_type="application/pdf",
        parse_ir_storage_object_id="so_ir",
        parse_ir_schema_version="0.2",
        parser_fingerprint="fp",
        quality_status="PASS",
    )
    defaults.update(overrides)
    return SnapshotRecord(**defaults)


def _make_link(snapshot_id: str):
    from knowledge_mining.mining.contracts.snapshot_store import SnapshotSourceLink

    return SnapshotSourceLink(
        id=f"link_{snapshot_id}",
        document_id="doc1",
        document_snapshot_id=snapshot_id,
        source_storage_object_id="so_src",
        source_content_revision=3,
    )


async def test_memory_mark_lifecycle_one_way() -> None:
    from knowledge_mining.mining.snapshot_store.repositories_memory import (
        MemorySnapshotRepository,
    )

    repo = MemorySnapshotRepository()
    await repo.commit(_make_snapshot(), _make_link("snap_1"))
    updated = await repo.mark_lifecycle("snap_1", "DEPRECATED")
    assert updated.lifecycle_status == "DEPRECATED"
    with pytest.raises(ValueError):
        await repo.mark_lifecycle("snap_1", "READY")
    with pytest.raises(KeyError):
        await repo.mark_lifecycle("missing", "REVOKED")


# ---------------------------------------------------------------------------
# SnapshotCommitService
# ---------------------------------------------------------------------------


async def test_commit_happy_path_pass(tmp_path) -> None:
    from knowledge_mining.mining.snapshot_store.repositories_memory import (
        MemorySnapshotRepository,
    )

    snaps = MemorySnapshotRepository()
    objects = MemoryStorageObjectRepository()
    store = FakeObjectStore(str(tmp_path / "objects"))
    await _register_ir_object(store, objects, "so_ir")
    service = _service(snaps, objects, store)

    result = await service.commit(
        frozen=_frozen(), document=_doc(),
        parse_ir_storage_object_id="so_ir",
        quality_decision=_decision("PASS"),
        run_id="run_1", domain="default",
    )
    assert result.created is True
    snap = result.snapshot
    assert snap.quality_status == "PASS"
    assert snap.raw_content_hash == "raw-hash-1"
    assert snap.created_by_run_id == "run_1"
    assert snap.mime_type == "application/pdf"
    assert snap.snapshot_fingerprint.startswith("snap-")
    # 幂等复跑：同输入同管线 → created=False，同 id。
    again = await service.commit(
        frozen=_frozen(), document=_doc(),
        parse_ir_storage_object_id="so_ir",
        quality_decision=_decision("PASS"),
        run_id="run_2", domain="default",
    )
    assert again.created is False
    assert again.snapshot.id == snap.id


async def test_commit_warn_allowed_fail_rejected(tmp_path) -> None:
    from knowledge_mining.mining.snapshot_store.repositories_memory import (
        MemorySnapshotRepository,
    )

    objects = MemoryStorageObjectRepository()
    store = FakeObjectStore(str(tmp_path / "objects"))
    await _register_ir_object(store, objects, "so_ir")
    service = _service(MemorySnapshotRepository(), objects, store)

    warn = await service.commit(
        frozen=_frozen(), document=_doc(),
        parse_ir_storage_object_id="so_ir",
        quality_decision=_decision("WARN"),
        run_id="run_w", domain="default",
    )
    assert warn.snapshot.quality_status == "WARN"

    with pytest.raises(ValueError, match="FAIL"):
        await service.commit(
            frozen=_frozen(), document=_doc(),
            parse_ir_storage_object_id="so_ir",
            quality_decision=_decision("FAIL"),
            run_id="run_f", domain="default",
        )


async def test_commit_stale_input_propagates_and_writes_nothing(tmp_path) -> None:
    from knowledge_mining.mining.frozen_input.contracts import FrozenInputStale
    from knowledge_mining.mining.snapshot_store.repositories_memory import (
        MemorySnapshotRepository,
    )

    objects = MemoryStorageObjectRepository()
    store = FakeObjectStore(str(tmp_path / "objects"))
    await _register_ir_object(store, objects, "so_ir")
    snaps = MemorySnapshotRepository()

    async def _stale(frozen: FrozenInput) -> None:
        raise FrozenInputStale(
            frozen.document_id,
            frozen.source_content_revision,
            4,
        )

    service = _service(snaps, objects, store, stale=_stale)
    with pytest.raises(FrozenInputStale):
        await service.commit(
            frozen=_frozen(), document=_doc(),
            parse_ir_storage_object_id="so_ir",
            quality_decision=_decision("PASS"),
            run_id="run_s", domain="default",
        )
    assert snaps.count() == 0  # 不产生半成品快照（§9.4）


async def test_commit_missing_ir_object_blocks(tmp_path) -> None:
    from knowledge_mining.mining.snapshot_store.repositories_memory import (
        MemorySnapshotRepository,
    )

    objects = MemoryStorageObjectRepository()  # 未注册 so_ir
    store = FakeObjectStore(str(tmp_path / "objects"))
    service = _service(MemorySnapshotRepository(), objects, store)
    with pytest.raises(StorageObjectMissing):
        await service.commit(
            frozen=_frozen(), document=_doc(),
            parse_ir_storage_object_id="so_ir",
            quality_decision=_decision("PASS"),
            run_id="run_m", domain="default",
        )


async def test_commit_fingerprint_sensitive_to_parser(tmp_path) -> None:
    from knowledge_mining.mining.snapshot_store.repositories_memory import (
        MemorySnapshotRepository,
    )

    objects = MemoryStorageObjectRepository()
    store = FakeObjectStore(str(tmp_path / "objects"))
    await _register_ir_object(store, objects, "so_ir")
    service = _service(MemorySnapshotRepository(), objects, store)

    a = await service.commit(
        frozen=_frozen(), document=_doc("native_pdf@2.0.0"),
        parse_ir_storage_object_id="so_ir",
        quality_decision=_decision(), run_id="r1", domain="default",
    )
    b = await service.commit(
        frozen=_frozen(), document=_doc("native_pdf@2.1.0"),
        parse_ir_storage_object_id="so_ir",
        quality_decision=_decision(), run_id="r2", domain="default",
    )
    # 解析器升级 → 新 Snapshot（A07 语义）。
    assert a.snapshot.snapshot_fingerprint != b.snapshot.snapshot_fingerprint
    assert b.created is True




async def test_commit_accepts_compiler_fingerprint(tmp_path) -> None:
    """A08：切片策略指纹进快照身份——compiler 变化 → 新快照."""
    from knowledge_mining.mining.snapshot_store.repositories_memory import (
        MemorySnapshotRepository,
    )

    objects = MemoryStorageObjectRepository()
    store = FakeObjectStore(str(tmp_path / "objects"))
    await _register_ir_object(store, objects, "so_ir")
    service = _service(MemorySnapshotRepository(), objects, store)

    a = await service.commit(
        frozen=_frozen(), document=_doc(),
        parse_ir_storage_object_id="so_ir",
        quality_decision=_decision(), run_id="r1", domain="default",
    )
    b = await service.commit(
        frozen=_frozen(), document=_doc(),
        parse_ir_storage_object_id="so_ir",
        quality_decision=_decision(), run_id="r2", domain="default",
        compiler_fingerprint="segc-abc123",
    )
    assert b.created is True  # 不同 compiler 指纹 → 新快照
    assert b.snapshot.compiler_fingerprint == "segc-abc123"
    assert a.snapshot.compiler_fingerprint is None
    assert a.snapshot.snapshot_fingerprint != b.snapshot.snapshot_fingerprint


async def test_same_content_second_document_still_gets_link(tmp_path) -> None:
    """CRITICAL-1（对抗评审）：同内容不同文档共享指纹时，第二个文档的
    link 也必须写入——否则 latest_for_document(B) 永远 None。"""
    from knowledge_mining.mining.snapshot_store.repositories_memory import (
        MemorySnapshotRepository,
    )

    objects = MemoryStorageObjectRepository()
    store = FakeObjectStore(str(tmp_path / "objects"))
    await _register_ir_object(store, objects, "so_ir")
    snaps = MemorySnapshotRepository()
    service = _service(snaps, objects, store)

    a = await service.commit(
        frozen=_frozen(), document=_doc(),
        parse_ir_storage_object_id="so_ir",
        quality_decision=_decision(), run_id="r1", domain="default",
    )
    assert a.created is True
    # 文档 B：相同内容（同 raw hash/管线）→ 指纹命中复用，但 link 必须补写。
    frozen_b = _frozen()
    frozen_b = type(frozen_b)(
        document_id="doc2",
        source_storage_object_id=frozen_b.source_storage_object_id,
        source_raw_hash=frozen_b.source_raw_hash,
        source_content_revision=frozen_b.source_content_revision,
        mime=frozen_b.mime, size=frozen_b.size,
        original_filename=frozen_b.original_filename,
        captured_at=frozen_b.captured_at, provider=frozen_b.provider,
        bucket=frozen_b.bucket, object_key=frozen_b.object_key,
        object_version_id=frozen_b.object_version_id,
    )
    b = await service.commit(
        frozen=frozen_b, document=_doc(),
        parse_ir_storage_object_id="so_ir",
        quality_decision=_decision(), run_id="r2", domain="default",
    )
    assert b.created is False
    assert b.snapshot.id == a.snapshot.id
    found = await snaps.latest_for_document("doc2", "default")
    assert found is not None, "second document must have a link row"
