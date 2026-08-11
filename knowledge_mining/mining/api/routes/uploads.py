"""File upload routes — multipart upload with domain-scoped storage, archive extraction."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from knowledge_mining.mining.infra.archive_extractor import extract_archive
from knowledge_mining.mining.infra.mining_config import MiningConfig
from knowledge_mining.mining.infra.upload_config import UploadConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

_cfg = UploadConfig()

_DOMAIN_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')
_BATCH_PATTERN = re.compile(r"^[a-f0-9]{12}$")

_ACCEPTED_EXTENSIONS = [
    ".md", ".txt", ".pdf", ".html", ".htm", ".doc", ".docx", ".xls",
    ".xlsx", ".zip", ".chm", ".hdx",
]


def _validate_domain(domain: str) -> str:
    """Sanitize domain: only allow alphanumeric, underscore, hyphen."""
    if not _DOMAIN_PATTERN.fullmatch(domain):
        raise HTTPException(400, "Invalid domain name: only alphanumeric, underscore, hyphen allowed")
    return domain


def _get_file_ext(filename: str) -> str:
    return Path(filename).suffix.lower()


def _is_archive(filename: str) -> bool:
    return _get_file_ext(filename) in _cfg.archive_exts_set


def _size_limit_for(filename: str) -> int:
    """Return the applicable size limit based on file type."""
    return _cfg.upload_max_archive_size if _is_archive(filename) else _cfg.upload_max_file_size


@router.get("/config")
async def get_upload_config() -> dict[str, Any]:
    """Return upload configuration for client-side validation."""
    return {
        "max_file_size": _cfg.upload_max_file_size,
        "max_archive_size": _cfg.upload_max_archive_size,
        "max_files_per_request": _cfg.upload_max_files_per_request,
        "max_file_size_mb": _cfg.max_file_size_mb,
        "max_archive_size_mb": _cfg.max_archive_size_mb,
        "accepted_extensions": _ACCEPTED_EXTENSIONS,
        "archive_extensions": list(_cfg.archive_exts_set),
        "mining_run_submission_engine": MiningConfig().mining_run_submission_engine,
    }


def resolve_upload_batch_path(domain: str, upload_batch_id: str) -> Path:
    safe_domain = _validate_domain(domain)
    if not _BATCH_PATTERN.fullmatch(upload_batch_id):
        raise HTTPException(422, "Invalid upload_batch_id")
    root = _cfg.upload_root_path.resolve()
    candidate = (root / safe_domain / upload_batch_id).resolve()
    if root not in candidate.parents or not candidate.is_dir():
        raise HTTPException(404, "Upload batch not found")
    return candidate


@router.post("")
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    domain: str = Form("cloud_core_network"),
) -> dict[str, Any]:
    """Upload files to a domain-scoped batch directory.

    Archive files (.zip, .rar) are automatically extracted after upload.
    Returns upload_batch_id, storage_path, and extracted_archives info.
    """
    if len(files) > _cfg.upload_max_files_per_request:
        raise HTTPException(400, f"Too many files: max {_cfg.upload_max_files_per_request}")

    domain = _validate_domain(domain)

    batch_id = uuid.uuid4().hex[:12]
    upload_root = _cfg.upload_root_path
    dest_dir = upload_root / domain / batch_id

    # Belt-and-suspenders: ensure resolved path stays under upload_root
    dest_dir = dest_dir.resolve()
    if not str(dest_dir).startswith(str(upload_root)):
        raise HTTPException(400, "Invalid path")

    # Check disk space before writing
    if dest_dir.exists():
        disk = shutil.disk_usage(dest_dir)
    else:
        # Check parent that exists
        check_dir = dest_dir
        while not check_dir.exists():
            check_dir = check_dir.parent
        disk = shutil.disk_usage(check_dir)

    if disk.free < _cfg.upload_disk_reserve_bytes:
        raise HTTPException(507, "Insufficient disk space")

    dest_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    for f in files:
        if not f.filename:
            continue
        safe_name = Path(f.filename).name
        if not safe_name:
            continue
        file_path = dest_dir / safe_name

        limit = _size_limit_for(safe_name)

        # Chunked write with size limit
        total_size = 0
        with open(file_path, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > limit:
                    # Clean up partial file
                    file_path.unlink(missing_ok=True)
                    limit_mb = limit // (1024 * 1024)
                    raise HTTPException(400, f"File {safe_name} exceeds {limit_mb}MB limit")
                out.write(chunk)
        saved.append(safe_name)

    if not saved:
        raise HTTPException(400, "No valid files in upload")

    # Extract archives
    extracted_archives: list[dict[str, Any]] = []
    for fname in list(saved):  # iterate over copy since we may modify
        if _is_archive(fname):
            archive_path = dest_dir / fname
            result = await asyncio.to_thread(extract_archive, archive_path, dest_dir)
            if result.error:
                # Extraction failed — keep the archive, report error
                extracted_archives.append({
                    "archive": fname,
                    "error": result.error,
                    "file_count": 0,
                    "files": [],
                })
            else:
                extracted_archives.append({
                    "archive": fname,
                    "error": None,
                    "file_count": len(result.extracted_files),
                    "files": result.extracted_files,
                })
                # Remove archive from saved list (it's been extracted and deleted)
                if fname in saved:
                    saved.remove(fname)
                # Add extracted files
                saved.extend(result.extracted_files)

    storage_path = str(dest_dir)

    logger.info(
        "Uploaded %d files to %s (domain=%s, archives=%d)",
        len(saved), storage_path, domain, len(extracted_archives),
    )

    return {
        "upload_batch_id": batch_id,
        "domain": domain,
        "file_count": len(saved),
        "files": saved,
        "storage_path": storage_path,
        "extracted_archives": extracted_archives,
    }


@router.get("")
async def list_uploads(
    request: Request,
    domain: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List upload batches from the filesystem."""
    if domain:
        domain = _validate_domain(domain)

    upload_root = _cfg.upload_root_path
    if not upload_root.exists():
        return {"items": []}

    items: list[dict[str, Any]] = []

    scan_dirs = [upload_root / domain] if domain else [
        d for d in upload_root.iterdir() if d.is_dir()
    ]

    for domain_dir in scan_dirs:
        if not domain_dir.is_dir():
            continue
        domain_name = domain_dir.name
        for batch_dir in sorted(domain_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not batch_dir.is_dir():
                continue
            files = [f.name for f in batch_dir.iterdir() if f.is_file()]
            items.append({
                "upload_batch_id": batch_dir.name,
                "domain": domain_name,
                "file_count": len(files),
                "files": files,
                "storage_path": str(batch_dir),
            })
            if len(items) >= limit:
                return {"items": items}

    return {"items": items}
