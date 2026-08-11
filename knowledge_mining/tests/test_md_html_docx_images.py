"""Cross-format image extraction: Markdown / HTML / DOCX (+ caption matrix)."""
from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

import pytest

from knowledge_mining.mining.contracts.models import DocumentProfile, RawFileData
from knowledge_mining.mining.ingestion.preprocessing import html_to_markdown
from knowledge_mining.mining.infra.docx_parser import parse_docx_to_section_tree
from knowledge_mining.mining.infra.image_assets import (
    absolutize_markdown_image_paths,
    stage_markdown_images,
)
from knowledge_mining.mining.infra.structure import parse_structure
from knowledge_mining.mining.pipeline import (
    DocumentContext,
    PipelineConfig,
    parse_stage,
    segment_stage,
)
from knowledge_mining.mining.stages.parse import MarkdownParser, create_parser
from knowledge_mining.mining.stages.segment import DefaultSegmenter
from knowledge_mining.mining.workflow.operators.options import ParseSegmentOptions


def _png_bytes() -> bytes:
    # Minimal valid-ish PNG header + payload for materialize tests.
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_markdown_splits_local_image(tmp_path: Path):
    img = tmp_path / "fig.png"
    img.write_bytes(_png_bytes())
    md = "Before.\n\n![架构](fig.png)\n\nAfter."
    dump = tmp_path / "dump"
    tree = parse_structure(
        md,
        context={
            "file_path": str(tmp_path / "doc.md"),
            "image_dir": str(dump),
            "image_kind": "md_image",
        },
    )
    types = [b.block_type for b in tree.blocks]
    assert types == ["paragraph", "image", "paragraph"]
    image = tree.blocks[1]
    assert image.structure["kind"] == "md_image"
    assert image.structure["native_caption"] == "架构"
    assert Path(image.structure["image_path"]).is_file()
    assert image.structure["image_sha256"]


def test_markdown_data_uri_and_remote_skip(tmp_path: Path):
    uri = "data:image/png;base64," + base64.b64encode(_png_bytes()).decode()
    tree = parse_structure(
        f"![]({uri})\n\n![r](https://example.com/x.png)",
        context={"image_dir": str(tmp_path / "d"), "file_path": str(tmp_path / "a.md")},
    )
    assert tree.blocks[0].block_type == "image"
    assert tree.blocks[0].structure.get("image_path")
    assert tree.blocks[1].structure.get("caption_source") == "remote_skipped"


def test_html_to_markdown_absolutizes_relative_images(tmp_path: Path):
    img = tmp_path / "images" / "a.png"
    img.parent.mkdir()
    img.write_bytes(_png_bytes())
    html = tmp_path / "page.html"
    html.write_text(
        "<html><body><p>Hi</p><img src='images/a.png' alt='图1'/></body></html>",
        encoding="utf-8",
    )
    md = html_to_markdown(html, doc_title="page")
    assert img.resolve().as_posix() in md.replace("\\", "/")
    tree = MarkdownParser().parse(
        md,
        "page.html",
        {
            "file_path": str(html),
            "image_dir": str(tmp_path / "dump"),
            "source_format": "html",
        },
    )
    assert tree is not None
    images = [b for b in tree.blocks if b.block_type == "image"]
    assert len(images) == 1
    assert images[0].structure["kind"] == "html_image"
    assert Path(images[0].structure["image_path"]).is_file()


def test_stage_markdown_images_survives_source_delete(tmp_path: Path):
    src = tmp_path / "src.png"
    src.write_bytes(_png_bytes())
    md = f"![x]({src.resolve().as_posix()})"
    stage = tmp_path / "stage"
    rewritten = stage_markdown_images(md, stage)
    src.unlink()
    assert "![x](" in rewritten
    staged_path = rewritten.split("](")[1].rstrip(")")
    assert Path(staged_path).is_file()


def test_docx_extracts_embedded_image(tmp_path: Path):
    pytest.importorskip("docx")
    docx_path = _write_minimal_docx_with_png(tmp_path / "with_img.docx", _png_bytes())
    dump = tmp_path / "dump"
    tree = parse_docx_to_section_tree(
        str(docx_path), doc_title="with_img.docx", image_dir=str(dump),
    )
    images = [b for b in _iter_blocks(tree) if b.block_type == "image"]
    assert len(images) >= 1
    assert images[0].structure["kind"] == "docx_image"
    assert Path(images[0].structure["image_path"]).is_file()


def test_docx_image_block_from_blip_materializes(tmp_path: Path):
    """Unit-test blip dump without requiring a full OOXML package parse."""
    import sys
    import types

    from knowledge_mining.mining.infra import docx_parser as mod

    class Part:
        blob = _png_bytes()
        content_type = "image/png"

    class FakePartRoot:
        related_parts = {"rId4": Part()}

    class FakeDoc:
        part = FakePartRoot()

    class Blip:
        def get(self, _key):
            return "rId4"

    fake_ns = types.ModuleType("docx.oxml.ns")
    fake_ns.qn = lambda name: name
    sys.modules["docx.oxml.ns"] = fake_ns
    sys.modules.setdefault("docx", types.ModuleType("docx"))
    sys.modules.setdefault("docx.oxml", types.ModuleType("docx.oxml"))

    block, idx = mod._image_block_from_blip(
        Blip(), doc=FakeDoc(), image_dir=str(tmp_path), image_idx=0,
    )
    assert block is not None
    assert idx == 1
    assert block.structure["kind"] == "docx_image"
    assert Path(block.structure["image_path"]).is_file()


def test_parse_segment_matrix_caption_toggle(tmp_path: Path, monkeypatch):
    img = tmp_path / "f.png"
    img.write_bytes(_png_bytes())
    md_path = tmp_path / "doc.md"
    md_path.write_text(f"Intro\n\n![cap](f.png)\n", encoding="utf-8")
    raw = RawFileData(
        file_path=str(md_path),
        relative_path="doc.md",
        file_name="doc.md",
        file_type="markdown",
        content=md_path.read_text(encoding="utf-8"),
        raw_content_hash="a" * 64,
        normalized_content_hash="b" * 64,
    )
    from knowledge_mining.mining.stages.image_caption import ImageCaptioner

    captioner = ImageCaptioner(base_url="http://llm.test", enabled=False)
    monkeypatch.setattr(
        captioner._client,
        "execute",
        lambda **_: {
            "success": True,
            "data": {"status": "succeeded", "result": {"text_output": "矩阵图注"}},
        },
    )
    cfg = PipelineConfig(
        domain="test",
        run_id="matrix-1",
        parser_factory=create_parser,
        segmenter=DefaultSegmenter(),
        image_captioner=captioner,
    )
    ctx = DocumentContext(
        raw_file=raw,
        profile=DocumentProfile(document_key="doc:/doc.md"),
    )
    parsed = parse_stage(ctx, cfg)
    assert any(b.block_type == "image" for b in parsed.tree.blocks)

    off = segment_stage(
        parsed, cfg, options=ParseSegmentOptions(enableImageCaption=False)
    )
    img_segs = [s for s in off.segments if s.block_type == "image"]
    assert img_segs and img_segs[0].raw_text == ""

    on = segment_stage(
        parsed, cfg, options=ParseSegmentOptions(enableImageCaption=True)
    )
    img_segs = [s for s in on.segments if s.block_type == "image"]
    assert img_segs and img_segs[0].raw_text == "矩阵图注"


def test_absolutize_skips_remote_and_data():
    base = Path("/tmp")
    md = '![a](http://x/y.png) ![b](data:image/png;base64,aa)'
    assert absolutize_markdown_image_paths(md, base) == md


def test_fetch_remote_images_option_materializes(tmp_path: Path, monkeypatch):
    from knowledge_mining.mining.infra import image_assets as assets

    monkeypatch.setattr(
        assets,
        "_fetch_remote_image",
        lambda url, image_dir, stem="img", timeout=10.0: assets.materialize_image_bytes(
            _png_bytes(), image_dir, stem=stem, ext=".png",
        ),
    )
    tree = parse_structure(
        "![r](https://example.com/x.png)",
        context={
            "image_dir": str(tmp_path / "d"),
            "file_path": str(tmp_path / "a.md"),
            "fetch_remote_images": True,
        },
    )
    assert tree.blocks[0].structure.get("image_path")
    assert Path(tree.blocks[0].structure["image_path"]).is_file()


def _iter_blocks(node):
    yield from node.blocks
    for child in node.children:
        yield from _iter_blocks(child)


def _write_minimal_docx_with_png(path: Path, png: bytes) -> Path:
    """Build a tiny DOCX zip with one paragraph containing an embedded PNG."""
    # Relationship + content types + document with a:blip.
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
            xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
  <w:body>
    <w:p>
      <w:r>
        <w:drawing>
          <wp:inline>
            <a:graphic>
              <a:graphicData>
                <pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
                  <pic:blipFill>
                    <a:blip r:embed="rId4"/>
                  </pic:blipFill>
                </pic:pic>
              </a:graphicData>
            </a:graphic>
          </wp:inline>
        </w:drawing>
      </w:r>
    </w:p>
    <w:sectPr/>
  </w:body>
</w:document>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>
"""
    doc_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId4"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
    Target="media/image1.png"/>
</Relationships>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        zf.writestr("word/media/image1.png", png)
    path.write_bytes(buf.getvalue())
    return path
