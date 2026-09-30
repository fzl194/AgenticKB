"""57号工作线②：网页上传/替换成功后自动入队挖掘（直调路由函数，免 PG）。

钉住三点：
- 上传（单文件/归档/异步归档）与替换成功后各恰好入队一次，参数带对 kb/user；
- 响应只增不改（auto_mined 必有，run_id/reason 按需；原字段原样保留）；
- 失败路径（409 同名冲突等）不入队；kb 查不到 → auto_mined=False 不抛。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from knowledge_mining.mining.kb.routes import documents as routes
from knowledge_mining.mining.kb.services import auto_mine

pytestmark = pytest.mark.asyncio

USER = {"id": "u-1", "username": "alice"}


class _FakeUploadFile:
    def __init__(self, filename: str):
        self.filename = filename
        self.content_type = "application/octet-stream"

    async def read(self, _n: int) -> bytes:
        return b""


class _FakeSvc:
    def __init__(self, result):
        self._result = result
        self.intake_calls: list[dict] = []
        self.replace_calls: list[dict] = []

    async def intake_upload(self, **kw):
        self.intake_calls.append(kw)
        return self._result

    async def replace_content(self, **kw):
        self.replace_calls.append(kw)
        return {"id": "d-1", "content_revision": kw["expected_revision"] + 1}


class _FakeKbDB:
    def __init__(self, kb):
        self._kb = kb

    async def get_kb(self, _kb_id):
        return self._kb


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


@pytest.fixture
def enqueued(monkeypatch):
    calls: list[dict] = []

    async def _fake(*, app_state, kbdb, kb, user_id, username):
        calls.append({"kb_id": kb["id"], "user_id": user_id, "username": username})
        return {"auto_mined": True, "run_id": "run-9", "merged": False, "detail": "已入队"}

    monkeypatch.setattr(auto_mine, "enqueue_auto_mining", _fake)
    return calls


async def test_upload_file_enqueues_auto_mine_and_adds_fields(enqueued):
    svc = _FakeSvc({"kind": "file", "document": {"id": "d-1", "document_name": "a.pdf"}})
    out = await routes.upload_document(
        kb_id="kb-1", request=_request(), file=_FakeUploadFile("a.pdf"),
        directory=None, document_type=None, user=USER, svc=svc,
        kbdb=_FakeKbDB({"id": "kb-1"}),
    )
    assert out == {"id": "d-1", "document_name": "a.pdf",
                   "auto_mined": True, "run_id": "run-9"}
    assert enqueued == [{"kb_id": "kb-1", "user_id": "u-1", "username": "alice"}]


async def test_upload_archive_task_response_carries_fields(enqueued):
    svc = _FakeSvc({"kind": "archive_task", "archive_task_id": "t-1", "status": "processing"})
    out = await routes.upload_document(
        kb_id="kb-1", request=_request(), file=_FakeUploadFile("big.zip"),
        directory=None, document_type=None, user=USER, svc=svc,
        kbdb=_FakeKbDB({"id": "kb-1"}),
    )
    assert out.status_code == 202
    import json
    body = json.loads(out.body)
    assert body["archive_task_id"] == "t-1"
    assert body["auto_mined"] is True and body["run_id"] == "run-9"
    assert len(enqueued) == 1


async def test_replace_content_enqueues_auto_mine(enqueued):
    svc = _FakeSvc(None)
    out = await routes.replace_document_content(
        kb_id="kb-1", document_id="d-1", request=_request(),
        file=_FakeUploadFile("a.pdf"), expected_revision=3,
        user=USER, svc=svc, kbdb=_FakeKbDB({"id": "kb-1"}),
    )
    assert out["content_revision"] == 4
    assert out["auto_mined"] is True and out["run_id"] == "run-9"
    assert len(enqueued) == 1


async def test_duplicate_upload_409_does_not_enqueue(enqueued):
    from fastapi import HTTPException

    svc = _FakeSvc({"kind": "file", "document": {}})
    # 触发 Duplicate 分支：用 一个总是抛 Duplicate 的 svc
    from knowledge_mining.mining.kb.services.kb_service import Duplicate

    async def _dup(**kw):
        raise Duplicate("同目录已存在该文件")

    svc.intake_upload = _dup  # type: ignore[method-assign]
    with pytest.raises(HTTPException) as ei:
        await routes.upload_document(
            kb_id="kb-1", request=_request(), file=_FakeUploadFile("a.pdf"),
            directory=None, document_type=None, user=USER, svc=svc,
            kbdb=_FakeKbDB({"id": "kb-1"}),
        )
    assert ei.value.status_code == 409
    assert enqueued == []


async def test_kb_missing_degrades_without_raise(monkeypatch):
    async def _boom(**kw):  # 不应被调到（kb None 短路）
        raise AssertionError("enqueue must not be called")

    monkeypatch.setattr(auto_mine, "enqueue_auto_mining", _boom)
    svc = _FakeSvc({"kind": "file", "document": {"id": "d-1"}})
    out = await routes.upload_document(
        kb_id="kb-1", request=_request(), file=_FakeUploadFile("a.pdf"),
        directory=None, document_type=None, user=USER, svc=svc,
        kbdb=_FakeKbDB(None),
    )
    assert out["auto_mined"] is False and out["reason"] == "internal"
