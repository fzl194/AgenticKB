"""A0-1（34 号 §P0-1）：文档 current_serving / latest_revision 双视图.

同一篇文档的「当前可搜索版本」（Java serving 对该文档的选择规则）与「最新上传
版本的解析结果」可能不同——此前结构化页只读 latest 且无任何说明。

契约：
- 默认视图 current_serving：解析规则与 Java serving 一致（由路由层 kbdb 提供
  serving 快照上下文，read_service 消费）；
- 无 current_serving 时回落展示 latest，但 versioning 必须标记「尚未进入搜索」
  （latest_state=not_in_search），不得冒充当前知识；
- view=latest_revision 显式看最新解析；
- in_sync 判定基于两个视图的 snapshot 身份。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from knowledge_mining.mining.kb.routes.documents import document_parse_result
from knowledge_mining.mining.snapshot_store.read_service import ParseResultReadService

USER = {"id": "alice"}
KB = "kb-serving"


# ---------------------------------------------------------------------------
# 组件装配：同文档两代快照（rev1=旧内容/已进入搜索；rev2=新内容/尚未挖掘）
# ---------------------------------------------------------------------------


async def _kb_with_two_revisions(tmp_path):
    """同一文档两代快照：rev1（旧内容）/ rev2（重传后的当前内容）→ snap1/snap2."""
    from datetime import datetime, timezone
    import hashlib

    from knowledge_mining.mining.contracts.file_management import StorageObjectRecord
    from knowledge_mining.mining.contracts.storage.types import (
        ObjectLocation,
        PutOptions,
    )
    from knowledge_mining.mining.file_management.repositories_memory import (
        MemoryDocumentCurrentContentRepository,
        MemoryStorageObjectRepository,
    )
    from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
    from knowledge_mining.mining.segment_compiler.repositories_memory import (
        MemorySegmentStore,
    )
    from knowledge_mining.mining.snapshot_store.repositories_memory import (
        MemorySnapshotRepository,
    )
    from knowledge_mining.mining.workflow.new_chain_services import (
        build_new_chain_services,
    )
    from knowledge_mining.tests.new_chain.test_multi_format_pipeline import (
        _seed_document,
    )

    store = FakeObjectStore(str(tmp_path / "objects"))
    objects = MemoryStorageObjectRepository()
    documents = MemoryDocumentCurrentContentRepository()
    snapshots = MemorySnapshotRepository()
    segment_store = MemorySegmentStore()
    services = build_new_chain_services(
        bucket_prefix="serving-", object_store=store, storage_objects=objects,
        documents=documents, snapshots=snapshots, segment_store=segment_store,
    )

    doc_id = "doc-serving"

    async def _put_object(content: bytes, so_id: str) -> None:
        sha = hashlib.sha256(content).hexdigest()
        key = f"rev/{sha[:2]}/{sha}"

        async def _chunks():
            yield content

        await store.put_stream(
            ObjectLocation(bucket="serving-bucket", object_key=key),
            _chunks(),
            PutOptions(artifact_class="source", expected_sha256=sha),
        )
        await objects.register(StorageObjectRecord(
            id=so_id, provider="fake", bucket="serving-bucket", object_key=key,
            object_version_id=None, sha256=sha, size=len(content),
            mime="text/markdown", artifact_class="source", state="AVAILABLE",
            created_at=datetime.now(timezone.utc).isoformat(),
        ))

    # rev1：建档 + 解析 → snap1
    content1 = "# 旧版\n\n第一代内容。".encode("utf-8")
    raw1 = await _seed_document(
        store, objects, documents, fmt="md", data=content1, doc_id=doc_id,
        document_key="sample.md",
    )
    outcome1 = services.document_parse_service.parse_document(
        raw1, params={}, domain="e2e", run_document_id="rd-rev1",
    )
    snap1 = outcome1.snapshot_id

    # rev2：重传（content_revision 1→2）+ 再解析 → snap2（当前文件的 latest）
    content2 = "# 新版\n\n第二代内容。".encode("utf-8")
    sha2 = hashlib.sha256(content2).hexdigest()
    await _put_object(content2, "so-rev2")
    await documents.set_current_content(
        doc_id, "so-rev2", sha2, expected_revision=1,
    )
    raw2 = SimpleNamespace(
        document_id=doc_id, document_key="sample.md",
        file_type="md", mime="text/markdown",
    )
    outcome2 = services.document_parse_service.parse_document(
        raw2, params={}, domain="e2e", run_document_id="rd-rev2",
    )
    snap2 = outcome2.snapshot_id
    assert snap1 != snap2

    components = {
        "snapshots": snapshots, "storage_objects": objects,
        "object_store": store, "segment_store": segment_store,
        "documents": documents,
    }
    return snap1, snap2, components, documents, doc_id


async def _current_document(documents, document_id):
    return await documents.get(document_id)


class _KbDb:
    """Fake KbDB：serving 查询结果可注入."""

    def __init__(self, documents: dict[str, dict], serving: dict | None) -> None:
        self._documents = documents
        self.serving = serving

    async def is_visible(self, *, kb_id: str, user_id: str) -> bool:
        return True

    async def get_document_identity(self, document_id: str) -> dict | None:
        return self._documents.get(document_id)

    async def get_current_serving_snapshot(self, kb_id: str, document_id: str):
        return self.serving


def _request(components) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        parse_result_components=components,
    )))


# ---------------------------------------------------------------------------
# read_service：视图选择
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_serving_prefers_serving_snapshot(tmp_path):
    """默认视图用 serving 快照——即使最新解析（rev2）更新."""
    snap1, snap2, components, documents, doc_id = await _kb_with_two_revisions(tmp_path)
    assert doc_id, "fixture must expose document id"
    service = ParseResultReadService(
        snapshots=components["snapshots"],
        storage_objects=components["storage_objects"],
        object_store=components["object_store"],
        segment_store=components["segment_store"],
        documents=documents,
    )
    serving = {
        "document_snapshot_id": snap1, "build_id": "build-1",
        "source_content_revision": 1,
    }

    result = await service.get_parse_result(
        domain="e2e", document_id=doc_id, view="current_serving", serving=serving,
    )

    assert result["snapshot"]["id"] == snap1
    assert result["versioning"]["view"] == "current_serving"
    assert result["versioning"]["in_sync"] is False
    assert result["versioning"]["latest_state"] == "not_in_search"
    assert result["versioning"]["serving"]["document_snapshot_id"] == snap1
    assert result["versioning"]["latest"]["document_snapshot_id"] == snap2


@pytest.mark.asyncio
async def test_no_serving_falls_back_to_latest_with_marker(tmp_path):
    """无 current_serving（未挖掘/失败）：展示 latest 但必须标记尚未进入搜索."""
    snap1, snap2, components, documents, doc_id = await _kb_with_two_revisions(tmp_path)
    service = ParseResultReadService(
        snapshots=components["snapshots"],
        storage_objects=components["storage_objects"],
        object_store=components["object_store"],
        segment_store=components["segment_store"],
        documents=documents,
    )

    result = await service.get_parse_result(
        domain="e2e", document_id=doc_id, view="current_serving", serving=None,
    )

    assert result["snapshot"]["id"] == snap2
    assert result["versioning"]["in_sync"] is False
    assert result["versioning"]["latest_state"] == "not_in_search"
    assert result["versioning"]["serving"] is None


@pytest.mark.asyncio
async def test_in_sync_when_serving_is_latest(tmp_path):
    snap1, snap2, components, documents, doc_id = await _kb_with_two_revisions(tmp_path)
    service = ParseResultReadService(
        snapshots=components["snapshots"],
        storage_objects=components["storage_objects"],
        object_store=components["object_store"],
        segment_store=components["segment_store"],
        documents=documents,
    )

    result = await service.get_parse_result(
        domain="e2e", document_id=doc_id, view="current_serving",
        serving={"document_snapshot_id": snap2, "build_id": "b2",
                 "source_content_revision": 2},
    )

    assert result["snapshot"]["id"] == snap2
    assert result["versioning"]["in_sync"] is True
    assert result["versioning"]["latest_state"] == "in_search"


@pytest.mark.asyncio
async def test_latest_revision_view_ignores_serving(tmp_path):
    """view=latest_revision：显式看最新上传版本的解析（即使 serving 是旧版）."""
    snap1, snap2, components, documents, doc_id = await _kb_with_two_revisions(tmp_path)
    service = ParseResultReadService(
        snapshots=components["snapshots"],
        storage_objects=components["storage_objects"],
        object_store=components["object_store"],
        segment_store=components["segment_store"],
        documents=documents,
    )

    result = await service.get_parse_result(
        domain="e2e", document_id=doc_id, view="latest_revision",
        serving={"document_snapshot_id": snap1, "build_id": "b1",
                 "source_content_revision": 1},
    )

    assert result["snapshot"]["id"] == snap2
    assert result["versioning"]["view"] == "latest_revision"


@pytest.mark.asyncio
async def test_legacy_call_signature_still_works(tmp_path):
    """不传 view/serving（既有调用方）：行为不变（latest 视图、无 versioning 冒充）."""
    snap1, snap2, components, documents, doc_id = await _kb_with_two_revisions(tmp_path)
    service = ParseResultReadService(
        snapshots=components["snapshots"],
        storage_objects=components["storage_objects"],
        object_store=components["object_store"],
        segment_store=components["segment_store"],
        documents=documents,
    )

    result = await service.get_parse_result(domain="e2e", document_id=doc_id)

    assert result["snapshot"]["id"] == snap2


# ---------------------------------------------------------------------------
# 路由：view 参数 + serving 上下文注入
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_defaults_to_current_serving(tmp_path):
    snap1, snap2, components, documents, doc_id = await _kb_with_two_revisions(tmp_path)
    kbdb = _KbDb(
        {doc_id: {"id": doc_id, "kb_id": KB, "domain": "e2e"}},
        serving={"document_snapshot_id": snap1, "build_id": "b1",
                 "source_content_revision": 1},
    )

    result = await document_parse_result(
        KB, doc_id, _request(components), USER, kbdb,
    )

    assert result["snapshot"]["id"] == snap1
    assert result["versioning"]["view"] == "current_serving"
    assert result["versioning"]["in_sync"] is False


@pytest.mark.asyncio
async def test_route_rejects_unknown_view(tmp_path):
    snap1, snap2, components, documents, doc_id = await _kb_with_two_revisions(tmp_path)
    kbdb = _KbDb(
        {doc_id: {"id": doc_id, "kb_id": KB, "domain": "e2e"}}, serving=None,
    )

    with pytest.raises(Exception) as excinfo:
        await document_parse_result(
            KB, doc_id, _request(components), "future_view", USER, kbdb,
        )
    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_route_latest_revision_explicit(tmp_path):
    snap1, snap2, components, documents, doc_id = await _kb_with_two_revisions(tmp_path)
    kbdb = _KbDb(
        {doc_id: {"id": doc_id, "kb_id": KB, "domain": "e2e"}},
        serving={"document_snapshot_id": snap1, "build_id": "b1",
                 "source_content_revision": 1},
    )

    result = await document_parse_result(
        KB, doc_id, _request(components), USER, kbdb, view="latest_revision",
    )

    assert result["snapshot"]["id"] == snap2
    assert result["versioning"]["view"] == "latest_revision"
