"""M5.3：golden corpus 切片端到端验收（真实适配器 → 快照 → 编译）.

50 份语料全链路：解析 → 转正快照 → 切片编译 → 断言：

- 每条切片都映射回原文元素（links 非空——检索命中可回原文定位）；
- 家具（页眉/页脚/页码）不进知识切片；
- 表格行切片携带表头（召回时自带列语义）；
- 全部切片可投影为 RawSegmentData（现有下游零改动消费，§2.3）；
- 正例语料切片非空。
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
from knowledge_mining.mining.contracts.parse_plan import ParsePlan  # noqa: E402
from knowledge_mining.mining.contracts.segment_compiler import (  # noqa: E402
    SegmentPolicy,
)
from knowledge_mining.mining.contracts.storage.types import (  # noqa: E402
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.file_management.repositories_memory import (  # noqa: E402
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.frozen_input.contracts import FrozenInput  # noqa: E402
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402
from knowledge_mining.mining.parse_adapters.factory import resolve_pipeline  # noqa: E402
from knowledge_mining.mining.parse_operator.service import (  # noqa: E402
    DocumentParseService,
)
from knowledge_mining.mining.parse_quality.gate import QualityGate  # noqa: E402
from knowledge_mining.mining.parse_reconciler import StructuralReconciler  # noqa: E402
from knowledge_mining.mining.segment_compiler.projection import (  # noqa: E402
    to_raw_segment_data,
)
from knowledge_mining.mining.segment_compiler.repositories_memory import (  # noqa: E402
    MemorySegmentStore,
)
from knowledge_mining.mining.segment_compiler.service import (  # noqa: E402
    SegmentCompileService,
)
from knowledge_mining.mining.shadow_parse.repositories_memory import (  # noqa: E402
    MemoryParseAttemptRepository,
    MemoryParseRunRepository,
)
from knowledge_mining.mining.snapshot_store.repositories_memory import (  # noqa: E402
    MemorySnapshotRepository,
)
from knowledge_mining.mining.snapshot_store.service import (  # noqa: E402
    SnapshotCommitService,
)

from tests.golden_corpus.corpus import PARSER_ID, build_corpus  # noqa: E402


async def test_corpus_segments_end_to_end(tmp_path) -> None:
    store = FakeObjectStore(str(tmp_path / "objects"))
    storage_objects = MemoryStorageObjectRepository()
    snapshots = MemorySnapshotRepository()
    seg_store = MemorySegmentStore()

    async def _no_stale(frozen: FrozenInput) -> None:
        return None

    commit = SnapshotCommitService(
        snapshots=snapshots, stale_checker=_no_stale,
    )
    operator = DocumentParseService(
        object_store=store, parse_runs=MemoryParseRunRepository(),
        attempts=MemoryParseAttemptRepository(),
        storage_objects=storage_objects, parser_resolver=resolve_pipeline,
        commit_service=commit, quality_gate=QualityGate(),
        reconciler=StructuralReconciler(), bucket_prefix="corpus-m5-",
    )
    compiler = SegmentCompileService(
        object_store=store, storage_objects=storage_objects,
        segment_store=seg_store,
    )

    compiled_docs = 0
    table_row_with_header = 0
    for doc in build_corpus():
        sha = hashlib.sha256(doc.data).hexdigest()
        frozen = FrozenInput(
            document_id=f"doc-{doc.name}",
            source_storage_object_id=f"so-{doc.name}",
            source_raw_hash=sha, source_content_revision=1, mime=doc.mime,
            size=len(doc.data), original_filename=doc.name,
            captured_at="2026-08-19T00:00:00+00:00", provider="minio",
            bucket="corpus-m5-source", object_key=f"v1/ab/{sha[:12]}",
        )
        await store.put_bytes(
            ObjectLocation(bucket=frozen.bucket, object_key=frozen.object_key),
            doc.data, PutOptions(artifact_class="source"),
        )
        run = await operator.execute(
            frozen,
            ParsePlan(plan_id="p", primary_parser_id=PARSER_ID[doc.format_key]),
            domain="default", source_text=doc.source_text,
        )
        if run.status != "SUCCEEDED":
            continue  # 空坏样本无快照（M4 验收已覆盖）
        compiled_docs += 1

        result = await compiler.compile(
            run.snapshot_id,
            parse_ir_storage_object_id=run.parse_ir_storage_object_id,
            document_key=doc.name,
        )
        # 纯图无文字样本（如 pptx-picture）允许 0 切片——无可索引内容
        # 不伪造；有文本期望的语料必须编译出切片。
        has_text_expectation = bool(
            doc.expectations.expected_headings
            or doc.expectations.expected_paragraph_anchors
            or doc.expectations.expected_table_count
        )
        if has_text_expectation:
            assert result.segment_count >= 1, doc.name
        for seg in await seg_store.list_for_snapshot(run.snapshot_id):
            # 1) 每条切片映射回原文元素
            assert seg.links, f"{doc.name}/{seg.segment_index}: no links"
            assert all(l.element_id for l in seg.links)
            # 2) 家具不进切片
            assert seg.block_type not in (
                "page_header", "page_footer", "page_number",
            )
            # 4) 兼容投影
            rsd = to_raw_segment_data(seg, document_key=doc.name)
            assert isinstance(rsd, RawSegmentData)
            if seg.block_type == "table_row":
                assert seg.metadata.get("table_header"), (
                    f"{doc.name}: table_row without header"
                )
                table_row_with_header += 1

    # 正例语料全部编译出切片
    positives = sum(
        1 for d in build_corpus() if d.category in ("positive", "complex")
    )
    assert compiled_docs >= positives
    # 语料含真实表格样本：至少一批行切片携带表头
    assert table_row_with_header >= 3
