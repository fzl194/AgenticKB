"""``NativeHtmlParser`` + ``HtmlNormalizer`` 单元测试（M3, SRS §C06/§C07）.

fixture 为手写 HTML 字符串 bytes。覆盖：title 元素、h1/h2 父链、
表格 rowspan/colspan、列表、img -> figure（alt/src）、pre -> code、
xpath 证据、单一 dom_document 容器、IR 断言链、错误归一。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.contracts.parse_ir import validate
from knowledge_mining.mining.contracts.parser_adapter import (
    ParserAdapterError,
    UnsupportedFormat,
)
from knowledge_mining.mining.parse_adapters.native.native_html import (
    NATIVE_HTML_FINGERPRINT,
    HtmlNormalizer,
    NativeHtmlParser,
)

HTML_MIME = "text/html"
RAW_HASH = "77" * 32

HTML_SAMPLE = (
    "<!DOCTYPE html>\n"
    "<html><head><title>示例页面</title></head>\n"
    "<body><div id=\"main\">\n"
    "<h1>主标题</h1>\n"
    "<p>导言<b>加粗</b>段落。</p>\n"
    "<h2>小节</h2>\n"
    "<ul><li>条目一</li><li>条目二</li></ul>\n"
    "<table>\n"
    "<tr><td colspan=\"2\">宽单元格</td><td>表头C</td></tr>\n"
    "<tr><td rowspan=\"2\">高单元格</td><td>b1</td><td>b2</td></tr>\n"
    "<tr><td>c1</td><td>c2</td></tr>\n"
    "</table>\n"
    "<img src=\"logo.png\" alt=\"标志\"/>\n"
    "<pre>print(\"hi\")</pre>\n"
    "</div></body></html>\n"
)


@pytest.fixture
def parser() -> NativeHtmlParser:
    return NativeHtmlParser()


@pytest.fixture
def normalizer() -> HtmlNormalizer:
    return HtmlNormalizer()


def _normalize(parser, normalizer, data: bytes):
    artifact = parser.parse(data, mime=HTML_MIME)
    return normalizer.normalize(artifact, source_raw_hash=RAW_HASH)


# ---------------------------------------------------------------------------
# RED 1: parse 层
# ---------------------------------------------------------------------------

def test_parse_block_types_in_document_order(parser) -> None:
    artifact = parser.parse(HTML_SAMPLE.encode("utf-8"), mime=HTML_MIME)

    assert artifact.parser_id == "native_html"
    assert [b.block_type for b in artifact.blocks] == [
        "title", "heading", "paragraph", "heading",
        "list_item", "list_item", "table", "figure", "code",
    ]
    headings = [b for b in artifact.blocks if b.block_type == "heading"]
    assert [b.level for b in headings] == [1, 2]
    assert [b.level for b in artifact.blocks if b.block_type == "list_item"] == [1, 1]
    # inline 标签文本收拢到段落
    para = next(b for b in artifact.blocks if b.block_type == "paragraph")
    assert para.text == "导言加粗段落。"


def test_parse_unsupported_mime_and_empty_bytes(parser) -> None:
    with pytest.raises(UnsupportedFormat):
        parser.parse(b"x", mime="text/plain")
    with pytest.raises(ParserAdapterError):
        parser.parse(b"", mime=HTML_MIME)


# ---------------------------------------------------------------------------
# RED 2: IR 断言链
# ---------------------------------------------------------------------------

def test_dom_container_title_and_heading_chain(parser, normalizer) -> None:
    doc = _normalize(parser, normalizer, HTML_SAMPLE.encode("utf-8"))
    assert validate(doc).valid

    assert [c.container_type for c in doc.containers] == ["dom_document"]
    assert doc.containers[0].page_number is None

    title = doc.elements[0]
    assert title.element_type == "title"
    assert title.text == "示例页面"

    h1 = next(e for e in doc.elements if e.text == "主标题")
    h2 = next(e for e in doc.elements if e.text == "小节")
    assert h1.parent_id is None
    assert h2.parent_id == h1.element_id
    para = next(e for e in doc.elements if e.element_type == "paragraph")
    assert para.parent_id == h1.element_id
    items = [e for e in doc.elements if e.element_type == "list_item"]
    assert [e.text for e in items] == ["条目一", "条目二"]
    assert all(e.parent_id == h2.element_id for e in items)


def test_table_rowspan_colspan(parser, normalizer) -> None:
    doc = _normalize(parser, normalizer, HTML_SAMPLE.encode("utf-8"))

    table_el = next(e for e in doc.elements if e.element_type == "table")
    asset = doc.structured_assets[f"{table_el.element_id}-table"]
    assert asset.rows == 3
    assert asset.columns == 3
    grid = {(c.row_index, c.column_index): c for c in asset.cells}

    assert grid[(0, 0)].text == "宽单元格"
    assert grid[(0, 0)].column_span == 2
    assert grid[(0, 0)].row_span == 1
    assert (0, 1) not in grid
    assert grid[(1, 0)].text == "高单元格"
    assert grid[(1, 0)].row_span == 2
    assert (2, 0) not in grid
    assert grid[(2, 2)].text == "c2"
    # 首行 is_header 约定
    assert grid[(0, 0)].is_header and not grid[(1, 1)].is_header


def test_figure_and_code_elements(parser, normalizer) -> None:
    doc = _normalize(parser, normalizer, HTML_SAMPLE.encode("utf-8"))

    figure = next(e for e in doc.elements if e.element_type == "figure")
    assert figure.parser_annotations["src"] == "logo.png"
    assert figure.parser_annotations["alt"] == "标志"

    code = next(e for e in doc.elements if e.element_type == "code")
    assert code.text == 'print("hi")'


def test_xpath_native_ref_evidence(parser, normalizer) -> None:
    doc = _normalize(parser, normalizer, HTML_SAMPLE.encode("utf-8"))

    for el in doc.elements:
        span = el.source_spans[0]
        assert span.native_ref is not None
        assert span.native_ref["xpath"].startswith("/html/")
    h1 = next(e for e in doc.elements if e.text == "主标题")
    assert h1.source_spans[0].native_ref["xpath"] == "/html/body/div/h1"
    items = [e for e in doc.elements if e.element_type == "list_item"]
    assert items[0].source_spans[0].native_ref["xpath"] == (
        "/html/body/div/ul/li[1]"
    )


def test_ir_chain_relations_stable_ids_fingerprint(parser, normalizer) -> None:
    data = HTML_SAMPLE.encode("utf-8")
    doc1 = _normalize(parser, normalizer, data)
    doc2 = _normalize(parser, normalizer, data)
    assert validate(doc1).valid

    assert [e.element_id for e in doc1.elements] == [
        e.element_id for e in doc2.elements
    ]
    reading = [
        r for r in doc1.relations if r.relation_type == "next_in_reading_order"
    ]
    assert len(reading) == len(doc1.elements) - 1
    assert doc1.source_identity.parser_fingerprint == NATIVE_HTML_FINGERPRINT
    assert doc1.source_identity.parser_fingerprint == (
        "native_html@1.0.0#lxml-5.2.1"
    )


def test_malicious_huge_rowspan_clamped() -> None:
    """评审 HIGH-2 回归：rowspan='2000000000' 不再撑爆网格（截断+可见）."""
    import time
    from knowledge_mining.mining.parse_adapters.native.native_html import (
        NativeHtmlParser,
    )

    html = (
        "<html><body><table>"
        "<tr><td rowspan='2000000000'>x</td><td>y</td></tr>"
        "<tr><td>z</td></tr>"
        "</table></body></html>"
    ).encode("utf-8")
    parser = NativeHtmlParser()
    start = time.monotonic()
    artifact = parser.parse(html, mime="text/html")
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"parse took {elapsed:.2f}s (DoS surface open)"
    tables = [b for b in artifact.blocks if b.block_type == "table"]
    assert tables[0].structure["rows"] <= 10
    assert tables[0].structure.get("clamped_spans") == 1


# ===========================================================================
# 整改轮（2026-08-17）：嵌套列表 / 链接 / caption / 语义容器 / 面积上限
# ===========================================================================


def test_nested_list_items_are_separate_elements() -> None:
    """嵌套列表：父 li 直属文本独立，子 li 各自成元素（层级递增）."""
    html = (
        "<html><body><ul>"
        "<li>水果"
        "<ul><li>苹果</li><li>香蕉</li></ul>"
        "</li>"
        "<li>蔬菜</li>"
        "</ul></body></html>"
    ).encode("utf-8")
    artifact = NativeHtmlParser().parse(html, mime="text/html")
    items = [(b.text, b.level) for b in artifact.blocks if b.block_type == "list_item"]
    texts = [t for t, _ in items]
    # 父项文本只含直属文本，不吸收子项
    assert "水果" in texts and "苹果" not in ("水果",)
    # 子项独立成块且层级更深
    assert ("苹果", 2) in items
    assert ("香蕉", 2) in items
    assert any(t == "水果" and lvl == 1 for t, lvl in items)
    assert ("蔬菜", 1) in items


def test_links_are_recorded_in_annotations() -> None:
    from knowledge_mining.mining.parse_adapters.native.native_html import (
        HtmlNormalizer,
    )

    html = (
        b"<html><body><p>See <a href='https://e.x/doc'>the doc</a> now.</p>"
        b"<a href='https://e.x/bare'>bare</a></body></html>"
    )
    doc = HtmlNormalizer().normalize(
        NativeHtmlParser().parse(html, mime="text/html"),
        source_raw_hash="77" * 32,
    )
    para = next(e for e in doc.elements if "the doc" in e.text)
    links = para.parser_annotations.get("links")
    assert links == [{"text": "the doc", "href": "https://e.x/doc"}]


def test_figcaption_and_table_caption_become_caption_elements() -> None:
    html = (
        "<html><body>"
        "<figure><img src='f.png' alt='图'><figcaption>图一说明</figcaption></figure>"
        "<table><caption>表一标题</caption>"
        "<tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>"
        "</body></html>".encode("utf-8")
    )
    artifact = NativeHtmlParser().parse(html, mime="text/html")
    kinds = [(b.block_type, b.text) for b in artifact.blocks]
    assert ("caption", "图一说明") in kinds
    assert ("caption", "表一标题") in kinds


def test_semantic_container_path_recorded() -> None:
    from knowledge_mining.mining.parse_adapters.native.native_html import (
        HtmlNormalizer,
    )

    html = (
        "<html><body><article><section><h1>标题</h1>"
        "<p>正文。</p></section></article></body></html>".encode("utf-8")
    )
    doc = HtmlNormalizer().normalize(
        NativeHtmlParser().parse(html, mime="text/html"),
        source_raw_hash="66" * 32,
    )
    para = next(e for e in doc.elements if e.element_type == "paragraph")
    assert para.metadata.get("semantic_path") == ["article", "section"]


def test_rowspan_colspan_area_cap_prevents_memory_dos() -> None:
    """rowspan=9999 colspan=9999 -> 面积超限截断，occupied 不被撑爆."""
    import time

    html = (
        b"<html><body><table>"
        b"<tr><td rowspan='9999' colspan='9999'>big</td></tr>"
        b"<tr><td>x</td></tr>"
        b"</table></body></html>"
    )
    start = time.monotonic()
    artifact = NativeHtmlParser().parse(html, mime="text/html")
    elapsed = time.monotonic() - start
    assert elapsed < 5.0, f"area bomb took {elapsed:.2f}s"
    st = next(
        b.structure for b in artifact.blocks if b.block_type == "table"
    )
    assert st.get("clamped_spans", 0) >= 1


def test_images_are_diagnosed() -> None:
    html = b"<html><body><p>x</p><img src='a.png'><img src='b.png'></body></html>"
    artifact = NativeHtmlParser().parse(html, mime="text/html")
    joined = "\n".join(artifact.warnings).lower()
    assert "image" in joined, f"图片静默丢失: {joined!r}"


def test_table_cells_have_independent_spans() -> None:
    from knowledge_mining.mining.parse_adapters.native.native_html import (
        HtmlNormalizer,
    )

    html = (
        "<html><body><table>"
        "<tr><th>表头A</th><th>表头B</th></tr>"
        "<tr><td>a</td><td>b</td></tr></table></body></html>".encode("utf-8")
    )
    doc = HtmlNormalizer().normalize(
        NativeHtmlParser().parse(html, mime="text/html"),
        source_raw_hash="55" * 32,
    )
    table = next(e for e in doc.elements if e.element_type == "table")
    asset = doc.structured_assets[f"{table.element_id}-table"]
    span_by_id = {s.span_id: s for s in table.source_spans}
    seen = set()
    for cell in asset.cells:
        assert cell.source_span_id is not None
        span = span_by_id[cell.source_span_id]
        assert "xpath" in span.native_ref or "row_index" in span.native_ref
        assert cell.source_span_id not in seen
        seen.add(cell.source_span_id)
