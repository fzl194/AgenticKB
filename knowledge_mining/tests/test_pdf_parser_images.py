"""Tests for PDF LTImage extraction → block_type=image + run-scoped dump."""
from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_mining.mining.infra.pdf_parser import parse_pdf_to_section_tree
from knowledge_mining.mining.infra.run_workdir import resolve_run_image_dir


def _collect_blocks(node):
    blocks = list(node.blocks)
    for child in node.children:
        blocks.extend(_collect_blocks(child))
    return blocks


def _build_pdf_with_image(path: Path) -> None:
    """Write a minimal one-page PDF: text above, 2x2 RGB image, text below."""
    # 2x2 DeviceRGB pixels (red, green, blue, white)
    pixels = bytes([
        255, 0, 0,
        0, 255, 0,
        0, 0, 255,
        255, 255, 255,
    ])

    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
        b"/Contents 4 0 R "
        b"/Resources << /XObject << /Im1 5 0 R >> "
        b"/Font << /F1 6 0 R >> >> >>"
    )
    content = (
        b"BT /F1 12 Tf 40 250 Td (Text above image) Tj ET\n"
        b"q 120 0 0 90 90 120 cm /Im1 Do Q\n"
        b"BT /F1 12 Tf 40 40 Td (Text below image) Tj ET\n"
    )
    add(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream")
    add(
        b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 "
        b"/ColorSpace /DeviceRGB /BitsPerComponent 8 "
        + f"/Length {len(pixels)} >>\nstream\n".encode()
        + pixels
        + b"\nendstream"
    )
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    # Assemble with xref
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode())
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(out))


def test_parse_pdf_extracts_image_to_run_workdir(tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    _build_pdf_with_image(pdf_path)

    run_id = "run_test_images_001"
    image_dir = resolve_run_image_dir(run_id, "doc:/sample.pdf")

    tree = parse_pdf_to_section_tree(
        str(pdf_path), doc_title="sample.pdf", image_dir=image_dir,
    )
    blocks = _collect_blocks(tree)
    types = [b.block_type for b in blocks]

    assert "image" in types
    assert types.count("image") == 1

    # Reading order: text above → image → text below
    img_i = types.index("image")
    texts = [(i, b) for i, b in enumerate(blocks) if b.block_type == "paragraph"]
    assert any("above" in b.text.lower() for _, b in texts)
    assert any("below" in b.text.lower() for _, b in texts)
    above_i = next(i for i, b in texts if "above" in b.text.lower())
    below_i = next(i for i, b in texts if "below" in b.text.lower())
    assert above_i < img_i < below_i

    image = blocks[img_i]
    assert image.text == ""
    assert image.structure is not None
    assert image.structure["kind"] == "pdf_image"
    assert image.structure["page"] == 1
    saved = Path(image.structure["image_path"])
    assert saved.is_file()
    assert saved.parent == image_dir
    assert saved.name.startswith("p1_")
    assert len(image.structure["image_sha256"]) == 64
    assert str(run_id) in str(saved)


def test_parse_pdf_without_image_dir_skips_images(tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    _build_pdf_with_image(pdf_path)

    tree = parse_pdf_to_section_tree(str(pdf_path), doc_title="sample.pdf")
    blocks = _collect_blocks(tree)
    assert all(b.block_type != "image" for b in blocks)
    assert any(b.block_type == "paragraph" for b in blocks)


def test_resolve_run_image_dir_is_run_scoped():
    p1 = resolve_run_image_dir("runA", "doc:/a.pdf")
    p2 = resolve_run_image_dir("runB", "doc:/a.pdf")
    assert "runA" in str(p1)
    assert "runB" in str(p2)
    assert p1 != p2
    assert p1.is_dir() and p2.is_dir()
