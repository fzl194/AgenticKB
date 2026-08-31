"""open_tools 跨版本迁移纯函数契约（29号 退役迁移 + 2026-08-31 两轮合并改名 9→7→3）。"""


def test_non_legacy_set_returns_none():
    from knowledge_mining.mining.kb.services.mcp_access_service import (
        normalize_legacy_open_tools,
    )

    # 新三件套（或其子集）= 无需迁移
    assert normalize_legacy_open_tools(
        ["search_knowledge", "get_knowledge"],
    ) is None
    assert normalize_legacy_open_tools([]) is None


def test_any_read_source_open_keeps_get_knowledge_open():
    from knowledge_mining.mining.kb.services.mcp_access_service import (
        normalize_legacy_open_tools,
    )

    # 旧九件套全集 → 三件套全集（全部读取源合一）
    nine = [
        "search_knowledge", "get_evidence", "get_document",
        "inspect_knowledge", "navigate_structure", "query_structured_asset",
        "list_knowledge_bases", "list_documents", "upload_document",
    ]
    out = normalize_legacy_open_tools(nine)
    assert out is not None
    assert set(out) == {"search_knowledge", "get_knowledge", "upload_document"}

    # 中间版本（七件套）同样收敛
    seven = [
        "search_knowledge", "get_content", "browse_knowledge",
        "inspect_knowledge", "navigate_structure",
        "query_structured_asset", "upload_document",
    ]
    out7 = normalize_legacy_open_tools(seven)
    assert set(out7) == {"search_knowledge", "get_knowledge", "upload_document"}

    # 只开任一读取源 → get_knowledge 开启
    for legacy in ("get_evidence", "get_document", "browse_knowledge",
                   "inspect_knowledge", "navigate_structure",
                   "query_structured_asset", "list_documents"):
        assert normalize_legacy_open_tools([legacy]) == ["get_knowledge"]


def test_all_read_sources_disabled_keeps_get_knowledge_disabled():
    from knowledge_mining.mining.kb.services.mcp_access_service import (
        normalize_legacy_open_tools,
    )

    # 全部读取源都被显式关闭 → get_knowledge 不开启（关闭语义优先）
    out = normalize_legacy_open_tools(
        ["search_knowledge", "get_segment_fulltext"])
    assert "get_knowledge" not in out
    assert out == ["search_knowledge"]


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
    assert out == ["search_knowledge", "get_knowledge", "upload_document"]
