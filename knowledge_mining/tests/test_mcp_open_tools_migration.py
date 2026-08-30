"""29号（未完成 E）：历史 open_tools 迁移纯函数契约."""


def test_non_legacy_set_returns_none():
    from knowledge_mining.mining.kb.services.mcp_access_service import (
        normalize_legacy_open_tools,
    )

    assert normalize_legacy_open_tools(
        ["search_knowledge", "get_evidence"],
    ) is None
    assert normalize_legacy_open_tools([]) is None


def test_legacy_set_drops_retired_and_adds_new_four():
    from knowledge_mining.mining.kb.services.mcp_access_service import (
        normalize_legacy_open_tools,
    )

    legacy = [
        "search_knowledge", "list_knowledge_bases", "list_documents",
        "get_document", "get_segment_fulltext", "upload_document",
    ]
    out = normalize_legacy_open_tools(legacy)
    assert out is not None
    assert "get_segment_fulltext" not in out
    # 新四结构工具补齐（用户从未见过，不存在误开启）
    for t in ("get_evidence", "inspect_knowledge", "navigate_structure",
              "query_structured_asset"):
        assert t in out
    # 显式关闭的既有工具保持关闭
    assert "upload_document" in out


def test_explicitly_disabled_tool_stays_disabled():
    from knowledge_mining.mining.kb.services.mcp_access_service import (
        normalize_legacy_open_tools,
    )

    out = normalize_legacy_open_tools(
        ["search_knowledge", "get_segment_fulltext"],
    )
    assert out is not None
    assert "list_documents" not in out  # 用户本来就关着
    assert "search_knowledge" in out
