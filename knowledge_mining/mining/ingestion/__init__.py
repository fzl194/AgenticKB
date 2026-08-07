"""Ingestion module: recursive folder scan for v1.1.

Discovers md/txt/html/htm/pdf/doc/docx files plus compiled-help archives
(.chm / .hdx) which are auto-extracted and converted to markdown on the fly.
Produces RawFileData objects with raw_content_hash and normalized_content_hash.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from knowledge_mining.mining.contracts.models import BatchParams, RawFileData
from knowledge_mining.mining.infra.hash_utils import compute_raw_hash, compute_snapshot_hash
from knowledge_mining.mining.ingestion.preprocessing import (
    SUPPORTED_ARCHIVE_EXTS,
    archive_to_markdown,
    html_to_markdown,
)
from knowledge_mining.mining.ingestion.pdf_preprocessing import pdf_to_text
from knowledge_mining.mining.ingestion.doc_preprocessing import doc_to_docx
from knowledge_mining.mining.ingestion.errors import PreprocessingError
from knowledge_mining.mining.ingestion.excel_preprocessing import excel_to_markdown

logger = logging.getLogger(__name__)

_EXTENSION_MAP: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "txt",
    ".html": "html",
    ".htm": "html",
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "docx",
    ".xls": "xls",
    ".xlsx": "xlsx",
    # Compiled-help archives — extracted + converted to markdown during ingest.
    ".chm": "markdown",
    ".hdx": "markdown",
}

PARSABLE_EXTENSIONS = {".md", ".markdown", ".txt"}
PDF_EXTENSIONS = {".pdf"}
HTML_EXTENSIONS = {".html", ".htm"}
DOCX_EXTENSIONS = {".doc", ".docx"}
EXCEL_EXTENSIONS = {".xls", ".xlsx"}
PREPROCESS_EXTENSIONS = SUPPORTED_ARCHIVE_EXTS  # {".chm", ".hdx"}

_SKIP_NAMES = {
    "manifest.jsonl", "manifest.json",
    "html_to_md_mapping.json", "html_to_md_mapping.csv",
    ".ds_store", "thumbs.db", ".gitkeep",
}

_MIME_MAP: dict[str, str] = {
    "markdown": "text/markdown",
    "txt": "text/plain",
    "html": "text/html",
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def ingest_directory(
    input_path: Path,
    batch_params: BatchParams | None = None,
) -> tuple[list[RawFileData], dict[str, Any]]:
    """Recursively scan input_path for recognized files."""
    input_path = Path(input_path)
    batch_params = batch_params or BatchParams()

    documents: list[RawFileData] = []
    summary: dict[str, Any] = {
        "discovered_documents": 0,
        "parsed_documents": 0,
        "unparsed_documents": 0,
        "preprocessed_archives": 0,
        "preprocessed_pdfs": 0,
        "preprocessed_excels": 0,
        "skipped_files": 0,
        "failed_files": 0,
    }

    for file_path in sorted(input_path.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.name.lower() in _SKIP_NAMES:
            summary["skipped_files"] += 1
            continue

        rel_path = file_path.relative_to(input_path)
        ext = file_path.suffix.lower()
        file_type = _EXTENSION_MAP.get(ext)
        if file_type is None:
            summary["skipped_files"] += 1
            continue

        summary["discovered_documents"] += 1

        try:
            content_bytes = file_path.read_bytes()
            raw_hash = compute_raw_hash(content_bytes)
            metadata_json: dict[str, Any] = {}
            # Path the parser opens. Differs from file_path only for .doc, which
            # is converted to a sidecar .docx; provenance (source_uri/relative_
            # path/file_name) still points at the original .doc.
            parse_path = file_path

            if ext in PREPROCESS_EXTENSIONS:
                # .chm / .hdx — extract + convert to markdown on the fly.
                try:
                    content = archive_to_markdown(
                        file_path,
                        doc_title=file_path.stem,
                    )
                    summary["parsed_documents"] += 1
                    summary["preprocessed_archives"] += 1
                    metadata_json["source_format"] = ext.lstrip(".")
                except Exception as e:
                    logger.warning(
                        "preprocess failed for %s: %s; registering without content",
                        file_path, e,
                    )
                    content = ""
                    summary["unparsed_documents"] += 1
                    metadata_json["source_format"] = ext.lstrip(".")
                    metadata_json["preprocess_error"] = f"{type(e).__name__}: {e}"
            elif ext in PDF_EXTENSIONS:
                # .pdf — extract text via pdfminer.six on the fly.
                try:
                    content = pdf_to_text(file_path)
                    summary["parsed_documents"] += 1
                    summary["preprocessed_pdfs"] += 1
                    metadata_json["source_format"] = "pdf"
                except Exception as e:
                    logger.warning(
                        "pdf preprocessing failed for %s: %s; registering without content",
                        file_path, e,
                    )
                    content = ""
                    summary["unparsed_documents"] += 1
                    metadata_json["source_format"] = "pdf"
                    metadata_json["preprocess_error"] = f"{type(e).__name__}: {e}"
            elif ext in HTML_EXTENSIONS:
                # .html / .htm — convert to markdown via shared renderer.
                try:
                    content = html_to_markdown(file_path, doc_title=file_path.stem)
                    file_type = "markdown"  # converted, will use MarkdownParser
                    summary["parsed_documents"] += 1
                    metadata_json["source_format"] = "html"
                except Exception as e:
                    logger.warning(
                        "html preprocessing failed for %s: %s; registering without content",
                        file_path, e,
                    )
                    content = ""
                    summary["unparsed_documents"] += 1
                    metadata_json["source_format"] = "html"
                    metadata_json["preprocess_error"] = f"{type(e).__name__}: {e}"
            elif ext == ".doc":
                # Legacy binary .doc — python-docx cannot read it ("Package not
                # found"). Convert to .docx (LibreOffice/Word backend) so it
                # flows through the structural DocxParser instead of being
                # flattened to plain text.
                try:
                    parse_path = doc_to_docx(file_path)
                    file_type = "docx"  # converted, will use DocxParser
                    content = ""  # docx parsed from file, like native .docx
                    summary["parsed_documents"] += 1
                    metadata_json["source_format"] = "doc"
                except PreprocessingError as exc:
                    logger.warning(
                        "doc->docx conversion failed for %s [%s]: %s; "
                        "registering without content",
                        file_path,
                        exc.code,
                        exc.safe_message,
                    )
                    content = ""
                    summary["unparsed_documents"] += 1
                    metadata_json.update({
                        "source_format": "doc",
                        **exc.as_metadata(),
                    })
                except Exception as e:
                    logger.warning(
                        "doc->docx conversion failed for %s: %s; registering without content",
                        file_path, e,
                    )
                    content = ""
                    summary["unparsed_documents"] += 1
                    metadata_json["source_format"] = "doc"
                    metadata_json["preprocess_error"] = f"{type(e).__name__}: {e}"
            elif ext in EXCEL_EXTENSIONS:
                try:
                    excel = excel_to_markdown(file_path)
                    content = excel.markdown
                    file_type = "markdown"
                    summary["parsed_documents"] += 1
                    summary["preprocessed_excels"] += 1
                    metadata_json.update({
                        "source_format": excel.source_format,
                        "preprocess_status": excel.status,
                        "preprocess_warnings": [
                            item.as_dict() for item in excel.warnings
                        ],
                        "excel_summary": excel.summary,
                    })
                except PreprocessingError as exc:
                    logger.warning(
                        "excel preprocessing failed for %s [%s]: %s; "
                        "registering without content",
                        file_path,
                        exc.code,
                        exc.safe_message,
                    )
                    content = ""
                    summary["unparsed_documents"] += 1
                    metadata_json.update({
                        "source_format": ext.lstrip("."),
                        **exc.as_metadata(),
                    })
            elif ext in DOCX_EXTENSIONS:
                # .docx — zip package; DocxParser reads file_path directly.
                content = ""
                summary["parsed_documents"] += 1
                metadata_json["source_format"] = ext.lstrip(".")
            elif ext in PARSABLE_EXTENSIONS:
                content = content_bytes.decode("utf-8", errors="replace")
                summary["parsed_documents"] += 1
            else:
                content = ""
                summary["unparsed_documents"] += 1

            normalized_hash = compute_snapshot_hash(content) if content else raw_hash

            doc = RawFileData(
                file_path=str(parse_path),
                relative_path=str(rel_path).replace("\\", "/"),
                file_name=file_path.name,
                file_type=file_type,
                content=content,
                raw_content_hash=raw_hash,
                normalized_content_hash=normalized_hash,
                file_size=len(content_bytes),
                source_uri=str(file_path),
                source_type=batch_params.default_source_type,
                document_type=batch_params.default_document_type,
                title=_infer_title(file_path, content, file_type),
                scope_json=dict(batch_params.batch_scope),
                tags_json=list(batch_params.tags),
                metadata_json=metadata_json,
            )
            documents.append(doc)
        except Exception:
            summary["failed_files"] += 1

    return documents, summary


def get_mime_type(file_type: str) -> str:
    """Map file_type to MIME type for asset_document_snapshots."""
    return _MIME_MAP.get(file_type, "application/octet-stream")


def _infer_title(file_path: Path, content: str, file_type: str) -> str | None:
    """Infer document title: H1 for markdown, filename for others."""
    if file_type == "markdown" and content:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
    return file_path.stem
