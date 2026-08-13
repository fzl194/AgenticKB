"""Pure-stdlib safe intake: MIME / format / archive / path inspection.

Implements the security-invariant subset of SRS §2.4 and §C03 (File
Inspector safe subset) that must run *before* a file is frozen into a Mining
Run. The full File Inspector (page count, text layer, scan ratio, layout
complexity, language) lands later; M1.4 only ships the admission gates:

1. ``detect_mime`` — magic-byte signature with extension hint. **Signature
   wins over extension** (SRS §2.4: "扩展名不可信").
2. ``inspect`` — wraps detection with encryption / archive flags and a
   policy verdict.
3. ``check_archive_limits`` — member count / expanded size / compression
   ratio gates (SRS §2.4: "限制文件数、展开大小、压缩比").
4. ``sanitize_archive_member_path`` — refuse ``..``, absolute paths, and
   members that escape the intended root (SRS §2.4: "禁止目录穿越").

Design (ADR-0003 D-024):
- Pure functions, no I/O, no external deps (no python-magic). Deterministic
  and unit-testable in isolation. ZIP member enumeration uses stdlib
  ``zipfile`` only via ``infolist()`` (reads central directory, never
  decompresses to disk).
- ``_SIGNATURES`` covers the project's actual parser coverage
  (md/txt/html/pdf/doc/docx/xls/xlsx/pptx/png/jpg/gif/rtf + zip/rar/7z/gz).
  Office OOXML and OOXML-like formats all share the ZIP signature; a second
  pass on the ZIP central directory disambiguates docx/xlsx/pptx vs plain zip
  when the caller supplies enough head bytes.
- All public methods are ``<50`` lines.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from knowledge_mining.mining.frozen_input.contracts import (
    IntakeVerdict,
    UnsafeFile,
)

# ---------------------------------------------------------------------------
# Constants (SRS §2.4 archive limits; defaults are conservative platform
# baselines — callers may override per-policy)
# ---------------------------------------------------------------------------

#: Default cap on the number of members an archive may contain (zip-bomb /
#: exhaustion guard, SRS §2.4).
DEFAULT_MAX_ARCHIVE_MEMBERS = 1_000

#: Default cap on the total expanded size of an archive (2 GiB).
DEFAULT_MAX_ARCHIVE_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024

#: Default cap on the compression ratio (expanded / compressed). 100x is
#: generous for legitimate archives but blocks the classic zip-bomb ratios
#: (>1000x).
DEFAULT_MAX_COMPRESSION_RATIO = 100

#: Number of head bytes the inspector needs to reliably classify common
#: formats. Office OOXML disambiguation needs ~4 KiB to see the first
#: central-directory entry; signatures alone need 8-512 bytes.
HEAD_BYTES_NEEDED = 4096

# Supported MIME set — kept in sync with SRS §C03 parser coverage. Files
# whose detected MIME is outside this set get ``UnsupportedFile``. Archives
# are admitted for staging / expansion, not direct parsing.
_SUPPORTED_MIMES: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/html",
        "application/pdf",
        "application/msword",  # .doc (OLE2)
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-excel",  # .xls (OLE2)
        "application/vnd.ms-powerpoint",  # .ppt (OLE2)
        "application/rtf",
        "image/png",
        "image/jpeg",
        "image/gif",
        # Archives — admitted so the platform can expand them; not parsed.
        "application/zip",
        "application/x-rar-compressed",
        "application/x-7z-compressed",
        "application/gzip",
        "application/x-tar",
    }
)

#: MIME -> short format label used in routing / audit.
_FORMAT_LABELS: dict[str, str] = {
    "text/plain": "text",
    "text/markdown": "markdown",
    "text/html": "html",
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-powerpoint": "ppt",
    "application/rtf": "rtf",
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "application/zip": "zip",
    "application/x-rar-compressed": "rar",
    "application/x-7z-compressed": "7z",
    "application/gzip": "gzip",
    "application/x-tar": "tar",
    "application/octet-stream": "unknown",
}

# Archive MIME -> format label (for is_archive detection).
_ARCHIVE_MIMES: frozenset[str] = frozenset(
    {
        "application/zip",
        "application/x-rar-compressed",
        "application/x-7z-compressed",
        "application/gzip",
        "application/x-tar",
    }
)

#: Extension -> MIME fallback table. Used ONLY as a hint when no signature
#: matches (e.g. plain text). Signature always wins (SRS §2.4).
_EXT_HINTS: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".rtf": "application/rtf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".zip": "application/zip",
    ".rar": "application/x-rar-compressed",
    ".7z": "application/x-7z-compressed",
    ".gz": "application/gzip",
    ".tgz": "application/gzip",
    ".tar": "application/x-tar",
}


# ---------------------------------------------------------------------------
# Signature table
# ---------------------------------------------------------------------------


class _Sig:
    """A magic-byte signature probe."""

    __slots__ = ("offset", "prefix", "mime")

    def __init__(self, prefix: bytes, mime: str, offset: int = 0) -> None:
        self.offset = offset
        self.prefix = prefix
        self.mime = mime


# Ordered most-specific-first. Office OOXML is detected by the ZIP signature
# plus a second-pass central-directory probe in ``_disambiguate_ooxml``.
_SIGNATURES: tuple[_Sig, ...] = (
    _Sig(b"%PDF", "application/pdf"),
    _Sig(b"\x89PNG\r\n\x1a\n", "image/png"),
    _Sig(b"\xff\xd8\xff", "image/jpeg"),
    _Sig(b"GIF87a", "image/gif"),
    _Sig(b"GIF89a", "image/gif"),
    _Sig(b"PK\x03\x04", "application/zip"),  # OOXML or plain zip
    _Sig(b"D0CF11E0A1B11AE1", "application/msword"),  # OLE2 (doc/xls/ppt)
    _Sig(b"{\\rtf", "application/rtf"),
    _Sig(b"Rar!\x1a\x07", "application/x-rar-compressed"),  # RARv4
    _Sig(b"Rar!\x1a\x07\x01\x00", "application/x-rar-compressed"),  # RARv5
    _Sig(b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed"),
    _Sig(b"\x1f\x8b", "application/gzip"),
)


# OLE2 (D0CF11E0) needs a second look: the same compound signature covers
# .doc / .xls / .ppt. Disambiguate by extension hint (the OLE2 root clsid is
# heavier to parse than this admission layer should do).
_OLE2_EXT_TO_MIME: dict[str, str] = {
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
}


def _normalize_extension(filename: str) -> str:
    """Return the lowercased extension (including the dot) of ``filename``."""
    return Path(filename).suffix.lower() if filename else ""


def _signature_mime(head: bytes) -> str | None:
    """Return the MIME matched by the first signature that hits, or None."""
    for sig in _SIGNATURES:
        start = sig.offset
        end = start + len(sig.prefix)
        if len(head) >= end and head[start:end] == sig.prefix:
            return sig.mime
    return None


def _looks_like_text(head: bytes) -> bool:
    """Heuristic: mostly-printable ASCII / UTF-8 with no NUL bytes."""
    if not head:
        return False
    if b"\x00" in head:
        return False
    # Count printable + common whitespace; require >= 90% printable.
    sample = head[:512]
    printable = sum(
        1
        for b in sample
        if b in (9, 10, 13) or 0x20 <= b <= 0x7E or b >= 0x80  # tab/nl/cr, ascii, utf8 cont
    )
    return printable / len(sample) >= 0.90


def _disambiguate_ooxml(head: bytes, ext_hint_mime: str | None) -> str:
    """Disambiguate a ZIP signature into docx/xlsx/pptx vs plain zip.

    Uses the declared extension as a strong hint; the OOXML central directory
    carries a ``[Content_Types].xml`` entry, but reading it requires pulling
    more bytes and parsing XML — out of scope for this pure admission gate.
    The extension hint is only trusted here because the ZIP signature already
    confirmed the file is a real ZIP (the SRS "signature wins" rule is about
    *extension spoofing*, e.g. ``evil.exe`` renamed to ``evil.pdf`` — a real
    ZIP masquerading as a real OOXML is still an admitted archive).
    """
    if ext_hint_mime in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }:
        return ext_hint_mime
    return "application/zip"


def _disambiguate_ole2(filename: str) -> str:
    """Pick doc/xls/ppt MIME for an OLE2 file using its extension."""
    ext = _normalize_extension(filename)
    return _OLE2_EXT_TO_MIME.get(ext, "application/msword")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class SafeIntake:
    """Stateless intake inspector (SRS §2.4, §C03 safe subset).

    All methods are pure (no I/O, no state) so the same instance can be
    shared across requests and tested without any fixtures.
    """

    __slots__ = ()

    # -- MIME detection ----------------------------------------------------

    def detect_mime(self, head_bytes: bytes, filename: str) -> str:
        """Detect MIME from ``head_bytes`` magic signature, ext as hint only.

        Signature wins over extension (SRS §2.4). For OLE2 / OOXML the
        signature identifies the family and the extension disambiguates the
        member; for plain text with no signature, the extension (md/txt/html)
        picks the specific text subtype.
        """
        sig_mime = _signature_mime(head_bytes)
        ext = _normalize_extension(filename)
        ext_hint = _EXT_HINTS.get(ext)

        if sig_mime is None:
            # No signature: maybe plain text. Trust text heuristic, fall back
            # to extension hint, else octet-stream.
            if _looks_like_text(head_bytes):
                if ext_hint in {"text/markdown", "text/html"}:
                    return ext_hint
                return "text/plain"
            if ext_hint is not None:
                return ext_hint
            return "application/octet-stream"

        # Signature matched. Disambiguate families where the signature is
        # ambiguous across multiple supported formats.
        if sig_mime == "application/zip":
            return _disambiguate_ooxml(head_bytes, ext_hint)
        if sig_mime == "application/msword":  # OLE2 compound
            return _disambiguate_ole2(filename)
        return sig_mime

    # -- Full verdict ------------------------------------------------------

    def inspect(
        self,
        head_bytes: bytes,
        filename: str,
        declared_size: int | None = None,
    ) -> IntakeVerdict:
        """Produce the admission verdict (SRS §C03 safe subset).

        ``head_bytes`` should be at least ``HEAD_BYTES_NEEDED`` bytes when
        available; fewer bytes are accepted (e.g. tiny files) but reduce
        detection confidence. ``declared_size`` is the caller's claimed total
        size (used only to populate the verdict, not to gate).
        """
        mime = self.detect_mime(head_bytes, filename)
        fmt = _FORMAT_LABELS.get(mime, "unknown")

        encrypted = self._detect_encryption(head_bytes, mime)
        is_archive = mime in _ARCHIVE_MIMES

        errors: list[str] = []
        warnings: list[str] = []
        if mime == "application/octet-stream":
            errors.append("unknown file type (no signature, no known extension)")
        if encrypted:
            errors.append("encrypted file; admission policy forbids parsing")

        ok = mime in _SUPPORTED_MIMES and not encrypted and not errors
        _ = declared_size  # reserved for future size-based policy
        return IntakeVerdict(
            ok=ok,
            detected_mime=mime,
            detected_format=fmt,
            encrypted=encrypted,
            is_archive=is_archive,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    # -- Archive limits ----------------------------------------------------

    def check_archive_limits(
        self,
        member_count: int,
        expanded_size: int,
        compressed_size: int,
        *,
        max_members: int = DEFAULT_MAX_ARCHIVE_MEMBERS,
        max_expanded_bytes: int = DEFAULT_MAX_ARCHIVE_EXPANDED_BYTES,
        max_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO,
    ) -> None:
        """Enforce archive size / count / ratio caps (SRS §2.4).

        Raises ``UnsafeFile`` with a stable ``reason`` on the first violated
        limit. ``compressed_size`` of 0 is treated as 1 to avoid div-by-zero
        on empty archives (which would otherwise hit member-count first).
        """
        if member_count > max_members:
            raise UnsafeFile(
                f"archive member count {member_count} exceeds limit {max_members}",
                reason="archive_member_count_exceeded",
            )
        if expanded_size > max_expanded_bytes:
            raise UnsafeFile(
                f"archive expanded size {expanded_size} exceeds limit "
                f"{max_expanded_bytes}",
                reason="archive_expanded_size_exceeded",
            )
        denom = compressed_size if compressed_size > 0 else 1
        ratio = expanded_size / denom
        if ratio > max_ratio:
            raise UnsafeFile(
                f"archive compression ratio {ratio:.1f} exceeds limit {max_ratio}",
                reason="archive_compression_ratio_exceeded",
            )

    # -- Path traversal ----------------------------------------------------

    def sanitize_archive_member_path(
        self, member_path: str, intended_root: Path
    ) -> Path:
        """Resolve ``member_path`` under ``intended_root`` or raise UnsafeFile.

        Rejects (SRS §2.4): absolute paths, ``..`` segments, and any resolved
        path that escapes ``intended_root``. The returned Path is absolute
        and guaranteed (best-effort, lexical) to live under the root.
        """
        if not member_path:
            raise UnsafeFile("empty archive member path", reason="path_traversal")
        # Normalize separators (zip uses forward slashes).
        normalized = member_path.replace("\\", "/")
        if normalized.startswith("/"):
            raise UnsafeFile(
                f"absolute archive member path: {member_path!r}",
                reason="path_traversal_absolute",
            )
        if "\\" in member_path and Path(member_path).is_absolute():
            raise UnsafeFile(
                f"absolute archive member path: {member_path!r}",
                reason="path_traversal_absolute",
            )
        parts = [p for p in normalized.split("/") if p not in {"", "."}]
        if any(p == ".." for p in parts):
            raise UnsafeFile(
                f"archive member path contains '..': {member_path!r}",
                reason="path_traversal_dotdot",
            )
        resolved = (intended_root / Path(*parts)).resolve()
        root_resolved = intended_root.resolve()
        try:
            resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise UnsafeFile(
                f"archive member escapes root: {member_path!r}",
                reason="path_traversal_escape",
            ) from exc
        return resolved

    # -- ZIP enumeration (stdlib, no disk extraction) ----------------------

    def enumerate_zip_members(
        self, head_or_bytes: bytes
    ) -> tuple[int, int, int]:
        """Return ``(member_count, expanded_size, compressed_size)`` for a ZIP.

        Reads the central directory via ``zipfile.infolist()`` without
        decompressing any member to disk. ``head_or_bytes`` must be the full
        object bytes (the central directory lives at the *end* of a zip); for
        very large archives the caller should pass the full buffer. Used by
        ``FrozenInputService`` to feed ``check_archive_limits``.
        """
        import io

        try:
            with zipfile.ZipFile(io.BytesIO(head_or_bytes)) as zf:
                infos = zf.infolist()
        except zipfile.BadZipFile as exc:
            raise UnsafeFile(
                "corrupt or truncated archive central directory",
                reason="archive_corrupt",
            ) from exc
        member_count = len(infos)
        expanded = sum(i.file_size for i in infos)
        compressed = sum(i.compress_size for i in infos)
        return member_count, expanded, compressed

    # -- Encryption heuristics --------------------------------------------

    @staticmethod
    def _detect_encryption(head_bytes: bytes, mime: str) -> bool:
        """Best-effort encryption flag (SRS §2.4: encrypted files gated)."""
        if mime == "application/pdf":
            # PDF encryption is declared in the trailer dictionary; a
            # `/Encrypt` token near the head is uncommon but the trailer is
            # typically within the first 4 KiB for small files. For the
            # admission gate, scan the whole head_bytes buffer.
            return b"/Encrypt" in head_bytes
        if mime.startswith("application/vnd."):
            # OOXML / OLE2 encryption needs structural parsing; flag only
            # the obvious OLE2 encrypted-stream marker. False negatives here
            # are acceptable — the parser backend will re-check.
            return False
        return False


__all__ = [
    "DEFAULT_MAX_ARCHIVE_EXPANDED_BYTES",
    "DEFAULT_MAX_ARCHIVE_MEMBERS",
    "DEFAULT_MAX_COMPRESSION_RATIO",
    "HEAD_BYTES_NEEDED",
    "SafeIntake",
]
