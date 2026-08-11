"""Archive preprocessing for ingestion: .chm and .hdx → markdown string.

Two file types are handled here, both compiled help packages that bundle HTML
+ assets into a single archive:

- `.chm`  Microsoft Compiled HTML Help. Extraction prefers Windows `hh.exe`
          and falls back to `7z` if available on PATH. Conversion uses the
          embedded `.hhc` Sitemap TOC to order topics and assign heading depth.

- `.hdx`  Huawei HedEx package — a zip of HTML topics under `resources/`.
          Conversion concatenates topics in lexical order (no TOC).

Public API:

    is_archive(path)              -> bool
    archive_to_markdown(src, ...) -> str        (extract + convert in one shot)
    extract_archive(src, dst)     -> Path
    convert_extracted(extracted_dir, layout=, title=) -> str

The two CLI scripts under `scripts/data/` import from this module so there is
a single source of truth.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


SUPPORTED_ARCHIVE_EXTS = frozenset({".chm", ".hdx"})


def is_archive(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_ARCHIVE_EXTS


# ---------------------------------------------------------------------------
# Encoding-aware text reader
# ---------------------------------------------------------------------------

_META_CHARSET = re.compile(rb'charset\s*=\s*["\']?([\w-]+)', re.IGNORECASE)


def read_text(path: Path) -> str:
    """Read an HTML/HHC file using its declared meta charset (gb18030 for gbk variants)."""
    raw = path.read_bytes()
    m = _META_CHARSET.search(raw[:4096])
    enc = "utf-8"
    if m:
        declared = m.group(1).decode("ascii", errors="replace").lower()
        if declared in ("gb2312", "gbk", "gb18030"):
            enc = "gb18030"
        else:
            enc = declared
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Archive extraction
# ---------------------------------------------------------------------------

def extract_chm(src: Path, dst: Path) -> None:
    """Extract a .chm file. Prefers Windows hh.exe; falls back to 7z."""
    dst.mkdir(parents=True, exist_ok=True)

    hh = shutil.which("hh")
    if hh is None:
        candidate = Path(r"C:\Windows\hh.exe")
        if candidate.exists():
            hh = str(candidate)

    if hh is not None:
        subprocess.run(
            [hh, "-decompile", str(dst.resolve()), str(src.resolve())],
            check=False,
            capture_output=True,
        )
        if any(dst.iterdir()):
            return

    sevenz = shutil.which("7z") or shutil.which("7z.exe")
    if sevenz is not None:
        result = subprocess.run(
            [sevenz, "x", str(src), f"-o{dst}", "-y"],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0 and any(dst.iterdir()):
            return
        raise RuntimeError(
            f"7z failed for {src}: {result.stderr.decode('utf-8', errors='replace')}"
        )

    raise RuntimeError(
        "No CHM extractor available. Need Windows hh.exe or 7-Zip on PATH."
    )


def extract_hdx(src: Path, dst: Path) -> None:
    """Extract a .hdx file (Huawei HedEx zip archive) using stdlib zipfile."""
    dst.mkdir(parents=True, exist_ok=True)
    if not zipfile.is_zipfile(src):
        raise RuntimeError(
            f"{src} is not a zip archive; .hdx variant not supported by this tool."
        )
    with zipfile.ZipFile(src, "r") as zf:
        zf.extractall(dst)


def extract_archive(src: Path, dst: Path) -> Path:
    """Dispatch on extension. Returns dst."""
    ext = src.suffix.lower()
    if ext == ".chm":
        extract_chm(src, dst)
    elif ext == ".hdx":
        extract_hdx(src, dst)
    else:
        raise ValueError(f"Unsupported archive extension {ext!r}: {src}")
    return dst


# ---------------------------------------------------------------------------
# Generic HTML tree builder (stdlib html.parser)
# ---------------------------------------------------------------------------

_VOID_TAGS = {"br", "hr", "img", "meta", "link", "input", "col"}


class _TreeBuilder(HTMLParser):
    """Build a simple dict-based DOM tree. Robust to malformed HedEx HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict[str, Any] = {"tag": None, "attrs": {}, "children": []}
        self.stack: list[dict[str, Any]] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node: dict[str, Any] = {
            "tag": tag.lower(),
            "attrs": {k: (v or "") for k, v in attrs},
            "children": [],
        }
        self.stack[-1]["children"].append(node)
        if tag.lower() not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node: dict[str, Any] = {
            "tag": tag.lower(),
            "attrs": {k: (v or "") for k, v in attrs},
            "children": [],
        }
        self.stack[-1]["children"].append(node)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["tag"] == t:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1]["children"].append({"tag": "#text", "text": data})


def build_tree(html_text: str) -> dict[str, Any]:
    p = _TreeBuilder()
    p.feed(html_text)
    p.close()
    return p.root


# ---------------------------------------------------------------------------
# HHC TOC parser
# ---------------------------------------------------------------------------

class _HhcParser(HTMLParser):
    """Yield (depth, title, local) tuples in document order from an HHC file."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.entries: list[tuple[int, str, str]] = []
        self._cur: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        d = {k: (v or "") for k, v in attrs}
        if t == "ul":
            self.depth += 1
        elif t == "object" and (d.get("type") or "").lower() == "text/sitemap":
            self._cur = {}
        elif t == "param" and self._cur is not None:
            name = (d.get("name") or "").lower()
            if name in ("name", "local"):
                self._cur[name] = d.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "object" and self._cur is not None:
            title = self._cur.get("name", "").strip()
            local = self._cur.get("local", "").strip()
            if title and local:
                self.entries.append((self.depth, title, local))
            self._cur = None
        elif t == "ul":
            self.depth = max(0, self.depth - 1)


def parse_hhc(path: Path) -> list[tuple[int, str, str]]:
    p = _HhcParser()
    p.feed(read_text(path))
    p.close()
    return p.entries


# ---------------------------------------------------------------------------
# HTML -> Markdown rendering
# ---------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "head", "noscript", "title", "meta", "link"}


def _render(node: dict[str, Any], heading_offset: int) -> str:
    tag = node.get("tag")
    if tag == "#text":
        return node.get("text", "")
    if tag in _SKIP_TAGS:
        return ""

    children = node.get("children", [])

    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag[1]) + heading_offset
        level = max(1, min(6, level))
        text = _inline("".join(_render(c, heading_offset) for c in children))
        return f"\n\n{'#' * level} {text}\n\n" if text else ""

    if tag == "p":
        text = _inline("".join(_render(c, heading_offset) for c in children))
        return f"\n\n{text}\n\n" if text else ""

    if tag == "br":
        return "  \n"

    if tag == "hr":
        return "\n\n---\n\n"

    if tag in ("strong", "b"):
        text = _inline("".join(_render(c, heading_offset) for c in children))
        return f"**{text}**" if text else ""

    if tag in ("em", "i"):
        text = _inline("".join(_render(c, heading_offset) for c in children))
        return f"*{text}*" if text else ""

    if tag == "code":
        text = "".join(_render(c, heading_offset) for c in children)
        if "\n" in text:
            return f"\n\n```\n{text.strip()}\n```\n\n"
        return f"`{text}`" if text else ""

    if tag == "pre":
        text = "".join(_render(c, heading_offset) for c in children).strip("\n")
        if text.startswith("```"):
            return f"\n\n{text}\n\n"
        return f"\n\n```\n{text}\n```\n\n"

    if tag == "a":
        return "".join(_render(c, heading_offset) for c in children)

    if tag == "img":
        src = node["attrs"].get("src", "").strip()
        alt = node["attrs"].get("alt", "").strip()
        if not src or "public_sys-resources/" in src and src.lower().endswith(".gif"):
            return ""
        return f"![{alt}]({src})"

    if tag in ("ul", "ol"):
        return _render_list(node, heading_offset, ordered=(tag == "ol"))

    if tag == "li":
        return ""

    if tag == "table":
        return _render_table(node, heading_offset)

    if tag in ("thead", "tbody", "tfoot", "tr", "td", "th",
               "caption", "col", "colgroup"):
        return ""

    return "".join(_render(c, heading_offset) for c in children)


def _inline(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


_BLOCK_TAGS = {
    "p", "table", "div", "ul", "ol", "pre", "hr", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6",
}


def _render_list(node: dict[str, Any], heading_offset: int, ordered: bool) -> str:
    """Render a <ul>/<ol> to Markdown.

    Some authors put block-level children (img-wrapping <p>, <table>, nested
    <ul>) directly inside <li> instead of as siblings of the list. We split
    each <li>'s children into inline-runs and block-children: the leading
    inline run goes on the bullet line, blocks are emitted as following
    paragraphs (same shape we get when those blocks were authored as
    siblings of the <ul>). This avoids _inline() collapsing newlines from
    embedded tables/paragraphs into a single line.
    """
    items = [c for c in node.get("children", []) if c.get("tag") == "li"]
    out: list[str] = ["\n"]
    for idx, item in enumerate(items, start=1):
        prefix = f"{idx}. " if ordered else "- "

        segments: list[tuple[str, str]] = []
        inline_buf: list[str] = []

        def flush_inline() -> None:
            if inline_buf:
                s = _inline("".join(inline_buf))
                if s:
                    segments.append(("inline", s))
                inline_buf.clear()

        for child in item.get("children", []):
            if child.get("tag") in _BLOCK_TAGS:
                flush_inline()
                rendered = _render(child, heading_offset)
                if rendered.strip():
                    segments.append(("block", rendered))
            else:
                inline_buf.append(_render(child, heading_offset))
        flush_inline()

        if not segments:
            continue

        first_kind, first_text = segments[0]
        if first_kind == "inline":
            out.append(f"{prefix}{first_text}\n")
            rest = segments[1:]
        else:
            out.append(f"{prefix}\n")
            rest = segments

        for kind, text in rest:
            if kind == "inline":
                out.append(f"\n{text}\n")
            else:
                out.append(text)
    out.append("\n")
    return "".join(out)


def _render_table(node: dict[str, Any], heading_offset: int) -> str:
    """Render an HTML table to a Markdown pipe-table.

    Markdown lacks rowspan/colspan, so spanned cells are *expanded*.
    """
    raw_rows: list[tuple[bool, list[dict[str, Any]]]] = []
    caption: str | None = None

    def visit(n: dict[str, Any], in_header: bool) -> None:
        nonlocal caption
        for c in n.get("children", []):
            t = c.get("tag")
            if t == "caption":
                caption = _inline(
                    "".join(_render(cc, heading_offset) for cc in c.get("children", []))
                )
            elif t == "thead":
                visit(c, in_header=True)
            elif t in ("tbody", "tfoot"):
                visit(c, in_header=False)
            elif t == "tr":
                cells: list[dict[str, Any]] = []
                row_is_header = in_header
                for cc in c.get("children", []):
                    ct = cc.get("tag")
                    if ct in ("td", "th"):
                        if ct == "th":
                            row_is_header = True
                        cells.append(cc)
                if cells:
                    raw_rows.append((row_is_header, cells))

    visit(node, in_header=False)
    if not raw_rows:
        return ""

    grid: list[list[str | None]] = []
    headers: list[bool] = []
    pending: dict[tuple[int, int], str] = {}

    for row_idx, (is_header, cells) in enumerate(raw_rows):
        if row_idx >= len(grid):
            grid.append([])
            headers.append(is_header)

        col = 0
        for cell in cells:
            while (row_idx, col) in pending:
                _ensure_col(grid, row_idx, col)
                grid[row_idx][col] = pending.pop((row_idx, col))
                col += 1

            text = "".join(
                _render(cc2, heading_offset) for cc2 in cell.get("children", [])
            )
            text = _inline(text).replace("|", "\\|")

            try:
                rowspan = max(1, int(cell["attrs"].get("rowspan") or "1"))
            except ValueError:
                rowspan = 1
            try:
                colspan = max(1, int(cell["attrs"].get("colspan") or "1"))
            except ValueError:
                colspan = 1

            for dc in range(colspan):
                _ensure_col(grid, row_idx, col + dc)
                grid[row_idx][col + dc] = text
                for dr in range(1, rowspan):
                    pending[(row_idx + dr, col + dc)] = text

            col += colspan

        while (row_idx, col) in pending:
            _ensure_col(grid, row_idx, col)
            grid[row_idx][col] = pending.pop((row_idx, col))
            col += 1

    while pending:
        next_row = min(r for r, _ in pending)
        while next_row >= len(grid):
            grid.append([])
            headers.append(False)
        col = 0
        while (next_row, col) in pending:
            _ensure_col(grid, next_row, col)
            grid[next_row][col] = pending.pop((next_row, col))
            col += 1

    ncols = max((len(r) for r in grid), default=0)
    if ncols == 0:
        return ""
    for r in grid:
        while len(r) < ncols:
            r.append("")
        for i, v in enumerate(r):
            if v is None:
                r[i] = ""

    if headers and headers[0]:
        header = grid[0]
        body_rows = grid[1:]
    else:
        header = [""] * ncols
        body_rows = grid

    lines: list[str] = ["\n"]
    if caption:
        lines.append(f"{caption}\n")
    lines.append("| " + " | ".join(_clean_cell(c) for c in header) + " |")
    lines.append("| " + " | ".join(["---"] * ncols) + " |")
    for row in body_rows:
        lines.append("| " + " | ".join(_clean_cell(c) for c in row) + " |")
    lines.append("\n")
    return "\n".join(lines)


def _ensure_col(grid: list[list[str | None]], row: int, col: int) -> None:
    while row >= len(grid):
        grid.append([])
    while col >= len(grid[row]):
        grid[row].append(None)


def _clean_cell(text: str) -> str:
    return text if text else ""


# ---------------------------------------------------------------------------
# Topic body extraction
# ---------------------------------------------------------------------------

_BODY_DIV_CLASSES = {"articleBoxWithoutHead", "articleBox", "topicBody"}


def _find_body(root: dict[str, Any]) -> dict[str, Any]:
    """Locate the topic content root: prefer DITA's articleBox div, else <body>."""
    found: dict[str, Any] | None = None

    def walk(n: dict[str, Any]) -> None:
        nonlocal found
        if found is not None:
            return
        if n.get("tag") == "div":
            classes = (n["attrs"].get("class") or "").split()
            if any(c in _BODY_DIV_CLASSES for c in classes):
                found = n
                return
        for c in n.get("children", []):
            walk(c)

    walk(root)
    if found is not None:
        return found

    def walk_body(n: dict[str, Any]) -> dict[str, Any] | None:
        if n.get("tag") == "body":
            return n
        for c in n.get("children", []):
            r = walk_body(c)
            if r is not None:
                return r
        return None

    return walk_body(root) or root


_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_ABS_SCHEME_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|/|#|data:)")


def _prefix_image_paths(md: str, image_path_prefix: str) -> str:
    """Prepend a directory prefix to relative image paths in markdown."""
    if not image_path_prefix:
        return md
    if not image_path_prefix.endswith("/"):
        image_path_prefix = image_path_prefix + "/"

    def _sub(m: re.Match) -> str:
        alt, src = m.group(1), m.group(2).strip()
        if _ABS_SCHEME_RE.match(src):
            return m.group(0)
        return f"![{alt}]({image_path_prefix}{src})"

    return _IMG_RE.sub(_sub, md)


def convert_topic(html_path: Path, depth: int, image_path_prefix: str = "") -> str:
    """Convert one topic .html to markdown, with H1 mapped to depth+1.

    image_path_prefix: prepended to every relative <img> src in the output,
    so the converted markdown can sit somewhere other than alongside the
    HTML and still resolve images. Absolute, scheme-qualified, anchor-only,
    and data: URIs are left untouched.
    """
    text = read_text(html_path)
    tree = build_tree(text)
    body = _find_body(tree)
    md = "".join(_render(c, depth) for c in body.get("children", []))
    md = re.sub(r"\n{3,}", "\n\n", md)
    # Merge adjacent <strong>/<b> emitted with no separator: **a****b** -> **ab**.
    # Source HTML often wraps each word/phrase in its own <strong> with no
    # whitespace between them, which produces ambiguous markdown that some
    # renderers display literally. Tags separated by a space remain intact.
    md = md.replace("****", "")
    md = md.strip()
    md = _prefix_image_paths(md, image_path_prefix)
    return md


# ---------------------------------------------------------------------------
# Layout-aware orchestration
# ---------------------------------------------------------------------------

def detect_layout(extracted_dir: Path) -> str:
    if any(extracted_dir.glob("*.hhc")):
        return "chm"
    if (extracted_dir / "resources").is_dir() and (extracted_dir / "profile.xml").exists():
        return "hdx"
    if any(extracted_dir.glob("*.htm*")):
        return "chm"
    raise RuntimeError(f"Cannot determine layout for {extracted_dir}")


def convert_chm_extracted(
    extracted_dir: Path,
    doc_title: str | None = None,
    image_path_prefix: str = "",
) -> tuple[str, dict]:
    """Return (markdown_text, stats)."""
    hhc_files = sorted(extracted_dir.glob("*.hhc"), key=lambda p: -p.stat().st_size)
    if not hhc_files:
        raise RuntimeError(f"No .hhc TOC found in {extracted_dir}")

    entries = parse_hhc(hhc_files[0])
    if not entries:
        raise RuntimeError(f"HHC parsed empty: {hhc_files[0]}")

    title = doc_title or extracted_dir.name
    parts: list[str] = [f"# {title}\n"]
    seen: set[str] = set()
    converted = 0
    missing = 0

    for depth, _hhc_title, local in entries:
        if local in seen:
            continue
        seen.add(local)
        topic_path = extracted_dir / local
        if not topic_path.exists():
            missing += 1
            continue
        try:
            # Prefer per-topic absolute base so relative <img src> resolve after
            # topics from nested folders are concatenated into one markdown.
            prefix = image_path_prefix
            if not prefix:
                prefix = topic_path.parent.resolve().as_posix() + "/"
            md = convert_topic(topic_path, depth, prefix)
        except Exception as e:
            print(f"  [warn] topic conversion failed: {local}: {e}", file=sys.stderr)
            continue
        if md:
            parts.append(md)
            converted += 1

    return (
        "\n\n".join(parts) + "\n",
        {"toc_entries": len(entries), "converted": converted, "missing": missing},
    )


def convert_hdx_extracted(
    extracted_dir: Path,
    doc_title: str | None = None,
    image_path_prefix: str = "",
) -> tuple[str, dict]:
    """Return (markdown_text, stats). HDX has no TOC, just lex order."""
    res_dir = extracted_dir / "resources"
    if not res_dir.is_dir():
        raise RuntimeError(f"HDX layout missing resources/ dir: {extracted_dir}")

    htmls = sorted(p for p in res_dir.glob("*.htm*"))
    htmls = [p for p in htmls if not p.stem.startswith("hedex-")]
    if not htmls:
        raise RuntimeError(f"No topic htmls under {res_dir}")

    title = doc_title or extracted_dir.name
    parts: list[str] = [f"# {title}\n"]
    converted = 0
    for p in htmls:
        try:
            prefix = image_path_prefix
            if not prefix:
                prefix = p.parent.resolve().as_posix() + "/"
            md = convert_topic(p, depth=1, image_path_prefix=prefix)
        except Exception as e:
            print(f"  [warn] topic conversion failed: {p.name}: {e}", file=sys.stderr)
            continue
        if md:
            parts.append(md)
            converted += 1

    return (
        "\n\n".join(parts) + "\n",
        {"toc_entries": len(htmls), "converted": converted, "missing": 0},
    )


def convert_extracted(
    extracted_dir: Path,
    *,
    layout: str | None = None,
    doc_title: str | None = None,
    image_path_prefix: str = "",
) -> tuple[str, dict]:
    """Return (markdown_text, stats)."""
    layout = layout or detect_layout(extracted_dir)
    if layout == "chm":
        return convert_chm_extracted(extracted_dir, doc_title, image_path_prefix)
    if layout == "hdx":
        return convert_hdx_extracted(extracted_dir, doc_title, image_path_prefix)
    raise ValueError(f"Unknown layout {layout!r}")


# ---------------------------------------------------------------------------
# One-shot helpers used by ingestion
# ---------------------------------------------------------------------------

def html_to_markdown(html_path: Path, doc_title: str | None = None) -> str:
    """Convert a standalone HTML file to markdown using the shared renderer.

    Relative image paths are rewritten to absolute paths under the HTML's
    parent directory so MarkdownParser can materialize them later.
    """
    from knowledge_mining.mining.infra.image_assets import absolutize_markdown_image_paths

    text = read_text(html_path)
    tree = build_tree(text)
    body = _find_body(tree)
    md = "".join(_render(c, 0) for c in body.get("children", []))
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = md.replace("****", "")
    md = md.strip()
    title = doc_title or html_path.stem
    md = f"# {title}\n\n{md}\n"
    return absolutize_markdown_image_paths(md, html_path.parent)


def archive_to_markdown(
    src: Path,
    *,
    work_dir: Path | None = None,
    cleanup: bool = True,
    doc_title: str | None = None,
) -> str:
    """Extract `.chm`/`.hdx` and return the converted markdown as a string.

    Images referenced by topics are copied into a persistent staging directory
    (under the system temp) and rewritten to absolute paths **before** the
    extract tree is cleaned up, so later parse/VLM still see the files.

    If `work_dir` is None, a fresh temp dir is created and (by default) removed
    after conversion. If you pass `work_dir`, set `cleanup=False` to keep the
    extracted contents around for caching/debugging.
    """
    from knowledge_mining.mining.infra.image_assets import stage_markdown_images

    if not src.is_file():
        raise FileNotFoundError(src)
    ext = src.suffix.lower()
    if ext not in SUPPORTED_ARCHIVE_EXTS:
        raise ValueError(f"Not a supported archive: {src}")

    own_workdir = work_dir is None
    if own_workdir:
        work_dir = Path(tempfile.mkdtemp(prefix=f"mining_archive_{src.stem}_"))

    try:
        extract_archive(src, work_dir)
        # Empty prefix → convert_* uses per-topic absolute parent paths.
        md, _stats = convert_extracted(work_dir, doc_title=doc_title or src.stem)
        stage_dir = (
            Path(tempfile.gettempdir())
            / "mining_archive_images"
            / f"{src.stem}_{abs(hash(str(src.resolve()))) % (10**10)}"
        )
        md = stage_markdown_images(md, stage_dir)
        return md
    finally:
        if own_workdir and cleanup:
            shutil.rmtree(work_dir, ignore_errors=True)
