"""open_tools 跨版本迁移纯函数契约（29号 退役迁移 + 2026-08-31 合并改名 9→7）。"""


def test_non_legacy_set_returns_none():
    from knowledge_mining.mining.kb.services.mcp_access_service import (
        normalize_legacy_open_tools,
    )

    # 新七件套（或其子集）= 无需迁移
    assert normalize_legacy_open_tools(
        ["search_knowledge", "get_content"],
    ) is None
    assert normalize_legacy_open_tools([]) is None


def test_merge_rename_any_source_open_keeps_new_tool_open():
    from knowledge_mining.mining.kb.services.mcp_access_service import (
        normalize_legacy_open_tools,
    )

    # 九件套全集 → 七件套全集（两个读取源合一、两个罗列源合一）
    nine = [
        "search_knowledge", "get_evidence", "get_document",
        "inspect_knowledge", "navigate_structure", "query_structured_asset",
        "list_knowledge_bases", "list_documents", "upload_document",
    ]
    out = normalize_legacy_open_tools(nine)
    assert out is not None
    assert set(out) == {
        "search_knowledge", "get_content", "browse_knowledge",
        "inspect_knowledge", "navigate_structure",
        "query_structured_asset", "upload_document",
    }
    # 只开了 get_evidence（读取功能开启）→ get_content 开启
    out2 = normalize_legacy_open_tools(
        ["search_knowledge", "get_evidence"])
    assert out2 == ["search_knowledge", "get_content"]
    # 只开了 list_documents（罗列功能开启）→ browse_knowledge 开启
    out3 = normalize_legacy_open_tools(["list_documents"])
    assert out3 == ["browse_knowledge"]


def test_both_sources_disabled_keeps_merged_tool_disabled():
    from knowledge_mining.mining.kb.services.mcp_access_service import (
        normalize_legacy_open_tools,
    )

    # 读取两件都被显式关闭 → 合并后的 get_content 不开启（关闭语义优先）
    out = normalize_legacy_open_tools(
        ["search_knowledge", "get_segment_fulltext"])
    assert "get_content" not in out


def test_retired_names_are_dropped():
    from knowledge_mining.mining.kb.services.mcp_access_service import (
        normalize_legacy_open_tools,
    )

    legacy = [
        "search_knowledge", "list_knowledge_bases", "get_segment_fulltext",
        "upload_document",
    ]
    out = normalize_legacy_open_tools(legacy)
    assert out is not None
    assert "get_segment_fulltext" not in out
    assert out == ["search_knowledge", "browse_knowledge", "upload_document"]
