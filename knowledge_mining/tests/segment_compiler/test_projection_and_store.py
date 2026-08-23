"""M5.3 切片兼容投影 + 落库服务（RED 先行）.

- ``to_raw_segment_data``：CompiledSegment -> RawSegmentData（现有下游
  enrich/retrieval_unit/embedding 零改动消费，SRS §2.3 兼容不变量）。
- ``SegmentCompileService``：从快照的 Parse IR 对象编译切片并落库
  （替换语义：重切覆盖旧切片，§741 db 惯例）；element links 随切片持久化；
  compiler_fingerprint 记录（A08：策略变化 → 新指纹）。
"""
from __future__ import annotations

import hashlib
import sys

import pytest

pytestmark = pytest.mark.asyncio

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_mining.mining.contracts.models import RawSegmentData  # noqa: E402
from knowledge_mining.mining.contracts.parse_ir.enums import (  # noqa: E402
    PARSE_IR_SCHEMA_VERSION,
)
from knowledge_mining.mining.contracts.parse_ir.types import (  # noqa: E402
    Container,
    Element,
    EvidenceSpan,
    ParseIdentity,
    ParsedDocument,
    Relation,
)
from knowledge_mining.mining.contracts.segment_compiler import (  # noqa: E402
    CompiledSegment,
    SegmentElementLink,
    SegmentPolicy,
)
from knowledge_mining.mining.contracts.storage.types import (  # noqa: E402
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.file_management.repositories_memory import (  # noqa: E402
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402
from knowledge_mining.mining.infra.object_store.keys import (  # noqa: E402
    build_object_key,
)


# ---------------------------------------------------------------------------
# 兼容投影
# ---------------------------------------------------------------------------


def _seg() -> CompiledSegment:
    return CompiledSegment(
        segment_index=0,
        block_type="table_row",
        raw_text="A-101\t风扇停转",
        heading_chain=((1, "告警处理"), (2, "硬件告警")),
        element_ids=("t1",),
        links=(SegmentElementLink(
            element_id="t1", evidence_span_ids=("s3", "s4"),
        ),),
        metadata={"table_header": ["告警码", "原因"], "row_index": 1},
    )


def test_to_raw_segment_data_projection() -> None:
    from knowledge_mining.mining.segment_compiler.projection import (
        to_raw_segment_data,
    )

    rsd: RawSegmentData = to_raw_segment_data(
        _seg(), document_key="manual.pdf"
    )
    assert rsd.document_key == "manual.pdf"
    assert rsd.segment_index == 0
    # 新链类型收敛到 legacy 白名单（DB CHECK + 下游分支安全），
    # 行细节保留在 structure_json。
    assert rsd.block_type == "table"
    assert rsd.raw_text == "A-101\t风扇停转"
    assert rsd.section_title == "硬件告警"  # 最内层标题
    assert [n["title"] for n in rsd.section_path] == ["告警处理", "硬件告警"]
    # 元素/证据映射进 source_offsets_json（检索命中可回原文定位）。
    offsets = rsd.source_offsets_json
    assert offsets["element_links"][0]["element_id"] == "t1"
    assert offsets["element_links"][0]["evidence_span_ids"] == ["s3", "s4"]
    assert rsd.structure_json["table_header"] == ["告警码", "原因"]
    assert rsd.content_hash  # 非空（下游按内容去重）


# ---------------------------------------------------------------------------
# 编译落库服务
# ---------------------------------------------------------------------------


def _doc() -> ParsedDocument:
    return ParsedDocument(
        schema_version=PARSE_IR_SCHEMA_VERSION,
        source_identity=ParseIdentity(
            source_raw_hash="raw-1", parser_fingerprint="t@1",
            parse_ir_schema_version=PARSE_IR_SCHEMA_VERSION,
        ),
        containers=(Container(container_id="c0", container_type="page", order_index=0),),
        elements=(
            Element(
                element_id="h0", element_type="heading", order_index=0,
                text="章一", style={"level": 1},
                source_spans=(EvidenceSpan(span_id="s0", raw_text="章一"),),
            ),
            Element(
                element_id="p1", element_type="paragraph", order_index=1,
                text="正文内容甲。", parent_id="h0",
                source_spans=(EvidenceSpan(span_id="s1", raw_text="甲"),),
            ),
            Element(
                element_id="p2", element_type="paragraph", order_index=2,
                text="正文内容乙。", parent_id="h0",
                source_spans=(EvidenceSpan(span_id="s2", raw_text="乙"),),
            ),
        ),
        relations=(
            Relation(source_element_id="p1", target_element_id="p2",
                     relation_type="next_in_reading_order", method="t"),
        ),
    )


async def _seed_ir(store, objects) -> str:
    import json as _json

    payload = _json.dumps(
        _doc().to_dict(), ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    location = ObjectLocation(
        bucket="b-parse",
        object_key=build_object_key(
            "parse_ir", hashlib.sha256(payload).hexdigest()
        ),
    )
    await store.put_bytes(location, payload, PutOptions(artifact_class="parse_ir"))
    from knowledge_mining.mining.contracts.file_management import (
        StorageObjectRecord,
    )

    await objects.register(StorageObjectRecord(
        id="so_ir", provider="minio", bucket=location.bucket,
        object_key=location.object_key, object_version_id=None,
        sha256=hashlib.sha256(payload).hexdigest(), size=len(payload),
        mime="application/json", artifact_class="parse_ir", state="AVAILABLE",
        created_at="2026-08-19T00:00:00+00:00",
    ))
    return "so_ir"


async def test_compile_service_persists_and_replaces(tmp_path) -> None:
    from knowledge_mining.mining.segment_compiler.repositories_memory import (
        MemorySegmentStore,
    )
    from knowledge_mining.mining.segment_compiler.service import (
        SegmentCompileService,
    )

    store = FakeObjectStore(str(tmp_path / "objects"))
    objects = MemoryStorageObjectRepository()
    ir_id = await _seed_ir(store, objects)
    seg_store = MemorySegmentStore()
    service = SegmentCompileService(
        object_store=store, storage_objects=objects, segment_store=seg_store,
    )

    result = await service.compile(
        "snap_1", parse_ir_storage_object_id=ir_id,
        document_key="a.pdf", policy=SegmentPolicy(merge_adjacent_paragraphs=True),
    )
    assert result.segment_count >= 1
    stored = await seg_store.list_for_snapshot("snap_1")
    assert stored and all(s.links for s in stored)
    assert await seg_store.link_count("snap_1") >= len(stored)
    assert await seg_store.compiler_fingerprint("snap_1") == result.compiler_fingerprint

    # 重切（策略变化）→ 替换旧切片，不叠加（db.py:741 替换语义）。
    result2 = await service.compile(
        "snap_1", parse_ir_storage_object_id=ir_id,
        document_key="a.pdf",
        policy=SegmentPolicy(merge_adjacent_paragraphs=False),
    )
    stored2 = await seg_store.list_for_snapshot("snap_1")
    assert len(stored2) == result2.segment_count
    assert result2.compiler_fingerprint != result.compiler_fingerprint
    assert await seg_store.compiler_fingerprint("snap_1") == result2.compiler_fingerprint

    # 共享快照幂等：同内容多文档并发编译同一快照——同指纹直接复用，
    # 不得二次 replace（真库上并发 replace 会在 segment_key 唯一键上相撞）。
    result3 = await service.compile(
        "snap_1", parse_ir_storage_object_id=ir_id,
        document_key="a.pdf",
        policy=SegmentPolicy(merge_adjacent_paragraphs=False),
    )
    assert result3.compiler_fingerprint == result2.compiler_fingerprint
    stored3 = await seg_store.list_for_snapshot("snap_1")
    assert stored3 == stored2  # 未被重写，仍是 result2 的产出


async def test_compile_service_projection_roundtrip(tmp_path) -> None:
    from knowledge_mining.mining.segment_compiler.projection import (
        to_raw_segment_data,
    )
    from knowledge_mining.mining.segment_compiler.repositories_memory import (
        MemorySegmentStore,
    )
    from knowledge_mining.mining.segment_compiler.service import (
        SegmentCompileService,
    )

    store = FakeObjectStore(str(tmp_path / "objects"))
    objects = MemoryStorageObjectRepository()
    ir_id = await _seed_ir(store, objects)
    seg_store = MemorySegmentStore()
    service = SegmentCompileService(
        object_store=store, storage_objects=objects, segment_store=seg_store,
    )
    await service.compile(
        "snap_9", parse_ir_storage_object_id=ir_id, document_key="a.pdf",
    )
    for seg in await seg_store.list_for_snapshot("snap_9"):
        rsd = to_raw_segment_data(seg, document_key="a.pdf")
        assert isinstance(rsd, RawSegmentData)
        assert rsd.section_path or seg.heading_chain == ()


def _frozen():
    from knowledge_mining.mining.frozen_input.contracts import FrozenInput

    return FrozenInput(
        document_id="doc1", source_storage_object_id="so_src",
        source_raw_hash="raw-1", source_content_revision=3,
        mime="application/pdf", size=100, original_filename="a.pdf",
        captured_at="2026-08-19T00:00:00+00:00", provider="minio",
        bucket="agentickb-dev-source", object_key="v1/ab/cd/src-1",
        object_version_id=None,
    )


def _decision():
    from knowledge_mining.mining.parse_quality.gate import QualityDecision

    return QualityDecision(decision="PASS")


async def test_recompile_reuses_ir_and_creates_new_snapshot(tmp_path) -> None:
    """A08：切片策略升级 → 复用 IR 产新快照并重切；旧快照原样保留."""
    from knowledge_mining.mining.snapshot_store.repositories_memory import (
        MemorySnapshotRepository,
    )
    from knowledge_mining.mining.snapshot_store.service import (
        SnapshotCommitService,
    )
    from knowledge_mining.mining.segment_compiler.repositories_memory import (
        MemorySegmentStore,
    )
    from knowledge_mining.mining.segment_compiler.service import (
        SnapshotRecompileService,
    )

    store = FakeObjectStore(str(tmp_path / "objects"))
    objects = MemoryStorageObjectRepository()
    ir_id = await _seed_ir(store, objects)
    snapshots = MemorySnapshotRepository()

    async def _no_stale(frozen) -> None:  # noqa: ANN001
        return None

    commit = SnapshotCommitService(
        snapshots=snapshots, stale_checker=_no_stale,
        storage_objects=objects, object_store=store,
    )
    seg_store = MemorySegmentStore()
    first = await commit.commit(
        frozen=_frozen(), document=_doc(),
        parse_ir_storage_object_id=ir_id,
        quality_decision=_decision(), run_id="r1", domain="default",
    )

    from knowledge_mining.mining.segment_compiler.service import (
        SegmentCompileService,
    )

    recompiler = SnapshotRecompileService(
        snapshots=snapshots, commit_service=commit,
        compile_service=SegmentCompileService(
            object_store=store, storage_objects=objects,
            segment_store=seg_store,
        ),
    )
    new_snap, compile_result = await recompiler.recompile(
        first.snapshot.id, frozen=_frozen(), domain="default",
        policy=SegmentPolicy(table_view="whole"),
    )
    assert new_snap.id != first.snapshot.id
    assert new_snap.compiler_fingerprint == compile_result.compiler_fingerprint
    # 旧快照原样保留（历史可追溯）
    old = await snapshots.get(first.snapshot.id)
    assert old is not None and old.lifecycle_status == "READY"
    assert old.compiler_fingerprint is None
    # 新快照下有切片；旧快照 id 下没有
    assert await seg_store.list_for_snapshot(new_snap.id)
    assert not await seg_store.list_for_snapshot(first.snapshot.id)
