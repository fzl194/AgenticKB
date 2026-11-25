"""图注模型解析测试（模型选择归 llm_service 管）。

算子参数 model 可留空（None）＝用 llm_service 配置的默认视觉模型
（captioner.default_model，来自 GET /api/v1/models/vision-default）；
显式填写则覆盖默认。缓存键始终用解析后的具体模型名——换默认模型
必换缓存，不会串旧图注。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.contracts.parse_ir.types import Element, FigureAsset
from knowledge_mining.mining.figure_caption import (
    FIGCAP_PROMPT_VERSION,
    FigureCaptionFacade,
    FigureCaptionParams,
    caption_cache_key,
)
from tests.figure_caption.test_facade import (
    _FakeAppender,
    _FakeBytes,
    _FakeCache,
    _FakeCaptioner,
    _doc,
    _element,
    _figure_asset,
)

pytestmark = pytest.mark.asyncio

VISION_DEFAULT = "qwen3-vl:8b"


class _DefaultModelCaptioner(_FakeCaptioner):
    """带 default_model 的 captioner（生产 VlmFigureCaptioner 形态）。"""

    default_model = VISION_DEFAULT


def _one_figure_doc() -> "object":
    sha = "a" * 64
    elements = [
        _element("h1", "heading", "系统架构", order=0, level=1),
        _element("fig1", "figure", "", order=1),
    ]
    return _doc(
        elements,
        figures={"fig1-figure": _figure_asset(sha)},
        binary_assets={f"bin-{sha[:16]}": {"mime": "image/png"}},
    ), sha


async def test_params_model_none_uses_captioner_default_model():
    doc, sha = _one_figure_doc()
    cache, captioner = _FakeCache(), _DefaultModelCaptioner()
    appender = _FakeAppender()
    facade = FigureCaptionFacade(
        cache=cache,
        image_byte_source=_FakeBytes({sha: b"x" * 2048}),
        captioner=captioner,
        appender=appender,
    )
    outcome = await facade.generate_for_snapshot(
        snapshot_id="s1", document_key="k1", domain="odn",
        doc=doc, params=FigureCaptionParams(model=None),
    )
    assert outcome.captioned == 1
    # 提交用的模型 = 默认视觉模型
    assert captioner.calls[0]["model"] == VISION_DEFAULT
    # 缓存键/审计列/切片 metadata 都用解析后的具体模型名
    expected_key = caption_cache_key(
        domain="odn", source_raw_hash="rawhash-1", parser_fingerprint="pptx-fp-1",
        figure_element_id="fig1", model=VISION_DEFAULT,
        prompt_version=FIGCAP_PROMPT_VERSION,
    )
    assert cache.puts[0]["key"] == expected_key
    assert cache.puts[0]["model"] == VISION_DEFAULT
    assert appender.batches[0][2][0].metadata["vlm_model"] == VISION_DEFAULT


async def test_params_model_overrides_captioner_default():
    doc, sha = _one_figure_doc()
    captioner = _DefaultModelCaptioner()
    facade = FigureCaptionFacade(
        cache=_FakeCache(),
        image_byte_source=_FakeBytes({sha: b"x" * 2048}),
        captioner=captioner,
        appender=_FakeAppender(),
    )
    outcome = await facade.generate_for_snapshot(
        snapshot_id="s1", document_key="k1", domain="odn",
        doc=doc, params=FigureCaptionParams(model="glm-4.5v"),
    )
    assert outcome.captioned == 1
    assert captioner.calls[0]["model"] == "glm-4.5v"


async def test_no_model_resolved_degrades_without_cache_or_llm():
    doc, sha = _one_figure_doc()
    cache = _FakeCache()
    captioner = _FakeCaptioner()  # default_model 置空＝vision-default 不可得
    captioner.default_model = None
    facade = FigureCaptionFacade(
        cache=cache,
        image_byte_source=_FakeBytes({sha: b"x" * 2048}),
        captioner=captioner,
        appender=_FakeAppender(),
    )
    outcome = await facade.generate_for_snapshot(
        snapshot_id="s1", document_key="k1", domain="odn",
        doc=doc, params=FigureCaptionParams(model=None),
    )
    assert outcome.figures_found == 1
    assert outcome.failed == 1
    assert outcome.degraded is True
    assert outcome.captioned == 0 and outcome.appended == 0
    assert cache.puts == []            # 不缓存、
    assert captioner.calls == []       # 不打 LLM、
