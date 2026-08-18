"""PDF 结构性缺陷回归（整改轮，用户指令第 3 步 PDF 项）.

覆盖四个已知缺陷（全部先 RED）：
  P-1 双栏跨栏粘连：左栏行略过中线被误判"通栏行"，打乱左右栏阅读序；
  P-2 标题误杀（数字开头）："3D 打印…" 类真标题被 digit-leading 防御
      一刀切杀死；
  P-3 标题误杀（跨栏同带）：左栏标题与右栏正文同 top，dense_frags 用
      全页字符计算把标题当图内刻度杀死；
  P-4 表格 bbox/cell 不一致：数据核心收缩后 bbox 已收缩但 cell 仍含
      收缩框外的行（标题/正文混进表格资产）。

fixture：复用最小 PDF 构造器（与 test_native_pdf 相同模板），文本按
精确坐标放置。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.parse_adapters.factory import resolve_pipeline
from knowledge_mining.mining.parse_adapters.rendered_text import render_table_text

PDF_MIME = "application/pdf"
RAW_HASH = "5a" * 32


def _build_pdf(objects):
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    n = len(objects) + 1
    out += f"xref\n0 {n}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {n} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _stream(content: str) -> bytes:
    return f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode()


def _pages(contents: list[str]) -> bytes:
    """多页文档（每页一个 content stream）."""
    n_pages = len(contents)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode(),
    ]
    for i, content in enumerate(contents):
        page_num = 3 + 2 * i
        stream_num = page_num + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 4 0 R >> >> /Contents "
            f"{stream_num} 0 R >>".encode()
        )
        objects.append(_stream(content))
    # 对象编号：1 catalog, 2 pages, 3=font? —— 模板里 font 固定 4 0 R，
    # 但 3 已是第一页。改用：1 catalog, 2 pages, 3..(3+2n) pages/streams,
    # font 放最后并改引用。为简单起见单页/双页场景直接用下方 _page()。
    raise RuntimeError("use _page instead")


def _page(content: str) -> bytes:
    return _build_pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        _stream(content),
    ])


def _t(size: float, x: float, y: float, text: str) -> str:
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return f"BT /F1 {size} Tf {x} {y} Td ({escaped}) Tj ET"


def _run(data: bytes):
    parser, normalizer = resolve_pipeline("native_pdf")
    artifact = parser.parse(data, mime=PDF_MIME)
    doc = normalizer.normalize(artifact, source_raw_hash=RAW_HASH)
    return artifact, doc


# ---------------------------------------------------------------------------
# P-1 双栏跨栏粘连
# ---------------------------------------------------------------------------


def test_left_column_line_past_mid_stays_left() -> None:
    """左栏长行（x1 越过中线但未跨沟）不得被当通栏锚，乱序右栏."""
    lines = [
        _t(10, 72, 700, "L1 first line"),
        _t(10, 72, 680, "L2 long line that extends past mid"),  # x1 ≈ 340 > 306
        _t(10, 72, 660, "L3 tail"),
        _t(10, 72, 640, "L4 more tail"),
        _t(10, 350, 700, "R1 alpha"),
        _t(10, 350, 680, "R2 beta"),
        _t(10, 350, 660, "R3 gamma"),
        _t(10, 350, 640, "R4 delta"),
    ]
    _, doc = _run(_page("\n".join(lines)))

    # 段落聚合可能把同栏行合为一段；阅读序在文本流上必须保持
    # 全部 L* 先于全部 R*（粘连时 R1 会插进 L2 之前）。
    joined = " ".join(
        e.text for e in doc.elements if e.element_type == "paragraph"
    )
    positions = {tag: joined.find(f"{tag} ") for tag in
                 ("L1", "L2", "L3", "L4", "R1", "R2", "R3", "R4")}
    assert all(p >= 0 for p in positions.values()), joined
    assert max(
        positions[f"L{i}"] for i in (1, 2, 3, 4)
    ) < min(
        positions[f"R{i}"] for i in (1, 2, 3, 4)
    ), f"跨栏粘连: {joined}"


# ---------------------------------------------------------------------------
# P-2 数字开头标题误杀
# ---------------------------------------------------------------------------


def test_digit_prefixed_heading_survives() -> None:
    """数字开头的真标题（"3D Printing Technology"）不被轴刻度防御误杀.

    注：最小 PDF 构造器只注册 Helvetica（无 CJK 字形），中文标题无法
    在 fixture 中渲染；数字前缀 + 多词大写形态与 CJK 场景同路径。
    """
    lines = [
        _t(16, 72, 700, "3D Printing Technology"),
        _t(10, 72, 660, "Body line one for modal size."),
        _t(10, 72, 645, "Body line two for modal size."),
    ]
    _, doc = _run(_page("\n".join(lines)))
    headings = [e for e in doc.elements if e.element_type == "heading"]
    assert any("3D" in e.text for e in headings), (
        f"数字开头真标题被误杀: {[e.text for e in doc.elements]}"
    )


def test_axis_label_digit_noise_still_rejected() -> None:
    """防御不回退：纯数字+短乱序词的坐标轴刻度仍不是标题."""
    lines = [
        _t(9, 72, 700, "正文行内容较长以确保字号众数来自正文。"),
        _t(11, 300, 400, "0.4 nile"),
        _t(11, 300, 380, "0.1 itci"),
    ]
    _, doc = _run(_page("\n".join(lines)))
    headings = [e for e in doc.elements if e.element_type == "heading"]
    assert not any("nile" in e.text or "itci" in e.text for e in headings)


# ---------------------------------------------------------------------------
# P-3 跨栏同带标题误杀（dense_frugs 用全页字符）
# ---------------------------------------------------------------------------


def test_heading_beside_right_column_body_survives() -> None:
    """左栏大字标题与右栏正文同 top：dense_frags 只能用行内字符.

    （fixture 为 Helvetica/英文形态；dense_frags 的 x 范围约束与
    CJK 场景同路径。）
    """
    lines = [
        _t(16, 72, 700, "Methods Overview"),
        _t(10, 350, 700, "Right column body text here"),
        _t(10, 350, 680, "More right column text"),
        _t(10, 72, 660, "Left column body line."),
    ]
    _, doc = _run(_page("\n".join(lines)))
    headings = [e for e in doc.elements if e.element_type == "heading"]
    assert any("Methods" in e.text for e in headings), (
        f"跨栏同带标题被误杀: {[(e.element_type, e.text) for e in doc.elements]}"
    )


# ---------------------------------------------------------------------------
# P-4 表格 bbox / cell 内容一致
# ---------------------------------------------------------------------------


def _trap_page() -> bytes:
    """上下框陷阱：框内上部为标题行（单片段），下部为 2 列数据."""
    content = "\n".join([
        _t(12, 80, 690, "Trap Title"),
        _t(10, 80, 660, "HeadA"),
        _t(10, 180, 660, "HeadB"),
        _t(10, 80, 630, "a"),
        _t(10, 180, 630, "b"),
        _t(10, 80, 600, "c"),
        _t(10, 180, 600, "d"),
        "0 w",
        "72 590 m 272 590 l S",
        "72 700 m 272 700 l S",
    ])
    return _page(content)


def test_table_bbox_and_cells_are_consistent() -> None:
    _, doc = _run(_trap_page())
    tables = [
        (e, doc.structured_assets[f"{e.element_id}-table"])
        for e in doc.elements if e.element_type == "table"
    ]
    assert tables, "表格未识别"
    element, asset = tables[0]
    span = element.source_spans[0]
    bbox = span.visual_region["bbox"]
    # 收缩后 bbox 顶边应在数据首行（HeadA, top≈125-135）之下，
    # 即不包含 690 的标题行（top≈95）
    assert bbox[1] > 110, f"bbox 未收缩到数据核心: {bbox}"
    # cell 内容与 bbox 一致：收缩框外的行（Trap Title）不得残留在资产里
    cell_texts = [c.text for c in asset.cells]
    assert "Trap Title" not in cell_texts, f"cell 与 bbox 不一致: {cell_texts}"
    assert "HeadA" in cell_texts and "d" in cell_texts
    # rows 与保留的行一致（HeadA/a/c 三行）
    assert asset.rows == 3, f"rows={asset.rows}, cells={cell_texts}"


def test_table_element_text_derivable_from_asset() -> None:
    _, doc = _run(_trap_page())
    element = next(e for e in doc.elements if e.element_type == "table")
    asset = doc.structured_assets[f"{e.element_id if False else element.element_id}-table"]
    assert element.text == render_table_text(asset)
    assert "HeadA" in element.text
    assert "Trap Title" not in element.text


def test_table_cells_have_bbox_evidence_spans() -> None:
    _, doc = _run(_trap_page())
    element = next(e for e in doc.elements if e.element_type == "table")
    asset = doc.structured_assets[f"{element.element_id}-table"]
    span_by_id = {s.span_id: s for s in element.source_spans}
    seen = set()
    for cell in asset.cells:
        if not cell.text:
            continue
        assert cell.source_span_id is not None, "cell 无独立证据"
        span = span_by_id[cell.source_span_id]
        assert span.visual_region and "bbox" in span.visual_region
        assert cell.source_span_id not in seen
        seen.add(cell.source_span_id)
