"""KB file storage path strategy + traversal guard."""
from __future__ import annotations

from pathlib import Path


def build_storage_path(upload_root: Path, kb_id: str,
                       directory_path: str | None, filename: str) -> Path:
    """Build the on-disk path for a KB file; reject any traversal outside the KB root."""
    base = (upload_root / kb_id).resolve()
    parts = [p for p in (directory_path or "").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("unsafe directory path")
    fname = Path(filename).name  # strip any path components from filename itself
    if not fname:
        raise ValueError("empty filename")
    candidate = base.joinpath(*parts, fname)
    full = candidate.resolve()
    try:
        full.relative_to(base)
    except ValueError as exc:
        raise ValueError("path escapes kb storage root") from exc
    return full


def build_folder_dir(upload_root: Path, kb_id: str, path: str | None) -> Path:
    """Build the on-disk directory for a KB folder (path = "5G/AMF" or "" for root).

    Rejects any traversal outside the KB root. ``path`` 用 "/" 分隔的相对路径段。
    """
    base = (upload_root / kb_id).resolve()
    parts = [p for p in (path or "").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("unsafe folder path")
    candidate = base.joinpath(*parts) if parts else base
    full = candidate.resolve()
    try:
        full.relative_to(base)
    except ValueError as exc:
        raise ValueError("path escapes kb storage root") from exc
    return full


def normalize_folder_name(name: str) -> str:
    """Validate + normalize a folder name (single segment, no slashes, no traversal)."""
    n = (name or "").strip()
    if not n:
        raise ValueError("folder name is empty")
    if "/" in n or "\\" in n or n in (".", ".."):
        raise ValueError(f"unsafe folder name: {name!r}")
    if any(c in n for c in '\x00\r\n\t'):
        raise ValueError(f"unsafe folder name: {name!r}")
    return n


def join_path(parent_path: str | None, name: str) -> str:
    """Compose a folder path from parent path + child name. 根 parent → path = name."""
    pp = (parent_path or "").strip().strip("/")
    return f"{pp}/{name}" if pp else name


def build_document_key(directory_path: str | None, filename: str) -> str:
    """Build document_key matching the mining pipeline's derivation.

    pipeline (jobs/run.py:880): ``doc_key = f"doc:/{relative_path}"`` where relative_path
    is the file path relative to the mining input dir. KB triggers mining with
    input = ``{upload_root}/{kb_id}/``, so relative_path = ``{directory_path}/{filename}``.
    """
    parts = [p for p in (directory_path or "").split("/") if p]
    parts.append(Path(filename).name)
    return "doc:/" + "/".join(parts)
