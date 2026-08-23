"""KB「结构化数据」路由测试：真实读服务 + 显式组合根（无 PG/MinIO 依赖）.

覆盖 ``GET /api/kb/{kb_id}/documents/{document_id}/parse-result``：
  - 成员可见 → 200 返回 outline/tables/segments（前端页签同源形状）
  - 文档不属于该 KB / 不可见 → 404（不泄露存在性）
  - 当前内容版本无解析结果 → 404
  - IR 制品缺失（对象存储事故）→ 409
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from knowledge_mining.mining.contracts.storage.errors import StorageObjectMissing
from knowledge_mining.mining.kb.routes.documents import document_parse_result


class _KbDb:
    def __init__(self, documents: dict[str, dict]) -> None:
        self._documents = documents

    async def is_visible(self, *, kb_id: str, user_id: str) -> bool:
        return True

    async def get_document_identity(self, document_id: str) -> dict | None:
        return self._documents.get(document_id)


class _MissingIrSnapshots:
    """代理快照仓储：读到快照后让 IR 取数抛 StorageObjectMissing."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def latest_for_document(self, *args, **kwargs):
        found = await self._inner.latest_for_document(*args, **kwargs)
        if found is None:
            return None
        raise StorageObjectMissing("parse IR object vanished")


async def _parsed_kb(tmp_path, fmt: str = "md"):
    """走新链真组件：解析+切片一个 md 样例，返回路由所需全部部件."""
    from knowledge_mining.tests.new_chain.test_multi_format_pipeline import (
        MIME,
        _sample_bytes,
        _seed_document,
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

    store = FakeObjectStore(str(tmp_path / "objects"))
    objects = MemoryStorageObjectRepository()
    documents = MemoryDocumentCurrentContentRepository()
    snapshots = MemorySnapshotRepository()
    segment_store = MemorySegmentStore()
    services = build_new_chain_services(
        bucket_prefix="route-", object_store=store, storage_objects=objects,
        documents=documents, snapshots=snapshots,
        segment_store=segment_store,
    )
    raw = await _seed_document(
        store, objects, documents, fmt=fmt, data=_sample_bytes(fmt),
    )
    outcome = services.document_parse_service.parse_document(
        raw, params={}, domain="e2e", run_document_id="rd-route",
    )
    assert outcome.snapshot_id
    services.segment_compile_service.compile_for_snapshot(
        snapshot_id=outcome.snapshot_id,
        parse_ir_storage_object_id=outcome.parse_ir_storage_object_id,
        params={"tableView": "rows"},
    )
    components = {
        "snapshots": snapshots, "storage_objects": objects,
        "object_store": store, "segment_store": segment_store,
    }
    return raw, components


def _request(components) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        parse_result_components=components,
    )))


USER = {"id": "alice"}
KB = "kb-route"


@pytest.mark.asyncio
async def test_member_gets_structured_data(tmp_path):
    raw, components = await _parsed_kb(tmp_path)
    kbdb = _KbDb({raw.document_id: {
        "id": raw.document_id, "kb_id": KB, "domain": "e2e",
    }})

    result = await document_parse_result(
        KB, raw.document_id, _request(components), USER, kbdb,
    )

    assert result["segments"]["count"] >= 1
    assert result["tables"], "table grid missing"
    assert result["outline"]
    assert result["snapshot"]["quality_status"] in ("PASS", "WARN")


@pytest.mark.asyncio
async def test_document_of_another_kb_is_404(tmp_path):
    raw, components = await _parsed_kb(tmp_path)
    kbdb = _KbDb({raw.document_id: {
        "id": raw.document_id, "kb_id": "kb-other", "domain": "e2e",
    }})

    with pytest.raises(Exception) as excinfo:
        await document_parse_result(
            KB, raw.document_id, _request(components), USER, kbdb,
        )
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_unparsed_document_is_404(tmp_path):
    raw, components = await _parsed_kb(tmp_path)
    kbdb = _KbDb({"doc-unparsed": {
        "id": "doc-unparsed", "kb_id": KB, "domain": "e2e",
    }})

    with pytest.raises(Exception) as excinfo:
        await document_parse_result(
            KB, "doc-unparsed", _request(components), USER, kbdb,
        )
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_missing_parse_ir_is_409(tmp_path):
    raw, components = await _parsed_kb(tmp_path)
    broken = dict(components)
    broken["snapshots"] = _MissingIrSnapshots(components["snapshots"])
    kbdb = _KbDb({raw.document_id: {
        "id": raw.document_id, "kb_id": KB, "domain": "e2e",
    }})

    with pytest.raises(Exception) as excinfo:
        await document_parse_result(
            KB, raw.document_id, _request(broken), USER, kbdb,
        )
    assert excinfo.value.status_code == 409
