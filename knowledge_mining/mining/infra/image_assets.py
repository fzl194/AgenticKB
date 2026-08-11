"""Shared helpers for dumping document images into a run-scoped directory.

Image ``ContentBlock.structure`` / ``RawSegmentData.structure_json`` fields:

| key | meaning |
|---|---|
| kind | ``pdf_image`` / ``md_image`` / ``html_image`` / ``docx_image`` |
| image_path | Absolute path to materialized local file (for VLM) |
| image_sha256 | SHA-256 of image bytes |
| native_caption | Alt text / weak caption heuristic when available |
| original_src | Source URL or relative path before materialize |
| caption_source | ``vlm`` / ``fallback`` / ``missing_file`` / ``remote_skipped`` / … |
| vlm_caption | Caption returned by vision model (when captioned) |
| vlm_model | Model id used for caption |

``.txt`` is intentionally not image-capable. Remote ``http(s)`` images are
skipped unless ``fetch_remote_images`` is enabled on parse context / options.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# File types that may produce block_type=image and need an image_dir.
IMAGE_CAPABLE_FILE_TYPES = frozenset({"pdf", "markdown", "docx", "doc", "html"})

_DATA_URI_RE = re.compile(
    r"^data:(image/[\w+.-]+)?(;charset=[\w-]+)?;base64,(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_SAFE_NAME_RE = re.compile(r"[^\w.\-]+", re.UNICODE)


def summarize_image_blocks(tree: Any) -> dict[str, int]:
    """Count image blocks and caption_source tags in a SectionNode tree."""
    counts: dict[str, int] = {
        "images": 0,
        "with_path": 0,
        "missing_file": 0,
        "remote_skipped": 0,
        "vlm": 0,
        "fallback": 0,
    }

    def _walk(node: Any) -> None:
        for block in getattr(node, "blocks", ()) or ():
            if getattr(block, "block_type", None) != "image":
                continue
            counts["images"] += 1
            structure = getattr(block, "structure", None) or {}
            if structure.get("image_path"):
                counts["with_path"] += 1
            src = structure.get("caption_source")
            if src in counts:
                counts[src] += 1
        for child in getattr(node, "children", ()) or ():
            _walk(child)

    if tree is not None:
        _walk(tree)
    return counts


def materialize_image_bytes(
    data: bytes,
    image_dir: str | Path,
    *,
    stem: str,
    ext: str = ".bin",
) -> dict[str, str]:
    """Write image bytes under ``image_dir``; return path + sha256."""
    out_dir = Path(image_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    safe_stem = _SAFE_NAME_RE.sub("_", stem).strip("._") or "img"
    safe_stem = safe_stem[:80]
    if not ext.startswith("."):
        ext = f".{ext}"
    ext = ext.lower()[:12] or ".bin"
    dest = out_dir / f"{safe_stem}_{digest[:12]}{ext}"
    if not dest.is_file():
        dest.write_bytes(data)
    return {"image_path": str(dest.resolve()), "image_sha256": digest}


def materialize_image_file(
    source: str | Path,
    image_dir: str | Path,
    *,
    stem: str | None = None,
) -> dict[str, str] | None:
    """Copy an existing local image file into ``image_dir``."""
    src = Path(source)
    if not src.is_file():
        return None
    data = src.read_bytes()
    ext = src.suffix or ".bin"
    return materialize_image_bytes(
        data, image_dir, stem=stem or src.stem, ext=ext,
    )


def materialize_data_uri(
    uri: str,
    image_dir: str | Path,
    *,
    stem: str = "data",
) -> dict[str, str] | None:
    """Decode a data:image/...;base64,... URI into ``image_dir``."""
    m = _DATA_URI_RE.match(uri.strip())
    if not m:
        return None
    mime = (m.group(1) or "image/png").lower()
    try:
        data = base64.b64decode(m.group(3), validate=False)
    except Exception as exc:
        logger.warning("Failed to decode data URI: %s", exc)
        return None
    if not data:
        return None
    ext = mimetypes.guess_extension(mime.split(";")[0]) or ".bin"
    if ext == ".jpe":
        ext = ".jpg"
    return materialize_image_bytes(data, image_dir, stem=stem, ext=ext)


def classify_image_src(src: str) -> str:
    """Return ``data`` / ``remote`` / ``local`` for an image src string."""
    s = (src or "").strip()
    if not s:
        return "local"
    if s.lower().startswith("data:"):
        return "data"
    parsed = urlparse(s)
    if parsed.scheme in ("http", "https"):
        return "remote"
    return "local"


def resolve_local_image_src(
    src: str,
    *,
    base_dir: str | Path | None,
    image_dir: str | Path | None,
    stem: str = "img",
    fetch_remote: bool = False,
    remote_timeout: float = 10.0,
) -> dict[str, Any]:
    """Resolve an image src into materialized local assets or a skip reason.

    Returns a dict with some of:
      image_path, image_sha256, caption_source, original_src
    """
    original = (src or "").strip()
    result: dict[str, Any] = {"original_src": original}
    if not original:
        result["caption_source"] = "missing_src"
        return result

    kind = classify_image_src(original)
    if kind == "data":
        if not image_dir:
            result["caption_source"] = "missing_image_dir"
            return result
        meta = materialize_data_uri(original, image_dir, stem=stem)
        if meta:
            result.update(meta)
        else:
            result["caption_source"] = "decode_failed"
        return result

    if kind == "remote":
        if not fetch_remote or not image_dir:
            result["caption_source"] = "remote_skipped"
            return result
        meta = _fetch_remote_image(original, image_dir, stem=stem, timeout=remote_timeout)
        if meta:
            result.update(meta)
        else:
            result["caption_source"] = "remote_fetch_failed"
        return result

    # Local / relative path (also file://)
    path_str = original
    if path_str.lower().startswith("file:"):
        parsed = urlparse(path_str)
        path_str = unquote(parsed.path)
        # Windows file:///C:/... → /C:/... ; strip leading slash before drive
        if re.match(r"^/[A-Za-z]:", path_str):
            path_str = path_str[1:]
    candidate = Path(path_str)
    if not candidate.is_file() and base_dir is not None:
        candidate = (Path(base_dir) / path_str).resolve()
    if not candidate.is_file():
        result["caption_source"] = "missing_file"
        return result
    if image_dir:
        meta = materialize_image_file(candidate, image_dir, stem=stem)
        if meta:
            result.update(meta)
            return result
    result["image_path"] = str(candidate.resolve())
    result["image_sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return result


_MD_IMG_DEST_RE = re.compile(
    r"!\[([^\]]*)\]\(([^)\s]+)((?:\s+\"[^\"]*\")?)\)"
)


def absolutize_markdown_image_paths(md: str, base_dir: str | Path) -> str:
    """Rewrite relative ``![](src)`` destinations to absolute paths under base_dir."""
    base = Path(base_dir)

    def _repl(m: re.Match[str]) -> str:
        alt, src, title = m.group(1), m.group(2).strip().strip("<>"), m.group(3) or ""
        if classify_image_src(src) != "local":
            return m.group(0)
        candidate = Path(src)
        if not candidate.is_file():
            candidate = (base / src).resolve()
        if not candidate.is_file():
            return m.group(0)
        return f"![{alt}]({candidate.as_posix()}{title})"

    return _MD_IMG_DEST_RE.sub(_repl, md)


def stage_markdown_images(md: str, stage_dir: str | Path) -> str:
    """Copy local image files referenced in MD into ``stage_dir``; rewrite paths."""
    stage = Path(stage_dir)
    stage.mkdir(parents=True, exist_ok=True)

    def _repl(m: re.Match[str]) -> str:
        alt, src, title = m.group(1), m.group(2).strip().strip("<>"), m.group(3) or ""
        if classify_image_src(src) != "local":
            return m.group(0)
        candidate = Path(src)
        if not candidate.is_file():
            return m.group(0)
        meta = materialize_image_file(candidate, stage, stem=candidate.stem)
        if not meta:
            return m.group(0)
        return f"![{alt}]({Path(meta['image_path']).as_posix()}{title})"

    return _MD_IMG_DEST_RE.sub(_repl, md)


def _fetch_remote_image(
    url: str,
    image_dir: str | Path,
    *,
    stem: str,
    timeout: float,
) -> dict[str, str] | None:
    try:
        import urllib.request

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "CoreMasterKB-mining/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        if not data:
            return None
        ext = mimetypes.guess_extension(ctype) if ctype else None
        if not ext:
            ext = Path(urlparse(url).path).suffix or ".bin"
        if ext == ".jpe":
            ext = ".jpg"
        return materialize_image_bytes(data, image_dir, stem=stem, ext=ext)
    except Exception as exc:
        logger.warning("Remote image fetch failed for %s: %s", url, exc)
        return None
