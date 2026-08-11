"""Segment stage recognizes block_type=image (empty caption OK)."""
from __future__ import annotations

from knowledge_mining.mining.contracts.models import (
    ContentBlock,
    DocumentProfile,
    SectionNode,
)
from knowledge_mining.mining.stages.segment import segment_document


def test_segment_image_empty_caption_keeps_structure():
    image = ContentBlock(
        block_type="image",
        text="",
        structure={
            "kind": "pdf_image",
            "page": 1,
            "bbox": [10.0, 20.0, 110.0, 80.0],
            "image_path": "/tmp/mining_runs/r1/images/doc/p1_01.png",
            "image_sha256": "a" * 64,
        },
    )
    tree = SectionNode(
        title="doc.pdf",
        level=0,
        blocks=(
            ContentBlock(block_type="paragraph", text="Above the figure."),
            image,
            ContentBlock(block_type="paragraph", text="Below the figure."),
        ),
    )
    profile = DocumentProfile(document_key="doc:/doc.pdf")
    segs = segment_document(tree, profile, parser_name="pdf")

    types = [s.block_type for s in segs]
    assert types == ["paragraph", "image", "paragraph"]

    img = segs[1]
    assert img.raw_text == ""
    assert img.token_count == 0
    assert img.structure_json["kind"] == "pdf_image"
    assert img.structure_json["image_path"].endswith("p1_01.png")
    assert img.structure_json["image_sha256"] == "a" * 64
    assert img.content_hash != segs[0].content_hash
    assert len(img.content_hash) == 64


def test_segment_image_not_merged_into_neighbors():
    """Empty image must stay its own segment even when neighbors are short."""
    tree = SectionNode(
        title="doc.pdf",
        level=0,
        blocks=(
            ContentBlock(block_type="paragraph", text="Hi"),
            ContentBlock(
                block_type="image",
                text="",
                structure={"kind": "pdf_image", "image_sha256": "b" * 64},
            ),
            ContentBlock(block_type="paragraph", text="Lo"),
        ),
    )
    profile = DocumentProfile(document_key="doc:/doc.pdf")
    segs = segment_document(tree, profile, parser_name="pdf")
    assert [s.block_type for s in segs] == ["paragraph", "image", "paragraph"]


def test_segment_image_with_caption_text():
    tree = SectionNode(
        title="doc.pdf",
        level=0,
        blocks=(
            ContentBlock(
                block_type="image",
                text="Figure 1: Architecture overview",
                structure={"kind": "pdf_image", "image_sha256": "c" * 64},
            ),
        ),
    )
    profile = DocumentProfile(document_key="doc:/doc.pdf")
    segs = segment_document(tree, profile, parser_name="pdf")
    assert len(segs) == 1
    assert segs[0].block_type == "image"
    assert segs[0].raw_text == "Figure 1: Architecture overview"
    assert segs[0].token_count and segs[0].token_count > 0
