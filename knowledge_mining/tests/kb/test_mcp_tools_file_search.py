"""57 号：MCP 文件清单/搜索条目形状（纯函数，不需要 PostgreSQL）。

跨库搜索/鉴权行为（kb_id 可选、query 必填、422 矩阵）走真库用例——
见 test_mcp_tools_file_search_pg.py。
"""
from __future__ import annotations

from knowledge_mining.mining.kb.routes.mcp_tools import _document_list_item


def _own_doc() -> dict:
    return {
        "id": "doc-1", "document_name": "设备X手册.pdf", "status": "mined",
        "file_size": 1024, "modified_at": "2026-09-30T01:02:03+00:00",
        "created_at": "2026-09-01T00:00:00+00:00",
        "content_revision": 3, "directory_path": "产品文档/手册",
    }


def test_own_document_item_shape():
    item = _document_list_item(_own_doc())
    assert item == {
        "id": "doc-1",
        "name": "设备X手册.pdf",
        "status": "mined",
        "file_size": 1024,
        "modified_at": "2026-09-30T01:02:03+00:00",
        "referenced": False,
        "content_revision": 3,
        "directory_path": "产品文档/手册",
    }


def test_referenced_item_shape():
    d = {
        "id": "doc-r", "document_name": "引用文档.md", "file_size": 10,
        "referenced_at": "2026-09-29T08:00:00+00:00",
        "directory_path": "外部/引用",
    }
    item = _document_list_item(d, referenced=True)
    assert item["status"] == "referenced"
    assert item["referenced"] is True
    assert item["content_revision"] is None  # 引用文档无替换语义
    assert item["modified_at"] == "2026-09-29T08:00:00+00:00"
    assert "kb" not in item


def test_cross_kb_item_carries_kb_name():
    item = _document_list_item(_own_doc(), kb_name="设备库")
    assert item["kb"] == "设备库"


def test_missing_fields_degrade_to_none_not_crash():
    item = _document_list_item({"id": "doc-2", "document_name": "x.md"})
    assert item["status"] is None
    assert item["content_revision"] is None
    assert item["modified_at"] == ""


def test_limit_bounds_are_clamped_and_typed():
    """codex P2：负数/非整数 limit 不得造成 SQL 500 或绕过跨库上限（形状守卫：
    路由源码必须含钳制逻辑——行为级验证在 PG 门禁的 test_mcp_tools_keys）。"""
    import inspect
    from knowledge_mining.mining.kb.routes import mcp_tools

    src = inspect.getsource(mcp_tools.list_documents)
    assert "max(1, min(int(body.get(\"limit\") or 50), 200))" in src
    assert "422" in src
