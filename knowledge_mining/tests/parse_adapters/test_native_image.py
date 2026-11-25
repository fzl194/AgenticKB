# -*- coding: utf-8 -*-
"""native_image（S3 独立图片 VLM 解析）单元测试.

覆盖：无视觉通道=诚实失败；模型未解析=失败；描述产出单 paragraph 块并
携带模型/提示词版本审计；字节上下限守卫；gif 不收；inspect/router 路由
对账；factory resolve_pipeline 成对返回。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.parse_adapters.factory import resolve_pipeline
from knowledge_mining.mining.parse_adapters.native import native_image
from knowledge_mining.mining.parse_adapters.native.native_image import (
    IMAGE_DESCRIBE_PROMPT_VERSION,
    NATIVE_IMAGE_FINGERPRINT,
    NATIVE_IMAGE_PARSER_ID,
    NativeImageParser,
)
from knowledge_mining.mining.contracts.parser_adapter import (
    ParserAdapterError,
    UnsupportedFormat,
)

_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 512
)  # 签名 + 充量字节（绕过 _MIN_IMAGE_BYTES）


class _FakeDescriber:
    default_model = "fake-vision-model"

    def __init__(self, text: str | None = "图片描述：智能文档处理平台流程图。") -> None:
        self._text = text
        self.calls: list[dict] = []

    def describe(self, *, image_bytes, system_prompt, user_prompt, model, pipeline_stage):
        self.calls.append({
            "len": len(image_bytes), "system": system_prompt,
            "user": user_prompt, "model": model, "stage": pipeline_stage,
        })
        return self._text


@pytest.fixture(autouse=True)
def _reset_describer():
    native_image.set_vision_describer(None)
    yield
    native_image.set_vision_describer(None)


def test_no_vision_channel_fails_honestly() -> None:
    parser = NativeImageParser()
    with pytest.raises(ParserAdapterError, match="vision channel not configured"):
        parser.parse(_PNG_BYTES, mime="image/png")


def test_no_default_model_fails() -> None:
    class _NoModel:
        default_model = None

        def describe(self, **_):  # pragma: no cover - 不应被调用
            raise AssertionError("must not be called")

    native_image.set_vision_describer(_NoModel())
    with pytest.raises(ParserAdapterError, match="no vision model resolved"):
        NativeImageParser().parse(_PNG_BYTES, mime="image/png")


def test_describe_produces_single_paragraph_block() -> None:
    fake = _FakeDescriber()
    native_image.set_vision_describer(fake)
    artifact = NativeImageParser().parse(_PNG_BYTES, mime="image/png")

    assert artifact.parser_id == NATIVE_IMAGE_PARSER_ID
    assert artifact.blocks and len(artifact.blocks) == 1
    block = artifact.blocks[0]
    assert block.block_type == "paragraph"
    assert "智能文档处理平台" in block.text
    ref = block.native_ref
    assert ref["kind"] == "vlm_image_describe"
    assert ref["model"] == "fake-vision-model"
    assert ref["prompt_version"] == IMAGE_DESCRIBE_PROMPT_VERSION
    assert len(ref["image_sha256"]) == 64
    # 通道契约：pipeline_stage 区分图片解析与图注
    assert fake.calls[0]["stage"] == "image_parse"
    assert fake.calls[0]["model"] == "fake-vision-model"
    assert fake.calls[0]["len"] == len(_PNG_BYTES)


def test_empty_description_fails() -> None:
    native_image.set_vision_describer(_FakeDescriber(text="   "))
    with pytest.raises(ParserAdapterError, match="empty description"):
        NativeImageParser().parse(_PNG_BYTES, mime="image/png")


def test_describer_exception_wrapped() -> None:
    class _Boom:
        default_model = "m"

        def describe(self, **_):
            raise RuntimeError("llm down")

    native_image.set_vision_describer(_Boom())
    with pytest.raises(ParserAdapterError, match="VLM describe failed: llm down"):
        NativeImageParser().parse(_PNG_BYTES, mime="image/png")


def test_size_guards() -> None:
    native_image.set_vision_describer(_FakeDescriber())
    with pytest.raises(ParserAdapterError, match="too small"):
        NativeImageParser().parse(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, mime="image/png")
    with pytest.raises(ParserAdapterError, match="exceeds"):
        NativeImageParser().parse(b"\x89PNG" + b"\x00" * (17 * 1024 * 1024), mime="image/png")


def test_gif_and_others_unsupported() -> None:
    parser = NativeImageParser()
    assert not parser.supports("image/gif")
    with pytest.raises(UnsupportedFormat):
        parser.parse(b"GIF89a" + b"\x00" * 512, mime="image/gif")


def test_descriptor_and_pipeline_pair() -> None:
    parser = NativeImageParser()
    assert parser.descriptor.backend_kind == "local"
    assert NATIVE_IMAGE_PARSER_ID in NATIVE_IMAGE_FINGERPRINT
    pair = resolve_pipeline(NATIVE_IMAGE_PARSER_ID)
    assert pair is not None
    assert isinstance(pair[0], NativeImageParser)


def test_inspect_and_router_route_image() -> None:
    from knowledge_mining.mining.file_inspector.inspect import FileInspector
    from knowledge_mining.mining.file_inspector.router import (
        ParserRouter,
        _FORMAT_TO_MIME,
    )
    from knowledge_mining.mining.parse_adapters.registry import build_default_registry

    inspector = FileInspector()
    profile = inspector.inspect(_PNG_BYTES, declared_mime="image/png")
    assert profile.source_format == "png"
    jpeg = inspector.inspect(b"\xff\xd8\xff\xe0" + b"\x00" * 512, declared_mime=None)
    assert jpeg.source_format == "jpeg"

    assert _FORMAT_TO_MIME["png"] == "image/png"
    router = ParserRouter(build_default_registry())
    decision = router.plan(profile)
    assert decision.primary_parser_id == NATIVE_IMAGE_PARSER_ID
    assert decision.route_name == "native_png"
    # gif 无格式映射 → 诚实 unsupported
    gif = inspector.inspect(b"GIF89a" + b"\x00" * 512, declared_mime="image/gif")
    assert gif.source_format == "unknown"
    assert router.plan(gif).primary_parser_id is None
