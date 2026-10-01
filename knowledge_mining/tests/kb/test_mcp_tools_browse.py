"""58号§3：MCP 目录逐层浏览（browse-directory 内部端点，假 kbdb 免 PG）。

契约：child_directories 只列直属子目录（空目录可见）；documents 只列直属文件
（directory 精确匹配，不递归混入孙级）；referenced 不入目录树；目录不存在 422
（不自动创建）；非法路径 422；limit/offset 分页。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from knowledge_mining.mining.kb.routes import mcp_tools

pytestmark = pytest.mark.asyncio

KEY = {"user_id": "u-1", "domain": "generic", "status": "active"}

FOLDERS = [
    {"id": "f-1", "kb_id": "kb-1", "parent_id": None, "name": "产品文档", "path": "产品文档"},
    {"id": "f-2", "kb_id": "kb-1", "parent_id": "f-1", "name": "手册", "path": "产品文档/手册"},
    {"id": "f-3", "kb_id": "kb-1", "parent_id": "f-2", "name": "交换机", "path": "产品文档/手册/交换机"},
    {"id": "f-4", "kb_id": "kb-1", "parent_id": None, "name": "空目录", "path": "空目录"},
]
DOCS = [
    {"id": "d-root", "document_name": "根文件.md", "status": "mined",
     "content_revision": 1, "directory_path": "", "file_size": 1,
     "modified_at": "2026-09-30T00:00:00+00:00", "created_at": "2026-09-30T00:00:00+00:00"},
    {"id": "d-1", "document_name": "手册.pdf", "status": "uploaded",
     "content_revision": 4, "directory_path": "产品文档", "file_size": 2,
     "modified_at": "2026-09-30T01:00:00+00:00", "created_at": "2026-09-29T00:00:00+00:00"},
    {"id": "d-2", "document_name": "孙级文件.md", "status": "mined",
     "content_revision": 1, "directory_path": "产品文档/手册/交换机", "file_size": 3,
     "modified_at": "2026-09-30T02:00:00+00:00", "created_at": "2026-09-29T00:00:00+00:00"},
]


class _FakeKbDB:
    async def get_user_by_username(self, username):
        return {"id": "u-1", "username": username} if username else None

    async def get_mcp_key(self, key_id):
        return KEY if key_id else None

    async def key_open_kb_ids(self, key_id):
        return ["kb-1"]

    async def is_visible(self, *, kb_id, user_id):
        return kb_id == "kb-1"

    async def get_kb(self, kb_id):
        return {"id": "kb-1", "name": "设备库", "domain": "generic"} if kb_id == "kb-1" else None

    async def list_folders(self, kb_id):
        return [dict(f) for f in FOLDERS]

    async def find_folder_by_path(self, kb_id, path):
        return next((dict(f) for f in FOLDERS if f["path"] == path), None)

    async def list_documents_in_kb(self, *, kb_id, directory=None, limit=200,
                                   offset=0, **kw):
        assert kw == {}, "浏览视图不带搜索过滤参数（搜索走 file_query/directory_prefix）"
        rows = [dict(d) for d in DOCS if (d["directory_path"] or "") == directory]
        return rows[offset:offset + limit]


def _body(**kw):
    base = {"username": "alice", "key_id": "k-1", "kb_id": "kb-1", "directory": ""}
    base.update(kw)
    return base


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


async def test_browse_root_lists_direct_children_only():
    out = await mcp_tools.browse_directory(_body(directory=""), _request(),
                                           kbdb=_FakeKbDB())
    assert out["view"] == "directory"
    assert out["kb"] == "设备库"
    assert out["current_directory"] == ""
    assert out["parent_directory"] is None  # 根没有上层
    assert out["child_directories"] == [
        {"name": "产品文档", "path": "产品文档"},
        {"name": "空目录", "path": "空目录"},  # 空目录可见（58号§3.3）
    ]
    assert [d["document_id"] for d in out["documents"]] == ["d-root"]


async def test_browse_subdirectory_direct_children_only():
    out = await mcp_tools.browse_directory(
        _body(directory="产品文档/手册"), _request(), kbdb=_FakeKbDB())
    assert out["current_directory"] == "产品文档/手册"
    assert out["parent_directory"] == "产品文档"
    assert out["child_directories"] == [
        {"name": "交换机", "path": "产品文档/手册/交换机"}]
    # 直属文件为空，孙级文件不递归混入
    assert out["documents"] == []


async def test_browse_dir_with_docs_and_children():
    out = await mcp_tools.browse_directory(
        _body(directory="产品文档"), _request(), kbdb=_FakeKbDB())
    assert out["child_directories"] == [{"name": "手册", "path": "产品文档/手册"}]
    assert [d["document_id"] for d in out["documents"]] == ["d-1"]
    assert out["documents"][0]["content_revision"] == 4
    assert out["documents"][0]["referenced"] is False
    assert out["parent_directory"] == ""  # 顶层目录的上层=根（根的上层才是 None）


async def test_browse_missing_directory_422():
    """目录不存在 → 422（不自动创建，防幽灵目录；指引先浏览确认写法）。"""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await mcp_tools.browse_directory(
            _body(directory="不存在/的目录"), _request(), kbdb=_FakeKbDB())
    assert ei.value.status_code == 422


@pytest.mark.parametrize("bad", ["a\\b", "/绝对", "a//b", "a/./b", "a/../b", "x" * 513])
async def test_browse_rejects_directory_shapes(bad):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await mcp_tools.browse_directory(
            _body(directory=bad), _request(), kbdb=_FakeKbDB())
    assert ei.value.status_code == 422


async def test_browse_paginates_documents():
    out = await mcp_tools.browse_directory(
        _body(directory="", limit=200, offset=0), _request(), kbdb=_FakeKbDB())
    assert len(out["documents"]) == 1


async def test_browse_kb_not_open_404():
    """钥匙开放集之外的库：404 防探测（与 list-documents 同口径）。"""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await mcp_tools.browse_directory(
            _body(kb_id="kb-2", directory=""), _request(), kbdb=_FakeKbDB())
    assert ei.value.status_code == 404
