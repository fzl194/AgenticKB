"""Unit tests for 默认 registry 云端/Docling 槽位（M3 WP6, SRS §C04）.

覆盖：
- ``build_default_registry(include_cloud_slots=True)``（默认）在 legacy
  MD/TXT 之外注册两个**占位** descriptor：``docling``（local，许可待审）
  与 ``cloud_vlm``（cloud，未配置）；
- 占位 backend 因 ``license_status != "ok"`` 不会被 ParserRouter 选中
  （SRS §C04 "未通过许可或不健康 backend 不会被 Router 选中"）；
- ``include_cloud_slots=False`` 回退到 M2 的纯 legacy 组合；
- 契约最小演进：``ParserDescriptor.note`` 可选字段（默认空串）。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.parser_adapter import (
    ParserDescriptor,
)
from knowledge_mining.mining.file_inspector.inspect import DocumentProfile
from knowledge_mining.mining.file_inspector.router import ParserRouter
from knowledge_mining.mining.parse_adapters.registry import (
    build_default_registry,
)

_OOXML_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _ids(registry) -> set[str]:
    return {d.parser_id for d in registry.all()}


def test_descriptor_has_optional_note_field() -> None:
    descriptor = ParserDescriptor(
        parser_id="x",
        display_name="x",
        version="1.0.0",
        supported_mimes=frozenset({"text/plain"}),
    )
    assert descriptor.note == ""  # 默认值，不破坏 M2 既有构造调用


def test_default_registry_includes_cloud_slots() -> None:
    registry = build_default_registry()
    ids = _ids(registry)
    assert {"legacy_markdown", "legacy_txt", "docling", "cloud_vlm"} <= ids


def test_docling_slot_is_local_pending_license() -> None:
    registry = build_default_registry()
    docling = registry.get("docling")
    assert docling is not None
    assert docling.backend_kind == "local"
    assert docling.license_status == "pending_review"
    # 能力声明：layout / table / OCR（SRS §C04 能力矩阵 M3 预留）
    assert {"layout", "table", "ocr"} <= set(docling.capabilities)
    # 声明覆盖结构化格式但不含 M2 legacy 文本格式
    assert docling.supports("application/pdf")
    assert docling.supports(_OOXML_DOCX_MIME)
    assert not docling.supports("text/markdown")


def test_cloud_vlm_slot_is_cloud_unconfigured() -> None:
    registry = build_default_registry()
    vlm = registry.get("cloud_vlm")
    assert vlm is not None
    assert vlm.backend_kind == "cloud"
    assert vlm.license_status == "unconfigured"
    # 云端槽位注明用户将来配置模型的位置（note 元数据说明）
    assert vlm.note


def test_include_cloud_slots_false_restores_implemented_set() -> None:
    """M3.5：不含槽位时 registry = 全部已实现 parser（M2 两个 + M3 五个）."""
    registry = build_default_registry(include_cloud_slots=False)
    assert _ids(registry) == {
        "legacy_markdown", "legacy_txt",
        "native_docx", "native_xlsx", "native_pptx", "native_html", "native_pdf",
        "pdf_text_layer",
    }


def test_placeholder_slots_never_routed_as_primary() -> None:
    # SRS §C04：未过审 / 未配置 backend 不被 Router 选中
    registry = build_default_registry()
    router = ParserRouter(registry)
    pdf_profile = DocumentProfile(
        detected_mime="application/pdf",
        source_format="pdf",
        has_text_layer=True,
    )
    # M3.5 起 native_pdf 已实现且 license ok：PDF 路由到 native_pdf；
    # 占位槽位（docling/cloud_vlm）因 license != "ok" 不会被选中。
    decision = router.plan(pdf_profile)
    assert decision.primary_parser_id == "native_pdf"
    assert decision.primary_parser_id not in ("docling", "cloud_vlm")

    md_profile = DocumentProfile(
        detected_mime="text/markdown", source_format="markdown"
    )
    # legacy 文本格式不受占位槽位影响，照常路由
    assert router.plan(md_profile).primary_parser_id == "legacy_markdown"


def test_registry_includes_all_native_parsers() -> None:
    """M3.5：默认 registry 注册全部已实现 native 适配器并可被路由选中."""
    registry = build_default_registry()
    for pid in (
        "native_docx", "native_xlsx", "native_pptx", "native_html", "native_pdf",
    ):
        descriptor = registry.get(pid)
        assert descriptor is not None, f"{pid} missing from default registry"
        assert descriptor.license_status == "ok", pid
        assert descriptor.backend_kind == "local", pid
        assert descriptor.parser_fingerprint, pid


def test_router_plans_native_for_office_formats() -> None:
    """Router 经 registry 把 Office/PDF/HTML 路由到 native adapter."""
    from knowledge_mining.mining.file_inspector.inspect import DocumentProfile
    from knowledge_mining.mining.file_inspector.router import ParserRouter

    router = ParserRouter(build_default_registry())
    for fmt, pid in (
        ("docx", "native_docx"),
        ("xlsx", "native_xlsx"),
        ("pptx", "native_pptx"),
        ("html", "native_html"),
        ("pdf", "native_pdf"),
    ):
        decision = router.plan(DocumentProfile(
            detected_mime="application/octet-stream", source_format=fmt,
        ))
        assert decision.primary_parser_id == pid, fmt


def test_factory_resolves_pipeline_for_all_native_ids() -> None:
    """工厂按 parser_id 解析成对 (parser, normalizer)。"""
    from knowledge_mining.mining.parse_adapters.factory import resolve_pipeline

    for pid in (
        "legacy_markdown", "legacy_txt", "native_docx", "native_xlsx",
        "native_pptx", "native_html", "native_pdf", "pdf_text_layer",
    ):
        pair = resolve_pipeline(pid)
        assert pair is not None, pid
        parser, normalizer = pair
        assert parser.descriptor.parser_id == pid
        assert callable(normalizer.normalize)
    assert resolve_pipeline("docling") is None  # 占位无实现


def test_pdf_router_and_production_plan_keep_native_then_text_layer_order() -> None:
    """P02：同一 registry 顺序必须同时约束 Router 与生产 ParsePlan。"""
    from types import SimpleNamespace
    from knowledge_mining.mining.workflow.new_chain_services import DocumentParseFacade

    decision = ParserRouter(build_default_registry()).plan(DocumentProfile(
        detected_mime="application/pdf", source_format="pdf", has_text_layer=True,
    ))
    plan = DocumentParseFacade.default_plan_factory(
        SimpleNamespace(mime="application/pdf"), {}
    )
    assert decision.primary_parser_id == "native_pdf"
    assert decision.fallback_parser_ids == ("pdf_text_layer",)
    assert plan.backend_chain() == ("native_pdf", "pdf_text_layer")


def test_pdf_text_layer_pipeline_parses_a_real_pdf() -> None:
    """P02：备用后端必须能实际解析并规范化，而非只存在 descriptor。"""
    from knowledge_mining.mining.parse_adapters.factory import resolve_pipeline
    from knowledge_mining.tests.parse_adapters.test_cross_format_contract import (
        _pdf_with_table,
    )

    parser, normalizer = resolve_pipeline("pdf_text_layer")
    artifact = parser.parse(_pdf_with_table(), mime="application/pdf")
    document = normalizer.normalize(artifact, source_raw_hash="a" * 64)
    assert artifact.blocks
    assert document.elements
