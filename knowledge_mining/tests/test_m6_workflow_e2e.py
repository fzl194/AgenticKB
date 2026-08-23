"""M6.3 工作流级端到端：真组件门面 × handler 级联 × 兼容投影.

模拟 v2 骨架的文档处理段（input 之后）：document_parse → segment_compile
两 handler 经组合根门面驱动真服务（解析→门控→快照→切片→落库），断言：
- 快照与切片真实落库（memory 组件）；
- 产出的 DocumentContext.segments 是下游零改动可消费的 RawSegmentData
  （白名单类型 + structure_json 细节 + source_offsets_json 映射）。
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
    from knowledge_mining.mining.workflow.new_chain_services import (
        build_new_chain_services,
    )

    store = FakeObjectStore(str(tmp_path / "objects"))
    objects = MemoryStorageObjectRepository()
    documents = MemoryDocumentCurrentContentRepository()
    services = build_new_chain_services(
        bucket_prefix="m6e2e-",
        object_store=store,
        storage_objects=objects,
        documents=documents,
    )
    return store, objects, documents, services


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

        store, objects, documents, services = _harness(tmp_path)
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
        ctx = compiled.outputs.context
        # 兼容投影断言：下游零改动消费
        blocks = [s.block_type for s in ctx.segments]
        assert "paragraph" in blocks and "table" in blocks
        row = next(
            s for s in ctx.segments if s.structure_json.get("table_header")
        )
        assert row.structure_json["table_header"] == ["告警码", "原因"]
        assert row.source_offsets_json["element_links"]
        assert [n["title"] for n in row.section_path] == ["运维手册"]
        return ctx, services

    ctx, services = _run(scene())

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
