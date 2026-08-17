"""默认 backend registry 构建（M2 WP4 + M3 WP6 云端槽位, SRS §C04）.

注册内容：

- M2 legacy parser descriptor（Markdown / TXT，license_status="ok"，
  可被 Router 选中）；
- M3 占位槽位（``include_cloud_slots=True`` 时默认注册）：
  * ``docling`` —— local layout/table/OCR 候选，许可**待审**
    （``license_status="pending_review"``）。按 SRS §C04 "未通过许可
    或不健康的 backend 不会被 Router 选中"，注册了也不会成为 primary；
    引入真实实现并通过 WP13 许可审查后才参与路由。
  * ``cloud_vlm`` —— 云端 VLM/OCR 槽位（``backend_kind="cloud"``，
    ``license_status="unconfigured"``）。``note`` 字段注明用户将来
    配置云端模型的位置；M3 只做槽位预留，不做任何网络调用。

选择语义保持 M2 不变：``BackendRegistry.select_for`` 首个声明支持该
MIME 的 descriptor 胜出（含占位符——判断"是否可路由"请用 ParserRouter
的 local+license 过滤，SRS §C05）。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.parser_adapter import (
    BackendRegistry,
    ParserDescriptor,
)
from knowledge_mining.mining.parse_adapters.legacy_markdown import (
    LegacyMarkdownParser,
)
from knowledge_mining.mining.parse_adapters.legacy_txt import (
    LegacyPlainTextParser,
)

#: Docling 占位槽位声明覆盖的结构化格式（SRS §C03 解析覆盖面 M3 目标）。
_DOCLING_MIMES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/html",
    }
)

#: 云端 VLM 槽位按预留声明：扫描 PDF + 图像（模型接入后由配置收敛）。
_CLOUD_VLM_MIMES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
    }
)


def _docling_slot() -> ParserDescriptor:
    """Docling 占位 descriptor（许可待审，Router 不会选中）."""
    return ParserDescriptor(
        parser_id="docling",
        display_name="Docling (placeholder, license pending)",
        version="0.0.0",
        supported_mimes=_DOCLING_MIMES,
        backend_kind="local",
        license_status="pending_review",
        capabilities=frozenset({"layout", "table", "ocr"}),
        note=(
            "占位：真实 Docling adapter 待接入并通过 WP13 许可审查；"
            "在此之前 Router 不会将其选为 primary（SRS §C04）。"
        ),
    )


def _cloud_vlm_slot() -> ParserDescriptor:
    """云端 VLM/OCR 占位 descriptor（未配置，用户将来接入模型）."""
    return ParserDescriptor(
        parser_id="cloud_vlm",
        display_name="Cloud VLM / OCR (unconfigured slot)",
        version="0.0.0",
        supported_mimes=_CLOUD_VLM_MIMES,
        backend_kind="cloud",
        license_status="unconfigured",
        capabilities=frozenset({"ocr", "vision", "table"}),
        note=(
            "云端槽位预留：用户将来在系统配置中填写云端模型 provider / "
            "endpoint / api key 后启用（SRS §C05 ocr_reserved_cloud）；"
            "M3 不做任何网络调用。"
        ),
    )


def build_default_registry(include_cloud_slots: bool = True) -> BackendRegistry:
    """注册全部已实现 parser（+ 可选占位槽位）并返回 registry（SRS §C04）.

    M3.5 起 native 适配器（docx/xlsx/pptx/html/pdf，全部 license ok）一并
    注册，Router 可按画像路由到它们；实现类清单来自 ``factory``（单一
    事实源，避免 registry 与工厂漂移）。
    """
    from knowledge_mining.mining.parse_adapters.factory import iter_native_parsers

    registry = BackendRegistry()
    for parser in iter_native_parsers():
        registry.register(parser.descriptor)
    if include_cloud_slots:
        registry.register(_docling_slot())
        registry.register(_cloud_vlm_slot())
    return registry


__all__ = ["build_default_registry"]
