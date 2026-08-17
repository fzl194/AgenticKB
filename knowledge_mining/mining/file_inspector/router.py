"""Parser Router 初版（M3 WP6, SRS §C05）.

基于 :class:`~knowledge_mining.mining.file_inspector.inspect.DocumentProfile`
与 Backend Registry（SRS §C04）的**确定性规则表**（M3 初版：无打分、无
成本模型），产出 :class:`RouteDecision`：

- ``source_format`` -> MIME -> registry 中**第一个**满足
  ``backend_kind="local"`` 且 ``license_status="ok"`` 的 descriptor；
  router **不硬编码 parser_id**，路由结果完全由注册的 descriptor 决定
  （未过审 / 云端 backend 不会被选中，SRS §C04）。
- unknown 格式不抛：返回 ``primary_parser_id=None`` + reason
  ``unsupported_format``，由调用方决定后续动作（SRS §4.4）。
- PDF 无文本层：primary 保留（能力声明弱）、fallback 清空，reason 追加
  ``no_text_layer_needs_ocr`` + ``ocr_reserved_cloud``——云端 OCR 槽位在
  Registry 预留（``cloud_vlm`` 占位 descriptor），模型由用户后续配置。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from knowledge_mining.mining.contracts.parser_adapter import BackendRegistry
from knowledge_mining.mining.file_inspector.inspect import DocumentProfile

#: source_format -> 探测 MIME（与 SRS §C03 解析覆盖面对齐）。
_FORMAT_TO_MIME: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    "html": "text/html",
    "markdown": "text/markdown",
    "txt": "text/plain",
}

#: 可作为 local primary 的许可状态（SRS §C04：未过审不选）。
_OK_LICENSE = "ok"

#: 文本格式走 legacy 路由名；其余为 native（M3 命名约定）。
_LEGACY_ROUTE_FORMATS = frozenset({"markdown", "txt"})


@dataclass(frozen=True)
class RouteDecision:
    """一次路由决策的冻结快照（SRS §C05）.

    ``reason_codes`` 是稳定的机器可读元组（如 ``("mime_pdf",)``），供
    Operator 审计与降级链路记录；``primary_parser_id`` 为 ``None`` 表示
    无可用 backend（unknown 格式或全部 local backend 不健康）。
    """

    primary_parser_id: str | None
    fallback_parser_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    route_name: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)


class ParserRouter:
    """确定性规则路由器（M3 初版；构造注入 BackendRegistry）."""

    __slots__ = ("_registry",)

    def __init__(self, registry: BackendRegistry) -> None:
        self._registry = registry

    def plan(self, profile: DocumentProfile) -> RouteDecision:
        """对画像产出路由决策；unknown / 无健康 local backend 不抛（§4.4）."""
        if profile.source_format == "unknown":
            return RouteDecision(
                primary_parser_id=None,
                fallback_parser_ids=(),
                reason_codes=("unsupported_format",),
                route_name="unsupported",
            )

        mime = _FORMAT_TO_MIME.get(profile.source_format)
        if mime is None:
            return RouteDecision(
                primary_parser_id=None,
                reason_codes=("unsupported_format",),
                route_name="unsupported",
            )

        reasons = [f"mime_{profile.source_format}"]
        primary = self._select_local(mime)
        if primary is None:
            reasons.append("no_local_backend")
            return RouteDecision(
                primary_parser_id=None,
                reason_codes=tuple(reasons),
                route_name=f"{profile.source_format}_unrouted",
            )

        route_name = (
            f"legacy_{profile.source_format}"
            if profile.source_format in _LEGACY_ROUTE_FORMATS
            else f"native_{profile.source_format}"
        )
        # M3 初版：无降级链（fallback 留空），由后续 WP 引入多 backend 竞争。
        fallback: tuple[str, ...] = ()

        if (
            profile.source_format == "pdf"
            and profile.has_text_layer is False
        ):
            # 扫描件：保留弱能力 primary，OCR 让位云端槽位（用户后续配置）
            reasons.append("no_text_layer_needs_ocr")
            reasons.append("ocr_reserved_cloud")

        return RouteDecision(
            primary_parser_id=primary.parser_id,
            fallback_parser_ids=fallback,
            reason_codes=tuple(reasons),
            route_name=route_name,
        )

    def _select_local(self, mime: str):
        """registry 中第一个支持 ``mime`` 且 local + 许可 ok 的 descriptor."""
        for descriptor in self._registry.all():
            if (
                descriptor.supports(mime)
                and descriptor.backend_kind == "local"
                and descriptor.license_status == _OK_LICENSE
            ):
                return descriptor
        return None


__all__ = [
    "ParserRouter",
    "RouteDecision",
]
