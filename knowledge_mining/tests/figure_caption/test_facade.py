"""figure_caption 包单元测试（方案B B1）。"""
import hashlib

import pytest

from knowledge_mining.mining.contracts.parse_ir.types import (
    Diagnostics,
    Element,
    FigureAsset,
    ParseIdentity,
    ParsedDocument,
    Relation,
)
from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment
from knowledge_mining.mining.figure_caption import (
    FIGCAP_PROMPT_VERSION,
    FIGURE_SEGMENT_INDEX_BASE,
    CaptionOutcome,
    FigureCaptionFacade,
    FigureCaptionParams,
    build_user_prompt,
    caption_cache_key,
)
from knowledge_mining.mining.figure_caption.prompts import (
    FIGCAP_SYSTEM_PROMPT,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 测试工厂
# ---------------------------------------------------------------------------

IDENT = ParseIdentity(
    source_raw_hash="rawhash-1", parser_fingerprint="pptx-fp-1"
)


def _element(
    element_id: str,
    element_type: str,
    text: str = "",
    *,
    order: int = 0,
    level: int | None = None,
    style: dict | None = None,
) -> Element:
    return Element(
        element_id=element_id,
        element_type=element_type,
        order_index=order,
        text=text,
        style={**({"level": level} if level is not None else {}), **(style or {})},
    )


def _doc(
    elements: list[Element],
    *,
    relations: tuple[Relation, ...] = (),
    figures: dict[str, FigureAsset] | None = None,
    binary_assets: dict | None = None,
) -> ParsedDocument:
    return ParsedDocument(
        schema_version="1.0",
        source_identity=IDENT,
        containers=(),
        elements=tuple(elements),
        diagnostics=Diagnostics(),
        relations=relations,
        binary_assets=binary_assets or {},
        structured_assets=figures or {},
    )


def _figure_asset(sha: str, *, derived: dict | None = None) -> FigureAsset:
    return FigureAsset(
        figure_id="placeholder",  # 由 _collect 侧用 element_id 前缀查
        original_image_ref=f"bin-{sha[:16]}",
        image_hash=sha,
        derived_annotations=derived or {},
    )


class _FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.puts: list[dict] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def put(self, key, caption, **kwargs) -> None:
        self.puts.append({"key": key, "caption": caption, **kwargs})
        self.store[key] = caption


class _FakeBytes:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs
        self.requests: list[str] = []

    async def get_image_bytes(self, image_sha256: str) -> bytes | None:
        self.requests.append(image_sha256)
        return self.blobs.get(image_sha256)


class _FakeCaptioner:
    def __init__(self, caption: str | None = "VLM 图注文本") -> None:
        self.caption_text = caption
        self.calls: list[dict] = []

    def caption(self, *, image_bytes, mime, native_caption) -> str | None:
        self.calls.append(
            {"size": len(image_bytes), "mime": mime, "native": native_caption}
        )
        if isinstance(self.caption_text, Exception):
            raise self.caption_text
        return self.caption_text


class _FakeAppender:
    def __init__(self) -> None:
        self.batches: list[tuple[str, str, tuple[CompiledSegment, ...]]] = []

    async def append_figures_for_snapshot(
        self, snapshot_id, document_key, segments
    ) -> int:
        self.batches.append((snapshot_id, document_key, segments))
        return len(segments)


async def _run(
    doc: ParsedDocument,
    *,
    cache=None,
    blobs=None,
    captioner=None,
    params=None,
) -> tuple[CaptionOutcome, _FakeAppender]:
    cache = cache or _FakeCache()
    byte_source = _FakeBytes(blobs or {})
    captioner = captioner or _FakeCaptioner()
    appender = _FakeAppender()
    facade = FigureCaptionFacade(
        cache=cache,
        image_byte_source=byte_source,
        captioner=captioner,
        appender=appender,
    )
    outcome = await facade.generate_for_snapshot(
        snapshot_id="snap-1",
        document_key="doc-1",
        domain="default",
        doc=doc,
        params=params,
    )
    return outcome, appender


# ---------------------------------------------------------------------------
# 提示词契约
# ---------------------------------------------------------------------------


def test_prompt_contract_stable():
    assert FIGCAP_SYSTEM_PROMPT == "你是技术文档图注助手。"
    assert build_user_prompt(None) == build_user_prompt("  ")
    assert "原始图注/表注" in build_user_prompt("图1 架构图")


def test_cache_key_deterministic_and_component_sensitive():
    base = dict(
        domain="d1",
        source_raw_hash="h1",
        parser_fingerprint="fp1",
        figure_element_id="e1",
        model="glm-4.5v",
        prompt_version=FIGCAP_PROMPT_VERSION,
    )
    k1 = caption_cache_key(**base)
    assert k1 == caption_cache_key(**base)
    assert len(k1) == 64
    for field_name, value in [
        ("domain", "d2"),
        ("source_raw_hash", "h2"),
        ("parser_fingerprint", "fp2"),
        ("figure_element_id", "e2"),
        ("model", "glm-4.6v"),
        ("prompt_version", "vlm-figure-caption-2"),
    ]:
        changed = {**base, field_name: value}
        assert caption_cache_key(**changed) != k1, field_name


# ---------------------------------------------------------------------------
# 候选收集
# ---------------------------------------------------------------------------


def _two_figure_doc():
    sha_a, sha_b = "a" * 64, "b" * 64
    elements = [
        _element("h1", "heading", "系统架构", level=1),
        _element("fig1", "figure"),
        _element("p1", "paragraph", "正文说明"),
        _element("fig2", "figure"),
    ]
    doc = _doc(
        elements,
        figures={
            "fig1-figure": _figure_asset(sha_a),
            "fig2-figure": _figure_asset(sha_b),
        },
        binary_assets={
            f"bin-{sha_a[:16]}": {"sha256": sha_a, "mime": "image/png", "size": 4096},
            f"bin-{sha_b[:16]}": {"sha256": sha_b, "mime": "image/png", "size": 4096},
        },
    )
    return doc, sha_a, sha_b


async def test_caption_happy_path_appends_segments():
    doc, sha_a, sha_b = _two_figure_doc()
    outcome, appender = await _run(doc, blobs={sha_a: b"x" * 4096, sha_b: b"y" * 4096})
    assert outcome.captioned == 2
    assert outcome.appended == 2
    assert not outcome.degraded
    (snap, key, segments), = appender.batches
    assert (snap, key) == ("snap-1", "doc-1")
    assert [s.segment_index for s in segments] == [
        FIGURE_SEGMENT_INDEX_BASE,
        FIGURE_SEGMENT_INDEX_BASE + 1,
    ]
    seg = segments[0]
    assert seg.block_type == "figure"
    assert seg.raw_text == "VLM 图注文本"
    assert seg.heading_chain == ((1, "系统架构"),)
    assert seg.metadata["caption_source"] == "vlm"
    assert seg.metadata["figure_element_id"] == "fig1"
    assert seg.metadata["image_sha256"] == sha_a
    assert seg.metadata["prompt_version"] == FIGCAP_PROMPT_VERSION


async def test_no_figures_is_noop():
    doc = _doc([_element("p1", "paragraph", "纯文本")])
    outcome, appender = await _run(doc)
    assert outcome == CaptionOutcome()
    assert appender.batches == []


async def test_figure_with_text_or_bound_caption_excluded():
    sha = "c" * 64
    elements = [
        _element("fig-text", "figure", "已有文本"),
        _element("cap1", "caption", "图1 原生图注"),
        _element("fig-bound", "figure"),
    ]
    doc = _doc(
        elements,
        relations=(
            Relation(
                source_element_id="cap1",
                target_element_id="fig-bound",
                relation_type="caption_of",
            ),
        ),
        figures={
            "fig-text-figure": _figure_asset(sha),
            "fig-bound-figure": _figure_asset(sha),
        },
    )
    outcome, _ = await _run(doc, blobs={sha: b"x" * 4096})
    assert outcome.figures_found == 0
    assert outcome.appended == 0


async def test_derived_annotations_figure_excluded_plan_a_mutex():
    sha = "d" * 64
    doc = _doc(
        [_element("fig-a", "figure")],
        figures={"fig-a-figure": _figure_asset(sha, derived={"caption": "A方案"})},
    )
    outcome, _ = await _run(doc, blobs={sha: b"x" * 4096})
    assert outcome.figures_found == 0


async def test_max_figures_truncates():
    doc, sha_a, sha_b = _two_figure_doc()
    outcome, _ = await _run(
        doc, blobs={sha_a: b"x" * 4096, sha_b: b"y" * 4096},
        params=FigureCaptionParams(max_figures=1),
    )
    assert outcome.figures_found == 1
    assert outcome.captioned == 1


# ---------------------------------------------------------------------------
# 降级路径
# ---------------------------------------------------------------------------


async def test_missing_bytes_and_small_images_skipped():
    doc, sha_a, sha_b = _two_figure_doc()
    outcome, _ = await _run(doc, blobs={sha_b: b"y" * 10})  # a 缺字节，b 过小
    assert outcome.skipped_missing_bytes == 1
    assert outcome.skipped_small == 1
    assert outcome.captioned == 0
    assert outcome.degraded


async def test_captioner_failure_counts_and_continues():
    doc, sha_a, sha_b = _two_figure_doc()
    captioner = _FakeCaptioner(RuntimeError("vlm down"))
    outcome, _ = await _run(
        doc, blobs={sha_a: b"x" * 4096, sha_b: b"y" * 4096}, captioner=captioner
    )
    assert outcome.failed == 2
    assert outcome.captioned == 0
    assert outcome.degraded
    assert len(captioner.calls) == 2


async def test_cache_hit_skips_captioner_and_bytes():
    doc, sha_a, sha_b = _two_figure_doc()
    cache = _FakeCache()
    key = caption_cache_key(
        domain="default",
        source_raw_hash=IDENT.source_raw_hash,
        parser_fingerprint=IDENT.parser_fingerprint,
        figure_element_id="fig1",
        model=FigureCaptionParams().model,
        prompt_version=FIGCAP_PROMPT_VERSION,
    )
    cache.store[key] = "缓存图注"
    blobs = _FakeBytes({sha_a: b"x" * 4096, sha_b: b"y" * 4096})
    captioner = _FakeCaptioner()
    appender = _FakeAppender()
    facade = FigureCaptionFacade(
        cache=cache, image_byte_source=blobs, captioner=captioner, appender=appender
    )
    outcome = await facade.generate_for_snapshot(
        snapshot_id="snap-1",
        document_key="doc-1",
        domain="default",
        doc=doc,
    )
    assert outcome.cache_hits == 1
    assert outcome.captioned == 1
    # 缓存命中的 fig1 不取字节；fig2 走 VLM
    assert blobs.requests == [sha_b]
    assert len(captioner.calls) == 1
    (segments,) = [segs for _, _, segs in appender.batches]
    assert segments[0].raw_text == "缓存图注"


async def test_empty_caption_from_vlm_counts_failed():
    doc, sha_a, _ = _two_figure_doc()
    outcome, _ = await _run(
        _doc(
            [doc.elements[0], doc.elements[1]],  # heading + fig1
            figures={"fig1-figure": _figure_asset(sha_a)},
            binary_assets={f"bin-{sha_a[:16]}": {"sha256": sha_a, "mime": "image/png"}},
        ),
        blobs={sha_a: b"x" * 4096},
        captioner=_FakeCaptioner("   "),
    )
    assert outcome.failed == 1
    assert outcome.appended == 0
