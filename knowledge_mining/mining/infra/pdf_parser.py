"""Structural PDF parser using pdfminer.six layout API.

Strategy:
1. Extract per-page text blocks + LTImage (with median font size / coords).
2. Sort text and images into reading order (page, top-to-bottom, left-to-right).
3. When image_dir is set, dump each image to disk and emit block_type='image'.
4. Drop blocks whose normalized text recurs on most pages near a page edge
   (page headers / footers).
5. Drop table-of-contents noise (lines ending with dot leaders + page number).
6. Classify each text block:
   - First line matches `1.1.1 TITLE` numbering, block is short, font >= body
     size → heading (level = dots + 1). Any remainder becomes a paragraph.
   - First line starts with `Tabelle N` / `Abbildung N` → block_type='table'.
   - Otherwise → paragraph.
7. Build a SectionNode tree by stacking on heading levels.
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from knowledge_mining.mining.contracts.models import ContentBlock, SectionNode

logger = logging.getLogger(__name__)


HEADING_RE = re.compile(r"^(\d+(?:\.\d+){0,4})[\u00a0\s]+(\S.{0,200})$")
TOC_DOT_LEADER_RE = re.compile(r"\.{4,}\s*\d+\s*$")
TABLE_CAPTION_RE = re.compile(
    r"^(Tabelle|Table|Abbildung|Figure|Bild)\s+\d+(?:\.\d+)?\b",
    re.IGNORECASE,
)

# Chinese heading patterns
CN_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百千零\d]+[章部篇]")
CN_SECTION_RE = re.compile(r"^第[一二三四五六七八九十百千零\d]+[节条款]")
CN_ENUM_RE = re.compile(r"^[（(][一二三四五六七八九十\d]+[）)]\s*\S")
CN_DASH_ENUM_RE = re.compile(r"^[一二三四五六七八九十]+[、．.]\s*\S")


@dataclass(frozen=True)
class _PdfBlock:
    page_no: int
    text: str
    font_size: float
    x0: float
    y0: float
    page_height: float


@dataclass
class _LayoutItem:
    """One text or image item with sort keys for reading order."""

    page_no: int
    x0: float
    y0: float
    page_height: float
    kind: str  # "text" | "image"
    text_block: _PdfBlock | None = None
    image_block: ContentBlock | None = None


def parse_pdf_to_section_tree(
    pdf_path: str,
    doc_title: str | None = None,
    *,
    image_dir: str | Path | None = None,
) -> SectionNode:
    """Parse a PDF file into a SectionNode tree.

    When ``image_dir`` is provided, embedded ``LTImage`` objects are written
    under that directory and appear as ``block_type="image"`` ContentBlocks
    interleaved in reading order with text.
    """
    items = _extract_layout_items(pdf_path, image_dir=image_dir)
    items = _drop_repeated_headers_footers_items(items)
    items = _split_long_text_items(items)
    content_blocks = _items_to_content_blocks(items)
    if not content_blocks:
        return SectionNode(title=doc_title, level=0)
    return _build_section_tree(content_blocks, doc_title)


def _extract_blocks(pdf_path: str) -> list[_PdfBlock]:
    """Backward-compatible: text blocks only (no images)."""
    items = _extract_layout_items(pdf_path, image_dir=None)
    return [it.text_block for it in items if it.kind == "text" and it.text_block]


def _extract_layout_items(
    pdf_path: str,
    *,
    image_dir: str | Path | None,
) -> list[_LayoutItem]:
    """Collect text containers and images, sorted into reading order."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTChar, LTContainer, LTImage, LTTextContainer

    out_dir = Path(image_dir) if image_dir is not None else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    items: list[_LayoutItem] = []
    image_idx = 0

    for page_no, page in enumerate(extract_pages(pdf_path), start=1):
        page_height = float(getattr(page, "height", 0.0) or 0.0)
        page_items: list[_LayoutItem] = []

        for el in _walk_layout(page):
            if isinstance(el, LTImage):
                if out_dir is None:
                    continue
                image_idx += 1
                try:
                    block = _export_image_block(
                        el,
                        out_dir=out_dir,
                        page_no=page_no,
                        image_idx=image_idx,
                        page_height=page_height,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to export PDF image page=%s idx=%s: %s",
                        page_no, image_idx, exc,
                    )
                    continue
                page_items.append(_LayoutItem(
                    page_no=page_no,
                    x0=float(el.x0),
                    y0=float(el.y0),
                    page_height=page_height,
                    kind="image",
                    image_block=block,
                ))
            elif isinstance(el, LTTextContainer):
                text = el.get_text().strip()
                if not text:
                    continue
                sizes: list[float] = []
                for line in el:
                    for ch in line:
                        if isinstance(ch, LTChar):
                            sizes.append(ch.size)
                if sizes:
                    sizes.sort()
                    font_size = sizes[len(sizes) // 2]
                else:
                    font_size = 0.0
                tb = _PdfBlock(
                    page_no=page_no,
                    text=text,
                    font_size=round(font_size, 2),
                    x0=float(el.x0),
                    y0=float(el.y0),
                    page_height=page_height,
                )
                page_items.append(_LayoutItem(
                    page_no=page_no,
                    x0=tb.x0,
                    y0=tb.y0,
                    page_height=page_height,
                    kind="text",
                    text_block=tb,
                ))

        page_items.sort(key=lambda it: (-it.y0, it.x0))
        items.extend(page_items)

    return items


def _walk_layout(node: Any):
    """Yield LTImage and LTTextContainer leaves; recurse into other containers.

    Text containers are treated as atomic blocks (same as the previous
    top-level-only behaviour for direct page children). Images nested under
    LTFigure are discovered via recursion.
    """
    from pdfminer.layout import LTContainer, LTImage, LTTextContainer

    if isinstance(node, LTImage):
        yield node
        return
    if isinstance(node, LTTextContainer):
        yield node
        return
    if isinstance(node, LTContainer):
        for child in node:
            yield from _walk_layout(child)


def _export_image_block(
    lt_image: Any,
    *,
    out_dir: Path,
    page_no: int,
    image_idx: int,
    page_height: float,
) -> ContentBlock:
    """Write LTImage to disk; return an image ContentBlock."""
    dest, digest = _write_lt_image(lt_image, out_dir, page_no, image_idx)
    bbox = [
        round(float(lt_image.x0), 2),
        round(float(lt_image.y0), 2),
        round(float(lt_image.x1), 2),
        round(float(lt_image.y1), 2),
    ]
    return ContentBlock(
        block_type="image",
        text="",
        structure={
            "kind": "pdf_image",
            "page": page_no,
            "bbox": bbox,
            "page_height": page_height,
            "image_path": str(dest),
            "image_sha256": digest,
            "srcsize": list(getattr(lt_image, "srcsize", ()) or ()),
        },
    )


def _write_lt_image(
    lt_image: Any, out_dir: Path, page_no: int, image_idx: int,
) -> tuple[Path, str]:
    """Prefer pdfminer ImageWriter; fall back to raw stream dump."""
    src: Path | None = None
    try:
        from pdfminer.image import ImageWriter

        writer = ImageWriter(str(out_dir))
        exported_name = writer.export_image(lt_image)
        candidate = out_dir / exported_name
        if not candidate.is_file():
            raise FileNotFoundError(f"ImageWriter did not create {candidate}")
        src = candidate
    except Exception as exc:
        logger.debug("ImageWriter failed (%s); trying raw dump", exc)
        raw = None
        try:
            raw = lt_image.stream.get_data()
        except Exception:
            raw = lt_image.stream.get_rawdata()
        if not raw:
            raise
        src = out_dir / f"_raw_p{page_no}_{image_idx:02d}.bin"
        src.write_bytes(raw)

    data = src.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    dest = out_dir / f"p{page_no}_{image_idx:02d}{src.suffix.lower()}"
    if dest.resolve() != src.resolve():
        if dest.exists():
            dest.unlink()
        src.rename(dest)
    return dest, digest


def _normalize_for_recurrence(text: str) -> str:
    """Collapse digits so 'Seite 3 von 35' matches 'Seite 12 von 35'."""
    return re.sub(r"\d+", "N", text)


def _drop_repeated_headers_footers(blocks: list[_PdfBlock]) -> list[_PdfBlock]:
    items = [
        _LayoutItem(
            page_no=b.page_no, x0=b.x0, y0=b.y0, page_height=b.page_height,
            kind="text", text_block=b,
        )
        for b in blocks
    ]
    kept = _drop_repeated_headers_footers_items(items)
    return [it.text_block for it in kept if it.text_block is not None]


def _drop_repeated_headers_footers_items(
    items: list[_LayoutItem],
) -> list[_LayoutItem]:
    text_blocks = [it.text_block for it in items if it.kind == "text" and it.text_block]
    if not text_blocks:
        return items
    page_count = max(b.page_no for b in text_blocks)
    if page_count < 3:
        return items
    threshold = max(3, page_count // 2)

    counter: Counter[str] = Counter(
        _normalize_for_recurrence(b.text) for b in text_blocks
    )
    repeated = {t for t, c in counter.items() if c >= threshold}
    if not repeated:
        return items

    out: list[_LayoutItem] = []
    for it in items:
        if it.kind != "text" or it.text_block is None:
            out.append(it)
            continue
        b = it.text_block
        if _normalize_for_recurrence(b.text) not in repeated:
            out.append(it)
            continue
        if b.page_height <= 0:
            continue
        top_dist = b.page_height - b.y0
        bottom_dist = b.y0
        near_top = top_dist < b.page_height * 0.15
        near_bottom = bottom_dist < b.page_height * 0.12
        if near_top or near_bottom:
            continue
        out.append(it)
    return out


def _split_long_blocks(blocks: list[_PdfBlock]) -> list[_PdfBlock]:
    items = [
        _LayoutItem(
            page_no=b.page_no, x0=b.x0, y0=b.y0, page_height=b.page_height,
            kind="text", text_block=b,
        )
        for b in blocks
    ]
    split = _split_long_text_items(items)
    return [it.text_block for it in split if it.text_block is not None]


def _split_long_text_items(items: list[_LayoutItem]) -> list[_LayoutItem]:
    """Split text items with multi-line text at blank-line boundaries."""
    out: list[_LayoutItem] = []
    for it in items:
        if it.kind != "text" or it.text_block is None:
            out.append(it)
            continue
        b = it.text_block
        if "\n" not in b.text:
            out.append(it)
            continue

        parts = re.split(r"\n\s*\n", b.text)
        if len(parts) <= 1:
            if len(b.text) > 1000:
                parts = b.text.split("\n")
            else:
                out.append(it)
                continue

        for part in parts:
            part = part.strip()
            if part:
                nb = _PdfBlock(
                    page_no=b.page_no,
                    text=part,
                    font_size=b.font_size,
                    x0=b.x0,
                    y0=b.y0,
                    page_height=b.page_height,
                )
                out.append(_LayoutItem(
                    page_no=nb.page_no,
                    x0=nb.x0,
                    y0=nb.y0,
                    page_height=nb.page_height,
                    kind="text",
                    text_block=nb,
                ))
    return out


def _items_to_content_blocks(items: list[_LayoutItem]) -> list[ContentBlock]:
    text_blocks = [it.text_block for it in items if it.kind == "text" and it.text_block]
    size_counter = Counter(b.font_size for b in text_blocks if b.font_size > 0)
    body_size = size_counter.most_common(1)[0][0] if size_counter else 10.0
    distinct_sizes = sorted(
        {b.font_size for b in text_blocks if b.font_size > 0}, reverse=True,
    )

    result: list[ContentBlock] = []
    for it in items:
        if it.kind == "image" and it.image_block is not None:
            result.append(it.image_block)
        elif it.kind == "text" and it.text_block is not None:
            result.extend(
                _classify_one(it.text_block, body_size, distinct_sizes)
            )
    return result


def _classify_blocks(blocks: list[_PdfBlock]) -> list[ContentBlock]:
    if not blocks:
        return []
    size_counter = Counter(b.font_size for b in blocks if b.font_size > 0)
    body_size = size_counter.most_common(1)[0][0] if size_counter else 10.0
    distinct_sizes = sorted(
        {b.font_size for b in blocks if b.font_size > 0}, reverse=True,
    )
    result: list[ContentBlock] = []
    for b in blocks:
        result.extend(_classify_one(b, body_size, distinct_sizes))
    return result


def _classify_one(
    b: _PdfBlock,
    body_size: float,
    distinct_sizes: list[float],
) -> list[ContentBlock]:
    first_line, _, rest = b.text.partition("\n")
    first_line = first_line.strip()
    rest = rest.strip()

    if TOC_DOT_LEADER_RE.search(first_line):
        return []

    cn_heading = _try_cn_heading(first_line)
    if cn_heading:
        is_short = len(b.text) <= 400
        font_ok = b.font_size + 0.1 >= body_size
        if is_short and font_ok:
            out = [ContentBlock(
                block_type="heading", text=first_line, level=cn_heading,
            )]
            if rest:
                out.append(ContentBlock(block_type="paragraph", text=rest))
            return out

    m = HEADING_RE.match(first_line)
    if m:
        number = m.group(1)
        title = m.group(2).strip()
        level = number.count(".") + 1
        looks_like_toc = "...." in title or TOC_DOT_LEADER_RE.search(title)
        is_short = len(first_line) <= 200 and len(b.text) <= 400
        font_ok = b.font_size + 0.1 >= body_size
        if not looks_like_toc and is_short and font_ok and 1 <= level <= 6:
            out = [ContentBlock(
                block_type="heading",
                text=f"{number} {title}",
                level=level,
            )]
            if rest:
                out.append(ContentBlock(block_type="paragraph", text=rest))
            return out

    if b.font_size > body_size * 1.2 and len(first_line) < 200 and len(b.text) <= 400:
        level = _font_size_to_level(b.font_size, distinct_sizes)
        if 1 <= level <= 6:
            out = [ContentBlock(
                block_type="heading", text=first_line, level=level,
            )]
            if rest:
                out.append(ContentBlock(block_type="paragraph", text=rest))
            return out

    if TABLE_CAPTION_RE.match(first_line):
        return [ContentBlock(block_type="table", text=b.text)]

    return [ContentBlock(block_type="paragraph", text=b.text)]


def _try_cn_heading(text: str) -> int | None:
    """Detect Chinese heading patterns. Returns heading level or None."""
    if CN_CHAPTER_RE.match(text):
        return 1
    if CN_SECTION_RE.match(text):
        return 2
    if CN_ENUM_RE.match(text):
        return 3
    if CN_DASH_ENUM_RE.match(text):
        return 3
    return None


def _font_size_to_level(font_size: float, distinct_sizes: list[float]) -> int:
    """Map a font size to a heading level based on rank among distinct sizes.

    Largest size → level 1, second largest → level 2, etc.
    """
    for idx, size in enumerate(distinct_sizes):
        if abs(font_size - size) < 0.5:
            return min(idx + 1, 6)
    return 4  # default for unrecognized large sizes


def _build_section_tree(
    blocks: list[ContentBlock], doc_title: str | None,
) -> SectionNode:
    """Stack-based builder: pop ancestors with level >= current heading level."""
    root = _mutable_node(title=doc_title, level=0)
    stack: list[dict] = [root]

    for block in blocks:
        if block.block_type == "heading" and block.level:
            level = block.level
            while len(stack) > 1 and stack[-1]["level"] >= level:
                stack.pop()
            new_node = _mutable_node(title=block.text, level=level)
            stack[-1]["children"].append(new_node)
            stack.append(new_node)
        else:
            stack[-1]["blocks"].append(block)

    return _freeze(root)


def _mutable_node(title: str | None, level: int) -> dict:
    return {"title": title, "level": level, "children": [], "blocks": []}


def _freeze(node: dict) -> SectionNode:
    return SectionNode(
        title=node["title"],
        level=node["level"],
        blocks=tuple(node["blocks"]),
        children=tuple(_freeze(c) for c in node["children"]),
    )
