"""Unit tests for ImageCaptioner + captioning inside segment_stage."""
from __future__ import annotations

from pathlib import Path

from knowledge_mining.mining.contracts.models import (
    ContentBlock,
    DocumentProfile,
    SectionNode,
)
from knowledge_mining.mining.pipeline import (
    DocumentContext,
    PipelineConfig,
    segment_stage,
)
from knowledge_mining.mining.stages.image_caption import ImageCaptioner
from knowledge_mining.mining.stages.segment import DefaultSegmenter
from knowledge_mining.mining.workflow.operators.options import ParseSegmentOptions


def _tree_with_image(image_path: str, *, text: str = "") -> SectionNode:
    return SectionNode(
        title="doc.pdf",
        level=0,
        blocks=(
            ContentBlock(block_type="paragraph", text="Above the figure with enough text."),
            ContentBlock(
                block_type="image",
                text=text,
                structure={
                    "kind": "pdf_image",
                    "page": 2,
                    "image_path": image_path,
                    "image_sha256": "a" * 64,
                    "native_caption": "图 2 架构",
                },
            ),
            ContentBlock(block_type="paragraph", text="Below the figure with enough text."),
        ),
    )


def _ctx(tree: SectionNode) -> DocumentContext:
    return DocumentContext(
        tree=tree,
        profile=DocumentProfile(document_key="doc:/doc.pdf"),
    )


def test_captioner_fills_text_from_vlm(tmp_path: Path, monkeypatch) -> None:
    img = tmp_path / "fig.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    captioner = ImageCaptioner(base_url="http://llm.test", enabled=True)

    def fake_execute(**kwargs):
        assert kwargs.get("model") == "glm-4.5v"
        assert kwargs.get("pipeline_stage") == "segment"
        assert kwargs.get("messages")
        return {
            "success": True,
            "data": {
                "status": "succeeded",
                "result": {"text_output": "系统架构总览图"},
            },
        }

    monkeypatch.setattr(captioner._client, "execute", fake_execute)
    out = captioner.caption_tree(_tree_with_image(str(img)))
    image = out.blocks[1]
    assert image.text == "系统架构总览图"
    assert image.structure["caption_source"] == "vlm"


def test_captioner_fallback_when_llm_fails(tmp_path: Path, monkeypatch) -> None:
    img = tmp_path / "fig.png"
    img.write_bytes(b"x")
    captioner = ImageCaptioner(base_url="http://llm.test", enabled=True)
    monkeypatch.setattr(captioner._client, "execute", lambda **_: None)
    out = captioner.caption_tree(_tree_with_image(str(img)))
    image = out.blocks[1]
    assert image.text == "图 2 架构"
    assert image.structure["caption_source"] == "fallback"


def test_segment_stage_captions_when_option_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    img = tmp_path / "fig.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    captioner = ImageCaptioner(base_url="http://llm.test", enabled=False)
    monkeypatch.setattr(
        captioner._client,
        "execute",
        lambda **_: {
            "success": True,
            "data": {
                "status": "succeeded",
                "result": {"text_output": "开启后的图注"},
            },
        },
    )
    cfg = PipelineConfig(
        domain="test",
        segmenter=DefaultSegmenter(),
        image_captioner=captioner,
    )
    ctx = _ctx(_tree_with_image(str(img)))

    skipped = segment_stage(
        ctx, cfg, options=ParseSegmentOptions(enableImageCaption=False)
    )
    img_segs = [s for s in skipped.segments if s.block_type == "image"]
    assert img_segs and img_segs[0].raw_text == ""

    ran = segment_stage(
        ctx, cfg, options=ParseSegmentOptions(enableImageCaption=True)
    )
    img_segs = [s for s in ran.segments if s.block_type == "image"]
    assert img_segs and img_segs[0].raw_text == "开启后的图注"
    assert ran.tree is not None
    assert ran.tree.blocks[1].text == "开启后的图注"
    # Shared captioner must not stay permanently enabled after segment.
    assert captioner.enabled is False


def test_segment_stage_noop_without_captioner() -> None:
    tree = _tree_with_image("/missing.png")
    cfg = PipelineConfig(domain="test", segmenter=DefaultSegmenter())
    out = segment_stage(_ctx(tree), cfg)
    img_segs = [s for s in out.segments if s.block_type == "image"]
    assert img_segs and img_segs[0].raw_text == ""
