"""58号§2.5：MCP 上传票据绑定目录（begin-upload 校验矩阵 + 消费只认票据）。

begin-upload 直调路由函数（假 kbdb，免 PG）：
- directory 形状校验（反斜杠/绝对路径/空段/点段/超长 → 422）
- directory 必须已存在（不自动创建 → 422）
- replace（document_id）与 directory 互斥 → 400
- 票据写入 directory；upload-direct 消费时只认票据绑定目录
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from knowledge_mining.mining.kb.routes import mcp_tools

pytestmark = pytest.mark.asyncio

KEY = {"user_id": "u-1", "domain": "generic", "status": "active"}
DOC = {"id": "doc-1", "kb_id": "kb-1", "document_name": "设备手册.pdf",
       "content_revision": 3}


class _FakeKbDB:
    def __init__(self, *, folders: dict[str, bool] | None = None):
        self._folders = folders if folders is not None else {"产品文档": True}

    async def get_user_by_username(self, username):
        return {"id": "u-1", "username": username} if username else None

    async def get_mcp_key(self, key_id):
        return KEY if key_id else None

    async def key_open_kb_ids(self, key_id):
        return ["kb-1"]

    async def is_visible(self, *, kb_id, user_id):
        return kb_id == "kb-1"

    async def can_write(self, *, kb_id, user_id):
        return kb_id == "kb-1"

    async def get_kb(self, kb_id):
        return {"id": kb_id, "domain": "generic"} if kb_id == "kb-1" else None

    async def get_document_identity(self, document_id):
        return DOC if document_id == DOC["id"] else None

    async def find_folder_by_path(self, kb_id, path):
        if kb_id == "kb-1" and self._folders.get(path):
            return {"id": f"f-{path}", "kb_id": kb_id, "path": path}
        return None


def _body(**kw):
    base = {"username": "alice", "key_id": "k-1", "kb_id": "kb-1",
            "filename": "手册.pdf"}
    base.update(kw)
    return base


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


@pytest.fixture
def _clean_tickets():
    mcp_tools._TICKETS._tickets.clear()
    yield
    mcp_tools._TICKETS._tickets.clear()


async def test_begin_upload_binds_directory_into_ticket(_clean_tickets):
    out = await mcp_tools.begin_upload(
        _body(directory="产品文档"), _request(), kbdb=_FakeKbDB(),
    )
    entry = mcp_tools._TICKETS.peek(out["ticket"])
    assert entry["directory"] == "产品文档"


async def test_begin_upload_without_directory_defaults_root(_clean_tickets):
    out = await mcp_tools.begin_upload(_body(), _request(), kbdb=_FakeKbDB())
    entry = mcp_tools._TICKETS.peek(out["ticket"])
    assert entry["directory"] == ""


async def test_begin_upload_directory_must_exist_no_autocreate(_clean_tickets):
    """58号：不存在目录 422（不自动创建——防拼写错误制造幽灵目录）。"""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await mcp_tools.begin_upload(
            _body(directory="不存在的目录"), _request(), kbdb=_FakeKbDB(),
        )
    assert ei.value.status_code == 422
    assert "不自动创建" in ei.value.detail


@pytest.mark.parametrize("bad", [
    "a\\b",            # 反斜杠
    "/绝对路径",        # 绝对路径
    "a//b",            # 空段
    "a/",              # 尾斜杠=空段
    "a/./b",           # 点段
    "a/../b",          # 上跳段
    "x" * 513,         # 超长
])
async def test_begin_upload_rejects_directory_shapes(bad, _clean_tickets):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await mcp_tools.begin_upload(
            _body(directory=bad), _request(), kbdb=_FakeKbDB(),
        )
    assert ei.value.status_code == 422


async def test_begin_upload_replace_rejects_directory(_clean_tickets):
    """replace 禁止 directory：替换保持原目录不变。"""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await mcp_tools.begin_upload(
            _body(directory="产品文档", document_id="doc-1", expected_revision=3),
            _request(), kbdb=_FakeKbDB(),
        )
    assert ei.value.status_code == 400


async def test_upload_direct_consumes_only_ticket_directory(_clean_tickets):
    """消费只认票据绑定目录：PUT 无任何参数可指定/篡改目录（presigned 模型）。"""
    from knowledge_mining.mining.kb.routes import mcp_tools as mt

    out = await mt.begin_upload(
        _body(directory="产品文档"), _request(), kbdb=_FakeKbDB(),
    )
    ticket = out["ticket"]

    captured: dict = {}

    class _FakeSvc:
        async def intake_upload(self, **kw):
            captured.update(kw)
            return {"kind": "file",
                    "document": {"id": "doc-9", "document_name": "手册.pdf"}}

    async def _fake_auto_mine(**kw):
        return {"auto_mined": False, "reason": "test"}

    async def _stream():
        yield b"bytes"

    monkey = pytest.MonkeyPatch()
    monkey.setattr(mt, "get_internal_verify_secret", lambda: "test-secret")
    monkey.setattr(mt.auto_mine, "enqueue_auto_mining", _fake_auto_mine)
    try:
        # upload_direct 从 request.stream() 读字节——构造带内部密钥头的假 request
        req = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace()),
            headers={"X-Internal-Auth": "test-secret"},
            stream=lambda: _stream(),
        )
        result = await mt.upload_direct(ticket, req, kbdb=_FakeKbDB(),
                                        doc_svc=_FakeSvc())
    finally:
        monkey.undo()
    assert result["kind"] == "file"
    assert captured["directory_path"] == "产品文档"
