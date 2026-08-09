"""Run-scoped working directories for mining artifacts (extracted images, etc.)."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

_SAFE_KEY_RE = re.compile(r"[^\w.\-]+", re.UNICODE)


def mining_run_root(run_id: str) -> Path:
    """Root directory for one mining run's ephemeral artifacts."""
    root = Path(tempfile.gettempdir()) / "mining_runs" / run_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_run_image_dir(run_id: str, document_key: str) -> Path:
    """Per-document image dump dir: ``{tmp}/mining_runs/{run_id}/images/{doc}/``."""
    safe = _SAFE_KEY_RE.sub("_", document_key).strip("._") or "doc"
    safe = safe[:120]
    path = mining_run_root(run_id) / "images" / safe
    path.mkdir(parents=True, exist_ok=True)
    return path
