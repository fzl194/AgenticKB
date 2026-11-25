# -*- coding: utf-8 -*-
"""独立图片 VLM 解析适配器（S3 文件格式支持补全，68号报告 §六.S3）.

定位：**本地 backend 的新解析器**（不是激活 cloud_vlm 占位——那是云端
OCR 槽位，语义不同，保留不动）。独立 png/jpg 作为文档上传时，整图调
VLM 转结构化文字描述，产出可检索文本；模型不可用=解析失败（诚实降级，
不伪造空文档）。

- 视觉通道由外部注入（``set_vision_describer``）：生产端是
  ``build_new_chain_services`` 把已构建的 ``VlmFigureCaptioner`` 注册进来
  （与图片图注同一通道、互不依赖）；未注册（无多模态模型/测试环境）时
  parse 抛 ``ParserAdapterError``，路由诚实失败。
- 产物：单 ``paragraph`` 块（描述全文）+ 块级 ``native_ref`` 记录模型与
  描述版本——下游切片/检索走 LegacyLineNormalizer 既有投影，零新表。
- 提示词契约独立版本（``vlm-image-describe-1``），比图注更完整：图类型/
  主体/全部可见文字/数值/结构/结论分节。
- 防轰炸：字节上限（超大图直接失败，不传 VLM）+ 下限（空/截断图）。

fingerprint：``native_image@1.0.0#vlm-describe-v1``。
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable

from knowledge_mining.mining.contracts.parser_adapter import (
    BackendBlock,
    BackendParseArtifact,
    ParserAdapterError,
    ParserDescriptor,
    UnsupportedFormat,
)

NATIVE_IMAGE_PARSER_ID = "native_image"
NATIVE_IMAGE_VERSION = "1.0.0"
NATIVE_IMAGE_FINGERPRINT = f"{NATIVE_IMAGE_PARSER_ID}@{NATIVE_IMAGE_VERSION}#vlm-describe-v1"

NATIVE_IMAGE_MIMES = frozenset({"image/png", "image/jpeg"})
#: gif 故意不收（动图语义不明，68号报告 §六.S3 留白）。

#: 图片字节上下限：0.25KiB 以下视为截断/占位；16MiB 以上拒绝传 VLM。
_MIN_IMAGE_BYTES = 256
_MAX_IMAGE_BYTES = 16 * 1024 * 1024

IMAGE_DESCRIBE_SYSTEM_PROMPT = "你是技术文档图片解读助手。"

#: 独立图片解析提示词（S3）：分节、全部可见文字与数值必须转述。
IMAGE_DESCRIBE_USER_PROMPT = (
    "请解读这张作为独立文档入库的图片，输出一份结构化文字描述，"
    "按以下小节组织（无对应内容的小节省略）：\n"
    "1. 图类型：流程图/柱状图/表格图/照片/截图/架构图/其他\n"
    "2. 主体：图片展示的核心对象与场景\n"
    "3. 可见文字：逐条转述图中全部可见文字（标题、图例、节点标签、单元格）\n"
    "4. 数值：全部数值及各自归属（数值照抄，注明单位）\n"
    "5. 结构：图中元素之间的关系（流向/层级/对比）\n"
    "6. 结论：一句话概括这张图传达的信息\n"
    "要求：忠实转述、不得编造图中不存在的文字或数值；总长不超过 300 字；"
    "只输出描述正文，不要客套话。"
)
#: 提示词版本（进 parser_fingerprint 语义与审计；换提示词递增）。
IMAGE_DESCRIBE_PROMPT_VERSION = "vlm-image-describe-1"

#: 视觉描述通道（``VlmFigureCaptioner`` 或测试替身）。生产端由
#: ``build_new_chain_services`` 注册；``None`` = 未配置 → 解析失败。
_vision_describer: Any | None = None


def set_vision_describer(describer: Any | None) -> None:
    """注册/清除视觉描述通道（进程级；None=回到未配置态）。"""
    global _vision_describer
    _vision_describer = describer


def vision_describer() -> Any | None:
    return _vision_describer


class NativeImageParser:
    """DocumentParser 实现：整图 → VLM 结构化描述 → 单 paragraph 块."""

    def __init__(self) -> None:
        self.descriptor = ParserDescriptor(
            parser_id=NATIVE_IMAGE_PARSER_ID,
            display_name="Native Image Parser (VLM describe)",
            version=NATIVE_IMAGE_VERSION,
            supported_mimes=NATIVE_IMAGE_MIMES,
            backend_kind="local",
            parser_fingerprint=NATIVE_IMAGE_FINGERPRINT,
            capabilities=frozenset({"vision", "image"}),
        )

    def supports(self, mime: str) -> bool:
        return self.descriptor.supports(mime)

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        if not self.supports(mime):
            raise UnsupportedFormat(
                f"{NATIVE_IMAGE_PARSER_ID} cannot parse mime {mime!r}"
            )
        if len(data) < _MIN_IMAGE_BYTES:
            raise ParserAdapterError(
                f"{NATIVE_IMAGE_PARSER_ID}: image too small/truncated "
                f"({len(data)} bytes < {_MIN_IMAGE_BYTES})"
            )
        if len(data) > _MAX_IMAGE_BYTES:
            raise ParserAdapterError(
                f"{NATIVE_IMAGE_PARSER_ID}: image exceeds {_MAX_IMAGE_BYTES} "
                f"bytes ({len(data)}); refusing to send to VLM"
            )

        describer = vision_describer()
        if describer is None:
            raise ParserAdapterError(
                f"{NATIVE_IMAGE_PARSER_ID}: vision channel not configured "
                "(llm_service vision-default unavailable; cannot parse image)"
            )
        model = getattr(describer, "default_model", None)
        if not model:
            raise ParserAdapterError(
                f"{NATIVE_IMAGE_PARSER_ID}: no vision model resolved "
                "(llm_service vision-default returned none)"
            )

        try:
            description = describer.describe(
                image_bytes=data,
                system_prompt=IMAGE_DESCRIBE_SYSTEM_PROMPT,
                user_prompt=IMAGE_DESCRIBE_USER_PROMPT,
                model=model,
                pipeline_stage="image_parse",
            )
        except Exception as exc:
            raise ParserAdapterError(
                f"{NATIVE_IMAGE_PARSER_ID}: VLM describe failed: {exc}"
            ) from exc
        if not description or not description.strip():
            raise ParserAdapterError(
                f"{NATIVE_IMAGE_PARSER_ID}: VLM returned empty description"
            )
        description = description.strip()

        image_sha = hashlib.sha256(data).hexdigest()
        return BackendParseArtifact(
            parser_id=NATIVE_IMAGE_PARSER_ID,
            parser_version=NATIVE_IMAGE_VERSION,
            mime=mime.lower(),
            blocks=(
                BackendBlock(
                    block_type="paragraph",
                    text=description,
                    native_ref={
                        "kind": "vlm_image_describe",
                        "image_sha256": image_sha,
                        "model": model,
                        "prompt_version": IMAGE_DESCRIBE_PROMPT_VERSION,
                    },
                ),
            ),
            warnings=(),
        )


__all__ = [
    "IMAGE_DESCRIBE_PROMPT_VERSION", "IMAGE_DESCRIBE_SYSTEM_PROMPT",
    "IMAGE_DESCRIBE_USER_PROMPT", "NATIVE_IMAGE_FINGERPRINT",
    "NATIVE_IMAGE_MIMES", "NATIVE_IMAGE_PARSER_ID", "NATIVE_IMAGE_VERSION",
    "NativeImageParser", "set_vision_describer", "vision_describer",
]
