"""Archive extraction — pure Python (ZIP only).

RAR is a proprietary format with no pure-Python decoder, so we don't
auto-extract it. Users should convert RAR to ZIP before uploading, or
extract locally and upload the individual files.
"""
from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ExtractResult:
    """Result of archive extraction."""
    extracted_files: list[str] = field(default_factory=list)
    error: str | None = None


def _is_safe_path(member_path: str, dest_dir: Path) -> bool:
    """Check that extracting member_path under dest_dir won't escape dest_dir."""
    resolved = (dest_dir / member_path).resolve()
    return resolved.is_relative_to(dest_dir.resolve())


def _fix_zip_filename(info: zipfile.ZipInfo) -> str:
    """Handle Chinese filenames in ZIP archives.

    ZIP files created on Windows may encode filenames in GBK instead of UTF-8.
    The UTF-8 flag (bit 11) in flag_bits indicates whether the filename is UTF-8.
    """
    raw_name = info.filename
    if not raw_name:
        return raw_name
    if info.flag_bits & 0x800:
        return raw_name
    try:
        raw_bytes = raw_name.encode("cp437")
        return raw_bytes.decode("gbk")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return raw_name


_COPY_CHUNK = 256 * 1024


def extract_zip(
    archive_path: Path,
    dest_dir: Path,
    *,
    max_members: int | None = None,
    max_member_bytes: int | None = None,
    max_total_bytes: int | None = None,
) -> ExtractResult:
    """Extract a ZIP archive with path-traversal protection and Chinese filename support.

    P01-S1：分块拷贝（不整读成员）+ 三重解压额度（成员数/单成员/总量）——
    zip-bomb 的解压总量可以比压缩包大几个数量级，必须在解压时强制。
    额度为 None 表示不限（兼容既有内部调用方；生产上传路径显式传限）。
    """
    extracted: list[str] = []
    total = 0

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            infos = zf.infolist()
            if max_members is not None and len(infos) > max_members:
                return ExtractResult(
                    error=f"ZIP 成员数 {len(infos)} 超过上限 {max_members}",
                )
            for info in infos:
                if info.is_dir():
                    continue

                safe_name = _fix_zip_filename(info)
                member_path = Path(safe_name).as_posix()

                if not _is_safe_path(member_path, dest_dir):
                    return ExtractResult(
                        error=f"Path traversal detected in ZIP member: {safe_name}",
                    )

                out_path = dest_dir / member_path
                out_path.parent.mkdir(parents=True, exist_ok=True)

                member_size = 0
                with zf.open(info) as src, open(out_path, "wb") as dst:
                    while True:
                        block = src.read(_COPY_CHUNK)
                        if not block:
                            break
                        member_size += len(block)
                        total += len(block)
                        if max_member_bytes is not None and member_size > max_member_bytes:
                            return ExtractResult(
                                error=(f"ZIP 成员 {safe_name} 解压后 "
                                       f"{member_size} 字节超过单成员上限 {max_member_bytes}"),
                            )
                        if max_total_bytes is not None and total > max_total_bytes:
                            return ExtractResult(
                                error=(f"ZIP 解压总量 {total} 字节超过上限 "
                                       f"{max_total_bytes}（疑似 zip 炸弹）"),
                            )
                        dst.write(block)

                extracted.append(member_path)

    except zipfile.BadZipFile as exc:
        return ExtractResult(error=f"Bad ZIP file: {exc}")
    except Exception as exc:
        return ExtractResult(error=f"ZIP extraction failed: {exc}")

    return ExtractResult(extracted_files=extracted)


def extract_archive(archive_path: Path, dest_dir: Path) -> ExtractResult:
    """Extract a ZIP archive. On success, deletes the original archive file."""
    ext = archive_path.suffix.lower()

    if ext == ".zip":
        result = extract_zip(archive_path, dest_dir)
    else:
        return ExtractResult(
            error=f"不支持自动解压 {ext} 格式，请使用 ZIP 或手动解压后上传",
        )

    if result.error is None and result.extracted_files:
        try:
            archive_path.unlink()
            logger.info(
                "Extracted %s → %d files, archive deleted",
                archive_path.name, len(result.extracted_files),
            )
        except OSError as exc:
            logger.warning("Failed to delete archive %s: %s", archive_path, exc)

    return result
