"""Legacy .doc preprocessing for ingestion: binary Word (.doc) -> .docx file.

The old binary .doc (Word 97-2003) format is NOT a zip package, so python-docx
cannot read it -- it raises "Package not found". Rather than flatten a .doc to
plain text (which loses headings/tables), this module converts it to a real
.docx file so it flows through the structural ``DocxParser`` like a native
.docx. A fallback chain mirrors the .chm extractor (prefer a cross-platform
tool, fall back, else clear error):

  1. LibreOffice ``soffice --headless --convert-to docx`` -- cross-platform,
     preferred. Looked up on PATH and at common install locations. This is the
     backend baked into the Docker image, so it is the production path.
  2. Microsoft Word via COM automation (``win32com``) -- Windows only, used as
     a dev convenience when LibreOffice is unavailable but Word is installed.
  3. Neither available -> a stable ``PreprocessingError`` with an actionable
     message.

The converted .docx is written into a process-lifetime staging directory
(removed at interpreter exit). It must outlive ingestion because the parse
stage re-opens it via python-docx later in the same run.

Public API:

    doc_to_docx(src) -> Path        (path to the converted .docx)
"""
from __future__ import annotations

import atexit
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from knowledge_mining.mining.ingestion.errors import PreprocessingError

# Common LibreOffice install locations checked when soffice is not on PATH.
_SOFFICE_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/opt/libreoffice/program/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)

# wdFormatDocumentDefault -- Word's modern .docx format (used by Word COM).
_WD_FORMAT_DOCX = 16

_STAGING_ROOT: Path | None = None


def _staging_root() -> Path:
    """Process-lifetime dir holding converted .docx files (cleaned at exit).

    The parse stage re-opens converted files after ingestion finishes, so they
    cannot be deleted eagerly. A single staging root per process keeps them
    alive for the whole run and is removed via ``atexit`` when the (one-shot
    mining job) process exits.
    """
    global _STAGING_ROOT
    if _STAGING_ROOT is None:
        _STAGING_ROOT = Path(tempfile.mkdtemp(prefix="mining_doc2docx_"))
        atexit.register(shutil.rmtree, _STAGING_ROOT, ignore_errors=True)
    return _STAGING_ROOT


def _find_soffice() -> str | None:
    """Return a usable soffice/libreoffice executable path, or None."""
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for cand in _SOFFICE_CANDIDATES:
        if Path(cand).exists():
            return cand
    return None


def _word_com_available() -> bool:
    """True if MS Word COM automation can be used (Windows + pywin32)."""
    if sys.platform != "win32":
        return False
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        return False
    return True


def _convert_with_soffice(src: Path, soffice: str, out_dir: Path) -> Path:
    """Convert .doc -> .docx via LibreOffice headless. Returns the .docx path."""
    # Isolate the LibreOffice user profile per conversion: this both supplies a
    # writable profile dir in containers that have no $HOME and avoids the
    # single-instance profile lock when conversions run back-to-back.
    user_install = (out_dir / "louser").as_uri()
    result = subprocess.run(
        [
            soffice,
            f"-env:UserInstallation={user_install}",
            "--headless", "--convert-to", "docx",
            "--outdir", str(out_dir), str(src.resolve()),
        ],
        check=False,
        capture_output=True,
        timeout=120,
    )
    out = out_dir / (src.stem + ".docx")
    if not out.is_file():
        raise RuntimeError(
            f"LibreOffice produced no docx for {src.name}: "
            f"{result.stderr.decode('utf-8', errors='replace')}"
        )
    return out


def _convert_with_word_com(src: Path, out_dir: Path) -> Path:
    """Convert .doc -> .docx via MS Word COM automation (Windows only)."""
    import pythoncom
    import win32com.client as win32

    out = out_dir / (src.stem + ".docx")
    pythoncom.CoInitialize()
    word = None
    try:
        word = win32.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = False
        doc = word.Documents.Open(str(src.resolve()), ReadOnly=True)
        try:
            doc.SaveAs(str(out), FileFormat=_WD_FORMAT_DOCX)
        finally:
            doc.Close(False)
    finally:
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()
    if not out.is_file():
        raise RuntimeError(f"Word COM produced no docx for {src.name}")
    return out


def doc_to_docx(src: Path) -> Path:
    """Convert a legacy binary .doc file to a .docx file.

    Backend chain: LibreOffice (cross-platform) -> MS Word COM (Windows). The
    converted .docx lives in a process-lifetime staging dir so the parse stage
    can re-open it. Raises FileNotFoundError if ``src`` is missing and a typed
    ``PreprocessingError`` for converter availability or conversion failures.
    """
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(src)

    out_dir = Path(tempfile.mkdtemp(dir=_staging_root()))

    soffice = _find_soffice()
    if soffice is not None:
        try:
            return _convert_with_soffice(src, soffice, out_dir)
        except Exception as exc:
            raise PreprocessingError(
                "doc_conversion_failed",
                f"Failed to convert legacy Word document: {src.name}",
            ) from exc

    if _word_com_available():
        try:
            return _convert_with_word_com(src, out_dir)
        except Exception as exc:
            raise PreprocessingError(
                "doc_conversion_failed",
                f"Failed to convert legacy Word document: {src.name}",
            ) from exc

    raise PreprocessingError(
        "doc_converter_unavailable",
        f"No .doc converter available for {src.name}; install LibreOffice "
        "or save the document as .docx",
    )
