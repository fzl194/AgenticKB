"""Unit tests for ParserRouter（M3 WP6 初版, SRS §C05）.

用 stub BackendRegistry（ParserDescriptor 直接构造）驱动确定性路由表：

- 各 source_format 路由到 registry 中第一个 local 且 license_status="ok"
  的 backend（**不硬编码 parser_id**）；
- cloud / license 未过审（pending_review）/ unconfigured 的 backend 不会被
  选为 primary（SRS §C04 "未通过许可或不健康 backend 不会被 Router 选中"）；
- unknown 格式返回 primary=None + reason（不抛，调用方决定，SRS §4.4）；
- PDF 无文本层：primary 保留 + reason 标注 OCR 需求，fallback 清空
  （云端槽位预留，SRS §C05 / M3 范围外）。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.contracts.parser_adapter import (
    BackendRegistry,
    ParserDescriptor,
)
from knowledge_mining.mining.file_inspector.inspect import DocumentProfile
from knowledge_mining.mining.file_inspector.router import ParserRouter, RouteDecision

OOXML_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _descriptor(
    parser_id: str,
    *mimes: str,
    backend_kind: str = "local",
    license_status: str = "ok",
) -> ParserDescriptor:
    return ParserDescriptor(
        parser_id=parser_id,
        display_name=parser_id,
        version="1.0.0",
        supported_mimes=frozenset(mimes),
        backend_kind=backend_kind,
        license_status=license_status,
    )


def _profile(fmt: str, *, has_text_layer: bool | None = None) -> DocumentProfile:
    return DocumentProfile(
        detected_mime="application/octet-stream",
        source_format=fmt,
        has_text_layer=has_text_layer,
    )


def _full_registry() -> BackendRegistry:
    registry = BackendRegistry()
    registry.register(_descriptor("md_a", "text/markdown"))
    registry.register(_descriptor("txt_a", "text/plain"))
    registry.register(_descriptor("pdf_native", "application/pdf"))
    registry.register(_descriptor("docx_native", OOXML_DOCX_MIME))
    registry.register(_descriptor("html_native", "text/html"))
    return registry


@pytest.fixture
def router() -> ParserRouter:
    return ParserRouter(_full_registry())


# ---------------------------------------------------------------------------
# 基本路由
# ---------------------------------------------------------------------------


def test_markdown_routes_to_local_backend(router: ParserRouter) -> None:
    decision = router.plan(_profile("markdown"))
    assert decision.primary_parser_id == "md_a"
    assert "mime_markdown" in decision.reason_codes
    assert decision.route_name == "legacy_markdown"


def test_txt_routes_to_local_backend(router: ParserRouter) -> None:
    decision = router.plan(_profile("txt"))
    assert decision.primary_parser_id == "txt_a"
    assert decision.route_name == "legacy_txt"


def test_pdf_and_docx_route_to_native(router: ParserRouter) -> None:
    assert router.plan(_profile("pdf")).primary_parser_id == "pdf_native"
    assert router.plan(_profile("docx")).primary_parser_id == "docx_native"


def test_router_does_not_hardcode_parser_ids() -> None:
    # 换一个 parser_id 的 registry：路由结果跟随 descriptor 而非常量
    registry = BackendRegistry()
    registry.register(_descriptor("other_pdf", "application/pdf"))
    decision = ParserRouter(registry).plan(_profile("pdf"))
    assert decision.primary_parser_id == "other_pdf"


# ---------------------------------------------------------------------------
# 健康度 / 云端过滤（SRS §C04）
# ---------------------------------------------------------------------------


def test_unlicensed_local_backend_not_selected() -> None:
    registry = BackendRegistry()
    registry.register(
        _descriptor("docling", "application/pdf", license_status="pending_review")
    )
    decision = ParserRouter(registry).plan(_profile("pdf"))
    assert decision.primary_parser_id is None
    assert "no_local_backend" in decision.reason_codes


def test_cloud_backend_not_selected_as_local_primary() -> None:
    registry = BackendRegistry()
    registry.register(
        _descriptor("cloud_vlm", "application/pdf", backend_kind="cloud")
    )
    decision = ParserRouter(registry).plan(_profile("pdf"))
    assert decision.primary_parser_id is None
    assert "no_local_backend" in decision.reason_codes


def test_local_wins_over_cloud_even_when_cloud_registered_first() -> None:
    registry = BackendRegistry()
    registry.register(
        _descriptor("cloud_vlm", "application/pdf", backend_kind="cloud")
    )
    registry.register(_descriptor("pdf_native", "application/pdf"))
    decision = ParserRouter(registry).plan(_profile("pdf"))
    assert decision.primary_parser_id == "pdf_native"


# ---------------------------------------------------------------------------
# unknown / PDF 文本层
# ---------------------------------------------------------------------------


def test_unknown_format_returns_none_primary_with_reason(
    router: ParserRouter,
) -> None:
    decision = router.plan(_profile("unknown"))
    assert isinstance(decision, RouteDecision)
    assert decision.primary_parser_id is None
    assert decision.fallback_parser_ids == ()
    assert "unsupported_format" in decision.reason_codes


def test_pdf_without_text_layer_flags_ocr(router: ParserRouter) -> None:
    decision = router.plan(_profile("pdf", has_text_layer=False))
    # primary 保留（能力声明弱），fallback 清空并预留云端 OCR 槽位
    assert decision.primary_parser_id == "pdf_native"
    assert decision.fallback_parser_ids == ()
    assert "no_text_layer_needs_ocr" in decision.reason_codes
    assert "ocr_reserved_cloud" in decision.reason_codes


def test_pdf_with_text_layer_no_ocr_reason(router: ParserRouter) -> None:
    decision = router.plan(_profile("pdf", has_text_layer=True))
    assert "no_text_layer_needs_ocr" not in decision.reason_codes
    assert decision.fallback_parser_ids == ()


def test_decision_is_frozen(router: ParserRouter) -> None:
    decision = router.plan(_profile("txt"))
    with pytest.raises(Exception):
        decision.primary_parser_id = "hacked"  # type: ignore[misc]
