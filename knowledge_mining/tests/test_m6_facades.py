"""M6.3 服务同步门面 + 组合根（RED 先行）.

- ``DocumentParseFacade``：工作流 handler（同步）→ 新链 async 服务。
  raw_file → 查文档当前对象 → 冻结输入 → DocumentParseService（质量
  门控 + 快照转正）→ 解析指针；文档无新链对象 → None（SKIP）；
  Run 终态 FAILED/SUPERSEDED → 抛错（handler 归一为算子失败）。
- ``SegmentCompileFacade``：参数档位 → SegmentPolicy → 编译服务 →
  切片列表（handler 投影消费）。
- ``build_new_chain_services``：组合根（真组件组装，注入 runtime.services）。
"""
from __future__ import annotations

import asyncio
import hashlib
import sys

import pytest

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_mining.mining.contracts.parse_plan import ParsePlan
from knowledge_mining.mining.contracts.storage.types import (  # noqa: E402
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.file_management.repositories_memory import (  # noqa: E402
    MemoryDocumentCurrentContentRepository,
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402
from knowledge_mining.mining.infra.object_store.keys import (  # noqa: E402
    build_object_key,
)
from knowledge_mining.mining.contracts.file_management import (  # noqa: E402
    StorageObjectRecord,
)

MD = "# 手册\n\n正文。\n".encode("utf-8")


async def _seed(store, objects, documents, *, doc_id="doc-1"):
    """注册对象 + 建档（指针即对象），一步到位."""
    sha = hashlib.sha256(MD).hexdigest()
    key = build_object_key("source", sha)
    await store.put_bytes(
        ObjectLocation(bucket="ns-source", object_key=key), MD,
        PutOptions(artifact_class="source"),
    )
    await objects.register(StorageObjectRecord(
        id=f"so_{doc_id}", provider="fake", bucket="ns-source",
        object_key=key, object_version_id=None, sha256=sha,
        size=len(MD), mime="text/markdown", artifact_class="source",
        state="AVAILABLE", created_at="2026-08-21T00:00:00+00:00",
    ))
    await documents.create_document(
        kb_id="kb1", document_id=doc_id, folder_id=None, owner_id=None,
        document_name=f"{doc_id}.md", document_type="other",
        storage_object_id=f"so_{doc_id}", source_raw_hash=sha,
    )
    return sha


class _StubOperator:
    """DocumentParseService 替身：记录 plan/frozen，返回可控终态."""

    def __init__(self, *, final="SUCCEEDED", snapshot_id="snap_1"):
        self.final = final
        self.snapshot_id = snapshot_id
        self.calls: list[tuple[Any, Any]] = []

    async def execute(self, frozen, plan, *, domain, **kwargs):
        self.calls.append((frozen, plan))
        from types import SimpleNamespace

        return SimpleNamespace(
            id="parse_1", run_id="parse_1", status=self.final,
            snapshot_id=self.snapshot_id if self.final == "SUCCEEDED" else None,
            parse_ir_storage_object_id="so_ir" if self.final == "SUCCEEDED" else None,
            error_message=None if self.final == "SUCCEEDED" else "quality decision FAIL",
        )


from typing import Any  # noqa: E402


class _StubCompiler:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def compile(self, snapshot_id, *, parse_ir_storage_object_id,
                      document_key, policy=None):
        from types import SimpleNamespace

        self.calls.append(policy)
        return SimpleNamespace(
            snapshot_id=snapshot_id, segment_count=2,
            compiler_fingerprint="segc-x",
        )


class _StubSegmentStore:
    def __init__(self, segments) -> None:
        self._segments = segments

    async def list_for_snapshot(self, snapshot_id):
        return self._segments

    async def replace_for_snapshot(self, *a, **kw):  # noqa: ANN002, ANN003
        return len(self._segments)


def _raw_file(document_key="doc-1.md"):
    from types import SimpleNamespace

    return SimpleNamespace(
        file_type="markdown", document_key=document_key,
        document_id="doc-1", mime="text/markdown",
    )


# ---------------------------------------------------------------------------
# DocumentParseFacade
# ---------------------------------------------------------------------------


def _parse_facade(operator, documents, objects, store, *, plan_factory=None):
    from knowledge_mining.mining.frozen_input.service import FrozenInputService
    from knowledge_mining.mining.workflow.new_chain_services import (
        DocumentParseFacade,
    )

    # P03 后 freeze 链是门面必需组件（frozen_inputs 缺失 → 一律 SKIP）。
    frozen_inputs = FrozenInputService(
        documents=documents, storage_objects=objects, object_store=store,
    )
    return DocumentParseFacade(
        operator=operator, documents=documents, storage_objects=objects,
        frozen_inputs=frozen_inputs,
        plan_factory=plan_factory or (lambda raw, params: ParsePlan(
            plan_id="p", primary_parser_id="legacy_markdown",
        )),
    )


def test_parse_facade_happy_path_returns_outcome(tmp_path):
    async def scene():
        store = FakeObjectStore(str(tmp_path / "facade-objects"))
        objects = MemoryStorageObjectRepository()
        documents = MemoryDocumentCurrentContentRepository()
        await _seed(store, objects, documents)
        operator = _StubOperator()
        facade = _parse_facade(operator, documents, objects, store)
        outcome = facade.parse_document(
            _raw_file(), params={"qualityProfile": "default"},
            domain="default", run_document_id="rd-1",
        )
        assert outcome.run_id == "parse_1"
        assert outcome.snapshot_id == "snap_1"
        frozen, plan = operator.calls[0]
        assert frozen.document_id == "doc-1"
        assert frozen.source_content_revision == 1
        return True

    assert asyncio.new_event_loop().run_until_complete(scene())


def test_parse_facade_document_without_object_returns_none(tmp_path):
    async def scene():
        documents = MemoryDocumentCurrentContentRepository()
        objects = MemoryStorageObjectRepository()  # 空：对象未注册
        store = FakeObjectStore(str(tmp_path / "facade-objects"))
        await documents.create_document(
            kb_id="kb1", document_id="doc-9", folder_id=None, owner_id=None,
            document_name="d", document_type="other",
            storage_object_id="so_missing", source_raw_hash="x" * 0 or "x",
        )  # 指针指向未注册对象（旧链 storage_path 文档形态）
        facade = _parse_facade(_StubOperator(), documents, objects, store)
        from types import SimpleNamespace

        raw = SimpleNamespace(
            file_type="markdown", document_key="d",
            document_id="doc-9", mime="text/markdown",
        )
        assert facade.parse_document(
            raw, params={}, domain="default", run_document_id="rd",
        ) is None
        return True

    assert asyncio.new_event_loop().run_until_complete(scene())


def test_parse_facade_failed_run_raises(tmp_path):
    async def scene():
        documents = MemoryDocumentCurrentContentRepository()
        store = FakeObjectStore(str(tmp_path / "facade-objects"))
        objects = MemoryStorageObjectRepository()
        await _seed(store, objects, documents)
        facade = _parse_facade(
            _StubOperator(final="FAILED"), documents, objects, store,
        )
        with pytest.raises(RuntimeError, match="quality decision FAIL"):
            facade.parse_document(
                _raw_file(), params={}, domain="default",
                run_document_id="rd",
            )
        return True

    assert asyncio.new_event_loop().run_until_complete(scene())


def test_parse_facade_maps_budget_params_into_plan(tmp_path):
    async def scene():
        documents = MemoryDocumentCurrentContentRepository()
        store = FakeObjectStore(str(tmp_path / "facade-objects"))
        objects = MemoryStorageObjectRepository()
        await _seed(store, objects, documents)
        operator = _StubOperator()
        from knowledge_mining.mining.frozen_input.service import FrozenInputService
        from knowledge_mining.mining.workflow.new_chain_services import (
            DocumentParseFacade,
        )

        facade = DocumentParseFacade(
            operator=operator, documents=documents,
            storage_objects=objects,
            frozen_inputs=FrozenInputService(
                documents=documents, storage_objects=objects, object_store=store,
            ),
            plan_factory=DocumentParseFacade.default_plan_factory,
        )
        facade.parse_document(
            _raw_file(), params={
                "maxBackendAttempts": 2, "qualityProfile": "strict",
            }, domain="default",
            run_document_id="rd",
        )
        _, plan = operator.calls[0]
        assert plan.budget.max_backend_attempts == 2
        assert plan.primary_parser_id == "legacy_markdown"
        assert plan.quality_profile == "strict"
        return True

    assert asyncio.new_event_loop().run_until_complete(scene())


# ---------------------------------------------------------------------------
# SegmentCompileFacade
# ---------------------------------------------------------------------------


def test_compile_facade_maps_params_to_policy_and_returns_segments():
    async def scene():
        from knowledge_mining.mining.contracts.segment_compiler import (
            CompiledSegment,
        )
        from knowledge_mining.mining.workflow.new_chain_services import (
            SegmentCompileFacade,
        )

        segments = (
            CompiledSegment(segment_index=0, block_type="paragraph", raw_text="x"),
            CompiledSegment(segment_index=1, block_type="table_row", raw_text="a\tb"),
        )
        compiler = _StubCompiler()
        store = _StubSegmentStore(segments)
        facade = SegmentCompileFacade(
            compiler=compiler, segment_store=store,
        )
        result = facade.compile_for_snapshot(
            snapshot_id="snap_1", parse_ir_storage_object_id="so_ir",
            params={"tableView": "whole", "maxTokens": 256, "minTokens": 64},
        )
        policy = compiler.calls[0]
        assert policy.table_view == "whole"
        assert policy.max_tokens == 256
        assert result.segments == segments
        return True

    assert asyncio.new_event_loop().run_until_complete(scene())


# ---------------------------------------------------------------------------
# 组合根
# ---------------------------------------------------------------------------


def test_build_new_chain_services_returns_sync_facades():
    from knowledge_mining.mining.workflow.new_chain_services import (
        build_new_chain_services,
    )

    services = build_new_chain_services(bucket_prefix="test-")
    assert hasattr(services.document_parse_service, "parse_document")
    assert hasattr(services.segment_compile_service, "compile_for_snapshot")


def test_build_new_chain_services_uses_pg_repositories_for_sync_worker_pool():
    """A production worker pool must not silently select memory repositories."""
    from knowledge_mining.mining.workflow.new_chain_services import (
        build_new_chain_services,
    )

    services = build_new_chain_services(
        bucket_prefix="test-",
        object_store=object(),
        sync_pool=object(),
    )

    assert type(services.document_parse_service._documents).__name__ == (
        "PgDocumentCurrentContentRepository"
    )
    assert type(services.document_parse_service._storage_objects).__name__ == (
        "PgStorageObjectRepository"
    )
