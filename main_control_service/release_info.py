"""Deployment release manifest loading and validation."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any


_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def default_release_manifest_path() -> Path:
    """Return the repository/image-level release manifest path."""
    return Path(__file__).resolve().parents[1] / "releases.json"


def _required_text(record: dict[str, Any], key: str, *, context: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def _normalize_release(raw: Any, *, index: int) -> dict[str, Any]:
    context = f"releases[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{context} must be an object")

    version = _required_text(raw, "version", context=context)
    if not _VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"{context}.version must use MAJOR.MINOR.PATCH")

    released_at = _required_text(raw, "released_at", context=context)
    try:
        date.fromisoformat(released_at)
    except ValueError as exc:
        raise ValueError(f"{context}.released_at must use YYYY-MM-DD") from exc

    title = _required_text(raw, "title", context=context)
    changes = raw.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError(f"{context}.changes must be a non-empty list")
    normalized_changes = []
    for change_index, change in enumerate(changes):
        if not isinstance(change, str) or not change.strip():
            raise ValueError(
                f"{context}.changes[{change_index}] must be a non-empty string"
            )
        normalized_changes.append(change.strip())

    return {
        "version": version,
        "released_at": released_at,
        "title": title,
        "changes": normalized_changes,
    }


def load_current_release(path: Path | None = None) -> dict[str, Any]:
    """Load the current release once; invalid deployment metadata fails fast."""
    manifest_path = path or default_release_manifest_path()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to load release manifest: {manifest_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("release manifest must be an object")

    current = _required_text(payload, "current", context="manifest")
    releases = payload.get("releases")
    if not isinstance(releases, list) or not releases:
        raise ValueError("manifest.releases must be a non-empty list")

    by_version: dict[str, dict[str, Any]] = {}
    for index, raw_release in enumerate(releases):
        release = _normalize_release(raw_release, index=index)
        version = release["version"]
        if version in by_version:
            raise ValueError(f"duplicate release version: {version}")
        by_version[version] = release

    if current not in by_version:
        raise ValueError(f"current release {current} has no release record")
    return by_version[current]
