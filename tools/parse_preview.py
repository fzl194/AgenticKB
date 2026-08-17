"""解析效果预览器（M3 验收工具）——纯本地，不碰 MinIO/PG/pipeline.

用法：
    python tools/parse_preview.py <文档路径> [-o 输出.html]

链路（与生产影子链路同一套适配器，但不写任何存储）：
    读文件 bytes → FileInspector 探测 → ParserRouter 路由 → 适配器解析
    → Normalizer 产 Parse IR → validate → 渲染 HTML 报告

报告内容：
    - 概要：探测画像、路由决策（parser + 原因码）、指纹、元素/关系/容器计数
    - 结构树：按容器分组，标题按层级缩进，段落全文，表格渲染为 HTML 网格
      （合并单元格用 rowspan/colspan 还原），每元素附证据定位
    - 质量标记：置信度 < 0.7 的元素（如 PDF 字号启发式标题）黄底标注
"""
from __future__ import annotations

import argparse
import html
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
# HTML 渲染
# ---------------------------------------------------------------------------

_CSS = """
body { font-family: "Microsoft YaHei", sans-serif; margin: 24px auto; max-width: 1080px;
       color: #1f2937; line-height: 1.6; }
h1 { border-bottom: 3px solid #2563eb; padding-bottom: 8px; }
h2 { border-left: 4px solid #2563eb; padding-left: 10px; margin-top: 32px; }
table.meta td { padding: 4px 12px; }
table.meta td:first-child { color: #6b7280; white-space: nowrap; }
section.doc { border: 1px solid #e5e7eb; border-radius: 8px; padding: 8px 20px; margin: 12px 0; }
.container { margin: 10px 0 18px 0; }
.container-title { color: #2563eb; font-weight: 600; font-size: 0.95em; }
.low-conf { background: #fef9c3; border-radius: 4px; padding: 1px 6px; }
.ev { color: #9ca3af; font-size: 0.78em; margin-left: 8px; font-family: Consolas, monospace; }
table.grid { border-collapse: collapse; margin: 6px 0 6px 28px; }
table.grid td, table.grid th { border: 1px solid #d1d5db; padding: 4px 10px; font-size: 0.9em; }
table.grid th { background: #f3f4f6; }
.warn { color: #b45309; }
.headings { border-left: 2px solid #e5e7eb; margin-left: 10px; padding-left: 16px; }
"""


def _e(text: str) -> str:
    return html.escape(text or "")


def _evidence_tag(element) -> str:
    parts = []
    for span in element.source_spans:
        if span.source_locator:
            parts.append(str(span.source_locator))
        elif span.visual_region:
            parts.append(str(span.visual_region))
        elif span.native_ref:
            parts.append(str(span.native_ref))
    return f'<span class="ev">{_e(" | ".join(parts[:1]))}</span>' if parts else ""


def _render_table_asset(asset) -> str:
    grid: dict[tuple[int, int], str] = {}
    span_map: dict[tuple[int, int], tuple[int, int]] = {}
    for cell in asset.cells:
        span_map[(cell.row_index, cell.column_index)] = (
            cell.row_span, cell.column_span,
        )
        grid[(cell.row_index, cell.column_index)] = _e(cell.text)
    if not grid:
        return ""
    max_r = max(r for r, _ in grid) + 1
    max_c = max(c for _, c in grid) + 1
    covered: set[tuple[int, int]] = set()
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
            attrs = ""
            if rs > 1:
                attrs += f' rowspan="{rs}"'
            if cs > 1:
                attrs += f' colspan="{cs}"'
            tag = "th" if any(
                cell.row_index == r and cell.column_index == c and cell.is_header
                for cell in asset.cells
            ) else "td"
            cells_html.append(f"<{tag}{attrs}>{grid[(r, c)]}</{tag}>")
        if cells_html:
            rows_html.append("<tr>" + "".join(cells_html) + "</tr>")
    return (
        f'<table class="grid">{"".join(rows_html)}</table>'
        f'<div class="ev">table {asset.rows}×{asset.columns}'
        + (f'，续表→{_e(asset.continuation_of)}' if asset.continuation_of else "")
        + "</div>"
    )


def _container_group_html(doc: ParsedDocument) -> str:
    tables_by_element = {}
    for asset in doc.structured_assets.values():
        if hasattr(asset, "rows"):
            tables_by_element.setdefault(None, None)  # 占位，见下方关联
    # TableAsset 与 table element 的关联：按 caption/顺序无法稳定反查，
    # 简化处理——表格渲染跟随其对应 table element 的文档顺序（按
    # structured_assets 中的出现顺序在对应容器尾部展示）。
    table_assets = [
        a for a in doc.structured_assets.values() if hasattr(a, "rows")
    ]
    table_iter = iter(table_assets)

    by_container: dict[str | None, list] = {}
    order: list[str | None] = []
    for element in doc.elements:
        cid = element.page_span_ids[0] if element.page_span_ids else None
        if cid not in by_container:
            by_container[cid] = []
            order.append(cid)
        by_container[cid].append(element)

    containers_by_id = {c.container_id: c for c in doc.containers}
    out = []
    for cid in order:
        meta = containers_by_id.get(cid) if cid else None
        title = (
            f"{meta.container_type}"
            + (f" #{meta.page_number}" if meta.page_number else "")
            + (f" · {meta.name}" if meta.name else "")
        ) if meta else "文档（无容器）"
        out.append('<div class="container">')
        out.append(f'<div class="container-title">▣ {_e(title)}</div>')
        depth = 0
        pending_tables: list[str] = []
        furniture_count = 0
        for element in by_container[cid]:
            if element.element_type in ("page_header", "page_footer", "page_number"):
                furniture_count += 1
                continue  # 家具折叠：不逐条展示（见容器尾摘要）
        if furniture_count:
            out.append(
                f'<div class="ev">⋯ 本页家具（页眉/页码）{furniture_count} 项已折叠</div>'
            )
        content = [
            e for e in by_container[cid]
            if e.element_type not in ("page_header", "page_footer", "page_number")
        ]
        for element in content:
            if element.element_type == "heading":
                level = 1
                for span in element.source_spans:
                    pass
                # heading 深度：由父链推断（同容器内向父 heading 数）
                depth = _heading_depth(doc, element)
                tag = f"h{min(depth + 1, 6)}"
                out.append(
                    f'<div style="margin-left:{depth * 14}px">'
                    f"<{tag}>{_e(element.text)}{_evidence_tag(element)}</{tag}></div>"
                )
                continue
            if element.element_type == "paragraph":
                conf = ""
                if element.confidence.type is not None and element.confidence.type < 0.7:
                    conf = '<span class="low-conf">低置信</span> '
                out.append(
                    f'<p style="margin-left:{depth * 14 + 10}px">{conf}'
                    f"{_e(element.text)}{_evidence_tag(element)}</p>"
                )
                continue
            if element.element_type == "table":
                asset = next(table_iter, None)
                if asset is not None:
                    pending_tables.append(_render_table_asset(asset))
                continue
            # 其余类型（list_item/code/figure/…）
            out.append(
                f'<p style="margin-left:{depth * 14 + 10}px">'
                f'<b>[{_e(element.element_type)}]</b> {_e(element.text)}'
                f"{_evidence_tag(element)}</p>"
            )
        for rendered in pending_tables:
            out.append(rendered)
        out.append("</div>")
    return "\n".join(out)


def _heading_depth(doc: ParsedDocument, element) -> int:
    by_id = {e.element_id: e for e in doc.elements}
    depth = 0
    node = element
    while node.parent_id is not None and node.parent_id in by_id:
        depth += 1
        node = by_id[node.parent_id]
    return depth


def render_report(profile, decision, doc: ParsedDocument, source_name: str) -> str:
    verdict = validate(doc)
    warnings = list(doc.diagnostics.warnings)
    low_conf_count = sum(
        1 for e in doc.elements
        if e.confidence.type is not None and e.confidence.type < 0.7
    )
    meta_rows = [
        ("文件", _e(source_name)),
        ("探测格式", f"{profile.source_format}（MIME {profile.detected_mime}）"),
        ("容器数/类型", f"{len(doc.containers)} · {profile.container_kind or '—'}"),
        ("路由决策", f"<b>{_e(decision.primary_parser_id)}</b>"
         f"（reason: {', '.join(decision.reason_codes) or '—'}）"),
        ("解析器指纹", _e(doc.source_identity.parser_fingerprint)),
        ("元素总数", str(len(doc.elements))),
        ("结构关系数", str(len(doc.relations))),
        ("表格资产数", str(len(doc.structured_assets))),
        ("IR 校验", "通过" if verdict.valid else f"<b class='warn'>失败</b>"),
        ("低置信元素", f"{low_conf_count}" + ("（如 PDF 启发式标题）" if low_conf_count else "")),
    ]
    if warnings:
        meta_rows.append(("解析警告", f'<span class="warn">{_e("; ".join(warnings[:5]))}</span>'))

    types_count: dict[str, int] = {}
    for e in doc.elements:
        types_count[e.element_type] = types_count.get(e.element_type, 0) + 1
    type_summary = " · ".join(f"{k}×{v}" for k, v in sorted(types_count.items()))

    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>解析预览 · {_e(source_name)}</title>
<style>{_CSS}</style></head>
<body>
<h1>解析效果预览</h1>
<table class="meta">
{''.join(f'<tr><td>{k}</td><td>{v}</td></tr>' for k, v in meta_rows)}
</table>
<p class="ev">元素构成：{_e(type_summary)}</p>
<h2>解析出的文档结构（按容器分组）</h2>
<section class="doc">
{_container_group_html(doc)}
</section>
<p class="ev">说明：黄底“低置信”= 解析器自报置信度 &lt; 0.7（如 PDF 字号启发式判定
的标题）；灰色等宽字 = 证据定位（行号/页码+坐标/单元格/xpath）。</p>
</body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description="解析效果预览器（纯本地验收工具）")
    ap.add_argument("file", help="待解析文档（pdf/docx/xlsx/pptx/html/md/txt）")
    ap.add_argument("-o", "--out", default=None, help="输出 HTML 路径（默认与源文件同目录）")
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
    if declared is None:
        print(f"提示：未识别扩展名 {path.suffix}，交由内容签名探测")

    profile, decision, doc = parse_document(data, declared_mime=declared)
    out = Path(args.out) if args.out else path.with_suffix(".parse-preview.html")
    out.write_text(
        render_report(profile, decision, doc, path.name), encoding="utf-8"
    )
    print(f"路由: {decision.primary_parser_id}  元素: {len(doc.elements)}  "
          f"容器: {len(doc.containers)}  表格: {len(doc.structured_assets)}")
    print(f"报告已生成: {out.resolve()}")


if __name__ == "__main__":
    main()
