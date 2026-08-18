"""Structural Reconciler（C08 最小实现）测试 —— 整改轮，先 RED.

文档级跨元素/跨页规则从 adapter 迁入 Reconciler（用户指令）：
  R-1 家具标注：跨容器重复长行 -> page_header/page_footer（按垂直位置
      分侧）；纯数字/罗马短行 -> page_number。**只改类型与注记，不删除**。
  R-2 caption 绑定：caption 元素（HTML 产）或 "图N/表N/Fig.N" 前缀
      段落（PDF 形态）与相邻表格/图片 -> caption_of 关系 + 资产回填
      caption_element_id。
  R-3 跨页表格延续：相邻页、列数一致、表头相似 -> continuation_of。
  R-4 跨页段落延续：保守 continues_on 关系（不改写文本）。
  R-5 patch log：每次修复可追踪（规则名 + element id + 前后值），
      reconciler_version 回写 ParseIdentity。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.contracts.parse_ir import (
    Confidence,
    Container,
    Element,
    EvidenceSpan,
    ParseIdentity,
    ParsedDocument,
    Relation,
    TableAsset,
    TableCell,
)
from knowledge_mining.mining.parse_reconciler import (
    RECONCILER_VERSION,
    StructuralReconciler,
)

RAW_HASH = "c1" * 32


def _page_doc(elements_by_page: dict[int, list[Element]]) -> ParsedDocument:
    """按页构造 IR（page 容器 + 元素带 page_span）."""
    pages = sorted(elements_by_page)
    containers = tuple(
        Container(
            container_id=f"c-page-{i:04d}",
            container_type="page",
            order_index=i,
            page_number=i + 1,
            height=792.0,
        )
        for i in pages
    )
    elements: list[Element] = []
    for i in pages:
        for k, e in enumerate(elements_by_page[i]):
            elements.append(e)
    return ParsedDocument(
        schema_version="0.1",
        source_identity=ParseIdentity(source_raw_hash=RAW_HASH, parser_fingerprint="p"),
        containers=containers,
        elements=tuple(elements),
    )


def _para(text: str, page: int, top: float = 400.0) -> Element:
    return Element(
        element_id=f"e-{page}-{abs(hash((text, top))) % 100000}",
        element_type="paragraph",
        order_index=0,
        text=text,
        page_span_ids=(f"c-page-{page:04d}",),
        source_spans=(EvidenceSpan(
            span_id=f"s-{page}-{abs(hash(text)) % 99999}",
            page_id=f"c-page-{page:04d}",
            visual_region={"bbox": [72.0, top, 300.0, top + 12.0]},
        ),),
    )


def _table_el(
    page: int, table_id: str, header: tuple[str, ...], order: int = 0
) -> Element:
    eid = f"e-tbl-{table_id}"
    return Element(
        element_id=eid,
        element_type="table",
        order_index=order,
        text="\t".join(header),
        page_span_ids=(f"c-page-{page:04d}",),
    )


def _table_asset(element: Element, header: tuple[str, ...]) -> TableAsset:
    return TableAsset(
        table_id=f"{element.element_id}-table",
        page_span_ids=element.page_span_ids,
        rows=1,
        columns=len(header),
        cells=tuple(
            TableCell(row_index=0, column_index=c, text=t, is_header=True)
            for c, t in enumerate(header)
        ),
    )


# ---------------------------------------------------------------------------
# R-1 家具标注（从 native_pdf adapter 迁入）
# ---------------------------------------------------------------------------


def test_repeated_header_line_retyped_as_page_header() -> None:
    doc = _page_doc({
        0: [_para("某大学硕士学位论文重复页眉文字", 0, top=30)],
        1: [_para("某大学硕士学位论文重复页眉文字", 1, top=30)],
        2: [_para("某大学硕士学位论文重复页眉文字", 2, top=30)],
        3: [_para("正文内容第一章。", 3)],
    })
    result = StructuralReconciler().reconcile(doc)
    by_text = {e.text: e.element_type for e in result.document.elements}
    assert by_text["某大学硕士学位论文重复页眉文字"] == "page_header"
    assert by_text["正文内容第一章。"] == "paragraph"  # 不误伤
    assert any(p.rule == "furniture_typing" for p in result.patches)


def test_repeated_footer_line_retyped_as_page_footer() -> None:
    doc = _page_doc({
        i: [_para("Footer repeated across pages here", i, top=760)]
        for i in range(4)
    })
    result = StructuralReconciler().reconcile(doc)
    assert all(
        e.element_type == "page_footer"
        for e in result.document.elements
    )


def test_pure_page_numbers_retyped() -> None:
    doc = _page_doc({
        0: [_para("1", 0, top=780)],
        1: [_para("2", 1, top=780)],
        2: [_para("3", 2, top=780)],
    })
    result = StructuralReconciler().reconcile(doc)
    assert all(e.element_type == "page_number" for e in result.document.elements)


# ---------------------------------------------------------------------------
# R-2 caption 绑定
# ---------------------------------------------------------------------------


def test_caption_element_binds_to_adjacent_table() -> None:
    caption = Element(
        element_id="e-cap",
        element_type="caption",
        order_index=0,
        text="表 2-1 催化剂性能对比",
        page_span_ids=("c-page-0000",),
    )
    table = _table_el(0, "t1", ("样品", "活性"), order=1)
    doc = ParsedDocument(
        schema_version="0.1",
        source_identity=ParseIdentity(source_raw_hash=RAW_HASH, parser_fingerprint="p"),
        containers=(Container(
            container_id="c-page-0000", container_type="page",
            order_index=0, page_number=1,
        ),),
        elements=(caption, table),
        structured_assets={f"{table.element_id}-table": _table_asset(
            table, ("样品", "活性")
        )},
    )
    result = StructuralReconciler().reconcile(doc)
    asset = result.document.structured_assets[f"{table.element_id}-table"]
    assert asset.caption_element_id == "e-cap"
    assert any(
        r.relation_type == "caption_of"
        and r.source_element_id == "e-cap"
        and r.target_element_id == table.element_id
        for r in result.document.relations
    )


def test_pdf_style_caption_prefix_paragraph_binds() -> None:
    """PDF 形态：无 caption 元素，"表 N-" 前缀段落紧邻表格 -> 升 caption 绑定."""
    para = _para("表 3-1 实验参数设置", 0, top=300)
    table = _table_el(0, "t2", ("参数", "值"), order=1)
    doc = ParsedDocument(
        schema_version="0.1",
        source_identity=ParseIdentity(source_raw_hash=RAW_HASH, parser_fingerprint="p"),
        containers=(Container(
            container_id="c-page-0000", container_type="page",
            order_index=0, page_number=1,
        ),),
        elements=(para, table),
        structured_assets={f"{table.element_id}-table": _table_asset(
            table, ("参数", "值")
        )},
    )
    result = StructuralReconciler().reconcile(doc)
    retyped = next(e for e in result.document.elements if e.text.startswith("表 3-1"))
    assert retyped.element_type == "caption"
    asset = result.document.structured_assets[f"{table.element_id}-table"]
    assert asset.caption_element_id == retyped.element_id


# ---------------------------------------------------------------------------
# R-3 跨页表格延续
# ---------------------------------------------------------------------------


def test_cross_page_table_continuation() -> None:
    t1 = _table_el(0, "a", ("编号", "名称", "值"), order=0)
    t2 = _table_el(1, "b", ("编号", "名称", "值"), order=1)
    doc = ParsedDocument(
        schema_version="0.1",
        source_identity=ParseIdentity(source_raw_hash=RAW_HASH, parser_fingerprint="p"),
        containers=(
            Container(container_id="c-page-0000", container_type="page",
                      order_index=0, page_number=1),
            Container(container_id="c-page-0001", container_type="page",
                      order_index=1, page_number=2),
        ),
        elements=(t1, t2),
        structured_assets={
            f"{t1.element_id}-table": _table_asset(t1, ("编号", "名称", "值")),
            f"{t2.element_id}-table": _table_asset(t2, ("编号", "名称", "值")),
        },
    )
    result = StructuralReconciler().reconcile(doc)
    asset2 = result.document.structured_assets[f"{t2.element_id}-table"]
    assert asset2.continuation_of == f"{t1.element_id}-table"
    assert any(
        r.relation_type == "continues_on"
        and r.source_element_id == t1.element_id
        and r.target_element_id == t2.element_id
        for r in result.document.relations
    )


def test_different_header_not_continuation() -> None:
    t1 = _table_el(0, "a", ("编号", "名称"), order=0)
    t2 = _table_el(1, "b", ("温度", "压力"), order=1)
    doc = ParsedDocument(
        schema_version="0.1",
        source_identity=ParseIdentity(source_raw_hash=RAW_HASH, parser_fingerprint="p"),
        containers=(
            Container(container_id="c-page-0000", container_type="page",
                      order_index=0, page_number=1),
            Container(container_id="c-page-0001", container_type="page",
                      order_index=1, page_number=2),
        ),
        elements=(t1, t2),
        structured_assets={
            f"{t1.element_id}-table": _table_asset(t1, ("编号", "名称")),
            f"{t2.element_id}-table": _table_asset(t2, ("温度", "压力")),
        },
    )
    result = StructuralReconciler().reconcile(doc)
    asset2 = result.document.structured_assets[f"{t2.element_id}-table"]
    assert asset2.continuation_of is None


# ---------------------------------------------------------------------------
# R-4 跨页段落延续（保守，只加关系不改文本）
# ---------------------------------------------------------------------------


def test_cross_page_paragraph_continuation_relation() -> None:
    p1 = _para("这是一段没有被句号打断而是延续到下一页的长段落前半部分",
               0, top=760)
    p2 = _para("的后半部分，继续同一段叙述。", 1, top=40)
    doc = _page_doc({0: [p1], 1: [p2]})
    result = StructuralReconciler().reconcile(doc)
    assert any(
        r.relation_type == "continues_on"
        and r.source_element_id == p1.element_id
        and r.target_element_id == p2.element_id
        for r in result.document.relations
    )
    # 文本不被改写
    e1 = next(e for e in result.document.elements if e.element_id == p1.element_id)
    assert e1.text.endswith("前半部分")


def test_terminal_punctuation_blocks_continuation() -> None:
    p1 = _para("上一页以句号结束的完整段落。", 0, top=760)
    p2 = _para("下一页的新段落从这里开始。", 1, top=40)
    doc = _page_doc({0: [p1], 1: [p2]})
    result = StructuralReconciler().reconcile(doc)
    assert not any(
        r.relation_type == "continues_on"
        for r in result.document.relations
    )


# ---------------------------------------------------------------------------
# R-5 patch log + reconciler_version
# ---------------------------------------------------------------------------


def test_reconciler_version_written_back() -> None:
    doc = _page_doc({0: [_para("正文段落。", 0)]})
    result = StructuralReconciler().reconcile(doc)
    assert result.document.source_identity.reconciler_version == RECONCILER_VERSION
    assert result.document.diagnostics.backend_provenance.get("reconciler") == (
        RECONCILER_VERSION
    )


def test_patch_records_track_rule_and_elements() -> None:
    doc = _page_doc({
        i: [_para("跨页重复页眉样例文字超过十二个字符", i, top=30)]
        for i in range(3)
    })
    result = StructuralReconciler().reconcile(doc)
    assert result.patches
    for patch in result.patches:
        assert patch.rule
        assert patch.element_ids
