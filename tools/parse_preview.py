"""解析效果预览器（M3 验收工具）——纯本地，不碰 MinIO/PG/pipeline.

用法：
    python tools/parse_preview.py <文档路径> [-o 输出.html]

链路（与生产影子链路同一套适配器，但不写任何存储）：
    读文件 bytes → FileInspector 探测 → ParserRouter 路由 → 适配器解析
    → Normalizer 产 Parse IR → validate → 渲染 HTML 报告

报告 v2（结构化信息可见化）：
    - 左栏：解析出的标题树（目录），点击跳转
    - 概要：路由/容器/元素构成/关系数/表格数/IR 校验
    - 主区结构视图：每个元素一行 = 类型徽章 + 内容 + 证据定位（页码/坐标），
      家具（页眉/页码）折叠计数，表格渲染为网格（合并格跨行跨列）
    - 尾部：TableAsset JSON 样例（展示下游消费的结构化数据形态）
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from knowledge_mining.mining.contracts.parse_ir.schema import validate
from knowledge_mining.mining.contracts.parse_ir.types import ParsedDocument
from knowledge_mining.mining.file_inspector.inspect import FileInspector
from knowledge_mining.mining.file_inspector.router import ParserRouter
from knowledge_mining.mining.parse_adapters.factory import resolve_pipeline
from knowledge_mining.mining.parse_adapters.registry import build_default_registry


def parse_document(data: bytes, declared_mime: str | None = None):
    """走完整 探测→路由→解析→IR 链路，返回 (profile, decision, doc)。"""
    profile = FileInspector().inspect(data, declared_mime=declared_mime)
    decision = ParserRouter(build_default_registry()).plan(profile)
    if decision.primary_parser_id is None:
        raise SystemExit(
            f"无法路由该文档：source_format={profile.source_format!r}，"
            f"reason={decision.reason_codes}"
        )
    pair = resolve_pipeline(decision.primary_parser_id)
    if pair is None:
        raise SystemExit(f"parser {decision.primary_parser_id} 无实现")
    parser, normalizer = pair
    mime = declared_mime or profile.detected_mime
    artifact = parser.parse(data, mime=mime)
    doc = normalizer.normalize(
        artifact, source_raw_hash=f"preview:{profile.source_format}"
    )
    return profile, decision, doc


# ---------------------------------------------------------------------------
# HTML 渲染（v2：结构化信息可见化）
# ---------------------------------------------------------------------------

_CSS = """
body { font-family: "Microsoft YaHei", sans-serif; margin: 0; color: #1f2937; }
.layout { display: flex; min-height: 100vh; }
.toc { width: 320px; background: #f8fafc; border-right: 1px solid #e2e8f0;
       padding: 20px 14px; position: sticky; top: 0; overflow-y: auto;
       max-height: 100vh; font-size: 0.85em; }
.toc a { color: #334155; text-decoration: none; display: block; padding: 2px 6px;
         border-radius: 4px; }
.toc a:hover { background: #e0e7ff; }
.main { flex: 1; padding: 24px 40px; max-width: 920px; }
h1 { border-bottom: 3px solid #2563eb; padding-bottom: 8px; }
h2 { border-left: 4px solid #2563eb; padding-left: 10px; margin-top: 36px; }
table.meta td { padding: 4px 12px; }
table.meta td:first-child { color: #6b7280; white-space: nowrap; }
.badge { display: inline-block; font-size: 0.72em; border-radius: 10px;
         padding: 1px 8px; margin-right: 6px; font-family: Consolas, monospace; }
.b-heading { background: #dbeafe; color: #1d4ed8; }
.b-paragraph { background: #dcfce7; color: #15803d; }
.b-table { background: #fef3c7; color: #b45309; }
.b-furniture { background: #f1f5f9; color: #94a3b8; }
.b-unknown, .b-figure, .b-list_item, .b-code, .b-quote { background: #ede9fe; color: #6d28d9; }
.low-conf { background: #fef9c3; border-radius: 4px; padding: 1px 6px; font-size: 0.75em; }
.ev { color: #9ca3af; font-size: 0.75em; margin-left: 8px; font-family: Consolas, monospace; }
.el { margin: 5px 0; padding: 4px 10px; border-radius: 6px; }
.el-heading { background: #eff6ff; font-weight: 600; }
.el-paragraph { background: #ffffff; }
.el-table { background: #fffbeb; }
.el-furniture { background: #f8fafc; color: #94a3b8; font-size: 0.85em; }
table.grid { border-collapse: collapse; margin: 6px 0 6px 20px; }
table.grid td, table.grid th { border: 1px solid #d1d5db; padding: 4px 10px; font-size: 0.88em; }
table.grid th { background: #f3f4f6; }
.warn { color: #b45309; }
.container-head { color: #2563eb; font-weight: 600; margin-top: 18px;
                  border-bottom: 1px dashed #cbd5e1; padding-bottom: 4px; }
pre.ir { background: #0f172a; color: #e2e8f0; padding: 16px; border-radius: 8px;
         overflow-x: auto; font-size: 0.8em; }
"""

_FURNITURE = ("page_header", "page_footer", "page_number")


def _e(text: str) -> str:
    return html.escape(text or "")


def _ev_tag(element) -> str:
    for span in element.source_spans:
        if span.visual_region:
            vr = span.visual_region
            pg, bbox = vr.get("page_index"), vr.get("bbox")
            if pg is not None and bbox:
                coords = ",".join(str(round(v)) for v in bbox)
                return f'<span class="ev">p{pg + 1} · bbox({coords})</span>'
        if span.source_locator:
            return f'<span class="ev">{_e(str(span.source_locator)[:60])}</span>'
        if span.native_ref:
            return f'<span class="ev">{_e(str(span.native_ref)[:60])}</span>'
    return ""


def _badge(t: str) -> str:
    cls = "b-furniture" if t in _FURNITURE else f"b-{t}"
    return f'<span class="badge {cls}">{t}</span>'


def _render_table_asset(asset) -> str:
    grid, span_map = {}, {}
    for cell in asset.cells:
        span_map[(cell.row_index, cell.column_index)] = (cell.row_span, cell.column_span)
        grid[(cell.row_index, cell.column_index)] = _e(cell.text)
    if not grid:
        return ""
    max_r = max(r for r, _ in grid) + 1
    max_c = max(c for _, c in grid) + 1
    header_cells = {(c.row_index, c.column_index) for c in asset.cells if c.is_header}
    covered: set = set()
    rows_html = []
    for r in range(max_r):
        cells_html = []
        for c in range(max_c):
            if (r, c) in covered or (r, c) not in grid:
                continue
            rs, cs = span_map.get((r, c), (1, 1))
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    covered.add((rr, cc))
            attrs = (f' rowspan="{rs}"' if rs > 1 else "") + (f' colspan="{cs}"' if cs > 1 else "")
            tag = "th" if (r, c) in header_cells else "td"
            cells_html.append(f"<{tag}{attrs}>{grid[(r, c)]}</{tag}>")
        if cells_html:
            rows_html.append("<tr>" + "".join(cells_html) + "</tr>")
    note = f'<div class="ev">{asset.rows}行×{asset.columns}列' + (
        f" · 续表→{_e(asset.continuation_of)}" if asset.continuation_of else "") + "</div>"
    return f'<table class="grid">{"".join(rows_html)}</table>{note}'


def _heading_depth(doc: ParsedDocument, element) -> int:
    by_id = {e.element_id: e for e in doc.elements}
    d, node = 0, element
    while node.parent_id and node.parent_id in by_id:
        d += 1
        node = by_id[node.parent_id]
    return d


def _toc_html(doc: ParsedDocument) -> str:
    items = []
    for e in doc.elements:
        if e.element_type != "heading":
            continue
        d = min(_heading_depth(doc, e), 5)
        items.append(
            f'<div style="margin-left:{d * 12}px"><a href="#el-{e.element_id}">'
            f"{_e(e.text[:40])}</a></div>"
        )
    return "\n".join(items) if items else "<div>(无标题)</div>"


def _structure_html(doc: ParsedDocument) -> str:
    by_container: dict[str | None, list] = {}
    order: list[str | None] = []
    for element in doc.elements:
        cid = element.page_span_ids[0] if element.page_span_ids else None
        if cid not in by_container:
            by_container[cid] = []
            order.append(cid)
        by_container[cid].append(element)
    containers_by_id = {c.container_id: c for c in doc.containers}
    table_assets = [a for a in doc.structured_assets.values() if hasattr(a, "rows")]
    table_iter = iter(table_assets)

    parts = []
    for cid in order:
        meta = containers_by_id.get(cid) if cid else None
        if meta:
            title = f"{meta.container_type}" + (
                f" #{meta.page_number}" if meta.page_number else "")
            if meta.name:
                title += f" · {_e(meta.name)}"
        else:
            title = "文档"
        parts.append(f'<div class="container-head">{title}</div>')
        elements = by_container[cid]
        furniture = [e for e in elements if e.element_type in _FURNITURE]
        content = [e for e in elements if e.element_type not in _FURNITURE]
        if furniture:
            n_h = sum(1 for e in furniture if e.element_type == "page_header")
            n_n = sum(1 for e in furniture if e.element_type == "page_number")
            parts.append(
                f'<div class="el el-furniture">⋯ 家具 {len(furniture)} 项'
                f"（页眉 {n_h} / 页码 {n_n}）——已按类型分流，可过滤</div>"
            )
        for element in content:
            conf = ""
            if element.confidence.type is not None and element.confidence.type < 0.7:
                conf = f'<span class="low-conf">置信 {round(element.confidence.type, 2)}</span> '
            anchor = f'id="el-{element.element_id}"'
            if element.element_type == "heading":
                d = min(_heading_depth(doc, element), 5)
                parts.append(
                    f'<div class="el el-heading" {anchor} style="margin-left:{d * 18}px">'
                    f"{_badge(element.element_type)}{conf}{_e(element.text)}{_ev_tag(element)}</div>"
                )
            elif element.element_type == "table":
                asset = next(table_iter, None)
                rendered = _render_table_asset(asset) if asset else ""
                parts.append(
                    f'<div class="el el-table" {anchor}>'
                    f"{_badge(element.element_type)}表格{rendered}</div>"
                )
            else:
                parts.append(
                    f'<div class="el el-paragraph" {anchor}>'
                    f"{_badge(element.element_type)}{conf}{_e(element.text)}{_ev_tag(element)}</div>"
                )
    return "\n".join(parts)


def render_report(profile, decision, doc: ParsedDocument, source_name: str) -> str:
    verdict = validate(doc)
    warnings = list(doc.diagnostics.warnings)
    types_count: dict[str, int] = {}
    for e in doc.elements:
        types_count[e.element_type] = types_count.get(e.element_type, 0) + 1
    table_assets = [a for a in doc.structured_assets.values() if hasattr(a, "rows")]

    meta_rows = [
        ("文件", _e(source_name)),
        ("格式 → 路由", f"{profile.source_format} → <b>{_e(decision.primary_parser_id)}</b>"),
        ("容器", f"{len(doc.containers)}（{profile.container_kind or '—'}）"),
        ("元素", f"{len(doc.elements)}（" + " · ".join(
            f"{k}×{v}" for k, v in sorted(types_count.items())) + "）"),
        ("结构关系", f"{len(doc.relations)} 条（heading 父子 / 阅读顺序）"),
        ("表格资产", f"{len(table_assets)} 张（网格 + 合并格 + 表头标记）"),
        ("IR 校验", "通过" if verdict.valid else "<b class='warn'>失败</b>"),
    ]
    if warnings:
        meta_rows.append(("警告", f'<span class="warn">{_e("; ".join(warnings[:4]))}</span>'))

    sample = ""
    if table_assets:
        asset = table_assets[0]
        sample = json.dumps(
            {
                "kind": "table",
                "table_id": asset.table_id,
                "rows": asset.rows,
                "columns": asset.columns,
                "header_regions": [list(r) for r in asset.header_regions],
                "cells(sample)": [
                    {
                        "row": c.row_index, "col": c.column_index,
                        "text": c.text[:20], "row_span": c.row_span,
                        "column_span": c.column_span, "is_header": c.is_header,
                    }
                    for c in asset.cells[:6]
                ],
                "confidence": {"type": asset.confidence.type, "source": asset.confidence.source},
            },
            ensure_ascii=False, indent=2,
        )

    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>解析结构 · {_e(source_name)}</title>
<style>{_CSS}</style></head>
<body>
<div class="layout">
<nav class="toc"><b>目录（解析出的标题树）</b>
{_toc_html(doc)}
</nav>
<div class="main">
<h1>文档解析结构报告</h1>
<table class="meta">
{''.join(f'<tr><td>{k}</td><td>{v}</td></tr>' for k, v in meta_rows)}
</table>
<h2>结构视图</h2>
<p class="ev">每个元素 = 类型徽章 + 内容 + 证据定位；黄底=低置信判定；家具已分流。</p>
{_structure_html(doc)}
<h2>结构化数据形态（表格资产 JSON 样例）</h2>
<p class="ev">下游（检索 / Agent / 切片编译）消费的就是这样的记录：行列数、单元格
网格（文本+坐标+跨行跨列）、表头区、置信度——不是一段 Markdown 文本。</p>
<pre class="ir">{_e(sample or "(无表格资产)")}</pre>
</div></div>
</body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description="解析效果预览器（纯本地验收工具）")
    ap.add_argument("file", help="待解析文档（pdf/docx/xlsx/pptx/html/md/txt）")
    ap.add_argument("-o", "--out", default=None, help="输出 HTML 路径")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"文件不存在: {path}")
    data = path.read_bytes()

    declared = {
        ".pdf": "application/pdf", ".md": "text/markdown", ".txt": "text/plain",
        ".html": "text/html", ".htm": "text/html",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }.get(path.suffix.lower())

    profile, decision, doc = parse_document(data, declared_mime=declared)
    out = Path(args.out) if args.out else path.with_suffix(".parse-preview.html")
    out.write_text(render_report(profile, decision, doc, path.name), encoding="utf-8")
    print(f"路由: {decision.primary_parser_id}  元素: {len(doc.elements)}  "
          f"容器: {len(doc.containers)}  表格: {len(doc.structured_assets)}")
    print(f"报告: {out.resolve()}")


if __name__ == "__main__":
    main()
