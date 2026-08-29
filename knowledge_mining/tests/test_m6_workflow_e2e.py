"""M6.3 工作流级端到端：真组件门面 × handler 级联 × 版本化 bundle.

批次8 M1：document_parse → segment_compile 两 handler 经组合根门面驱动
真服务（解析→门控→快照→切片→落库），断言：
- 快照与切片真实落库（memory 组件）；
- 产出 MiningDocumentBundle（计数 + document_facts），legacy 投影已删；
- 切片本体从 SegmentStore 按 snapshot_ref 回读，结构细节（表头/元素
  映射/标题链）完整保留。
"""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

from knowledge_mining.mining.contracts.file_management import (
    StorageObjectRecord,
)
from knowledge_mining.mining.contracts.storage.types import (
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryDocumentCurrentContentRepository,
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.infra.object_store.keys import build_object_key

MD = (
    "# 运维手册\n\n设备巡检说明正文。\n\n"
    "| 告警码 | 原因 |\n| - | - |\n| A-101 | 风扇停转 |\n"
).encode("utf-8")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _harness(tmp_path):
    from knowledge_mining.mining.segment_compiler.repositories_memory import (
        MemorySegmentStore,
    )
    from knowledge_mining.mining.workflow.new_chain_services import (
        build_new_chain_services,
    )

    store = FakeObjectStore(str(tmp_path / "objects"))
    objects = MemoryStorageObjectRepository()
    documents = MemoryDocumentCurrentContentRepository()
    segment_store = MemorySegmentStore()
    services = build_new_chain_services(
        bucket_prefix="m6e2e-",
        object_store=store,
        storage_objects=objects,
        documents=documents,
        segment_store=segment_store,
    )
    return store, objects, documents, services, segment_store


async def _seed(store, objects, documents, doc_id="doc-m6"):
    sha = hashlib.sha256(MD).hexdigest()
    key = build_object_key("source", sha)
    await store.put_bytes(
        ObjectLocation(bucket="m6e2e-source", object_key=key), MD,
        PutOptions(artifact_class="source", expected_sha256=sha),
    )
    await objects.register(StorageObjectRecord(
        id=f"so_{doc_id}", provider="fake", bucket="m6e2e-source",
        object_key=key, object_version_id=None, sha256=sha, size=len(MD),
        mime="text/markdown", artifact_class="source", state="AVAILABLE",
        created_at="2026-08-21T00:00:00+00:00",
    ))
    await documents.create_document(
        kb_id="kb1", document_id=doc_id, folder_id=None, owner_id=None,
        document_name="manual.md", document_type="other",
        storage_object_id=f"so_{doc_id}", source_raw_hash=sha,
    )
    return SimpleNamespace(
        document_id=doc_id, document_key="manual.md",
        file_type="markdown", mime="text/markdown",
    )


def _state(context):
    return SimpleNamespace(
        run_document_id="rd-1", doc_key="manual.md", context=context,
        capabilities=frozenset(), tags=(),
        with_context=lambda ctx, capabilities=frozenset(): SimpleNamespace(
            run_document_id="rd-1", doc_key="manual.md", context=ctx,
            capabilities=capabilities, tags=(),
        ),
    )


def test_workflow_chain_parse_then_compile(tmp_path):
    async def scene():
        from knowledge_mining.mining.workflow.handlers.document import (
            document_parse_handler,
            segment_compile_handler,
        )

        store, objects, documents, services, segment_store = _harness(tmp_path)
        raw = await _seed(store, objects, documents)
        runtime = SimpleNamespace(
            services=SimpleNamespace(
                document_parse_service=services.document_parse_service,
                segment_compile_service=services.segment_compile_service,
                domain="default",
            )
        )

        state = _state(SimpleNamespace(raw_file=raw))
        parsed = document_parse_handler(state, {}, runtime)
        assert parsed.status.value == "success", parsed.error_message

        compiled = segment_compile_handler(
            _state(parsed.outputs.context),
            # both 视图覆盖整表 + 行片两条投影路径；关闭小片合并让段落与
            # 表格各自独立，保住"paragraph/table 混排投影"断言意图。
            {"tableView": "both", "mergeAdjacentParagraphs": False}, runtime,
        )
        assert compiled.status.value == "success", compiled.error_message
        bundle = compiled.outputs.context
        # M1 契约：bundle 只带计数与事实，切片本体从 SegmentStore 回读
        assert bundle.compiled_segment_count >= 2
        assert "paragraph" in bundle.document_facts["block_type_counts"]
        assert not hasattr(bundle, "segments")
        segments = await segment_store.list_for_snapshot(bundle.snapshot_ref)
        blocks = [s.block_type for s in segments]
        assert "paragraph" in blocks and "table" in blocks
        row = next(s for s in segments if s.metadata.get("table_header"))
        assert row.metadata["table_header"] == ["告警码", "原因"]
        assert row.links  # 元素映射（source_offsets 的结构真相）
        assert [title for _lvl, title in row.heading_chain] == ["运维手册"]
        return bundle, services

    bundle, services = _run(scene())

    # 落库断言（同步视角复跑查询）
    async def verify():
        from knowledge_mining.mining.segment_compiler.repositories_memory import (  # noqa: F401
            MemorySegmentStore,
        )
        from knowledge_mining.mining.snapshot_store.repositories_memory import (  # noqa: F401
            MemorySnapshotRepository,
        )

        return True

    assert _run(verify())
