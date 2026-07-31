"""Upload configuration — 来源：main_control_service。

配置来自 ``GET /api/v1/system/mining/raw`` 的 ``upload`` 段
（main_control_service/config/system/mining.yaml）。**不再读 .env。**
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import computed_field

from .control_plane import get_mining_service_config

_DEFAULT_UPLOAD = {
    "root": "./uploads",
    "max_file_size": 100 * 1024 * 1024,        # 100MB
    "max_archive_size": 500 * 1024 * 1024,     # 500MB
    "max_files_per_request": 100,
    "disk_reserve_bytes": 1024 * 1024 * 1024,  # 1GB
    "archive_extensions": ".zip",
}


def _upload_from_control_plane() -> dict[str, Any]:
    data = get_mining_service_config().get("upload") or {}
    return {
        "upload_root": data.get("root", _DEFAULT_UPLOAD["root"]),
        "upload_max_file_size": int(data.get("max_file_size", _DEFAULT_UPLOAD["max_file_size"])),
        "upload_max_archive_size": int(data.get("max_archive_size", _DEFAULT_UPLOAD["max_archive_size"])),
        "upload_max_files_per_request": int(data.get("max_files_per_request", _DEFAULT_UPLOAD["max_files_per_request"])),
        "upload_disk_reserve_bytes": int(data.get("disk_reserve_bytes", _DEFAULT_UPLOAD["disk_reserve_bytes"])),
        "upload_archive_extensions": data.get("archive_extensions", _DEFAULT_UPLOAD["archive_extensions"]),
    }


class UploadConfig:
    """Upload limits and storage configuration.

    无参 → 控制面 mining.yaml 的 upload 段；显式 kwargs → 直接构造（测试）。
    """

    def __init__(self, **fields: Any) -> None:
        if not fields:
            fields = _upload_from_control_plane()
        else:
            fields = {
                "upload_root": fields.get("upload_root", _DEFAULT_UPLOAD["root"]),
                "upload_max_file_size": int(fields.get("upload_max_file_size", _DEFAULT_UPLOAD["max_file_size"])),
                "upload_max_archive_size": int(fields.get("upload_max_archive_size", _DEFAULT_UPLOAD["max_archive_size"])),
                "upload_max_files_per_request": int(fields.get("upload_max_files_per_request", _DEFAULT_UPLOAD["max_files_per_request"])),
                "upload_disk_reserve_bytes": int(fields.get("upload_disk_reserve_bytes", _DEFAULT_UPLOAD["disk_reserve_bytes"])),
                "upload_archive_extensions": fields.get("upload_archive_extensions", _DEFAULT_UPLOAD["archive_extensions"]),
            }
        self.__dict__.update(fields)

    @computed_field
    @property
    def archive_exts_set(self) -> frozenset[str]:
        """Extensions that will be auto-extracted after upload (ZIP only — pure Python)."""
        return frozenset(
            e.strip().lower() for e in self.upload_archive_extensions.split(",") if e.strip()
        )

    @computed_field
    @property
    def max_file_size_mb(self) -> int:
        return self.upload_max_file_size // (1024 * 1024)

    @computed_field
    @property
    def max_archive_size_mb(self) -> int:
        return self.upload_max_archive_size // (1024 * 1024)

    @computed_field
    @property
    def upload_root_path(self) -> Path:
        return Path(self.upload_root).resolve()
