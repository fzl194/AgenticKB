"""Configuration for the Object Store adapters (M1.1, WP1A).

Frozen dataclass mirroring the ``system/storage.yaml`` shape. Credentials are
explicitly excluded from ``__repr__`` so logs / error traces never leak the
MinIO secret key (SRS §C00, ADR-0003 D-006).

References:
- SRS §8.1 (bucket_prefix + artifact class naming)
- ADR-0003 D-002 (dual adapter), D-006 (guarded MinIO)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from knowledge_mining.mining.contracts.storage.enums import VALID_PROVIDERS

_VALID_BUCKET_NAME = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")


@dataclass(frozen=True)
class ObjectStoreConfig:
    """Resolved configuration for ``make_object_store``.

    ``provider`` selects the adapter (``"fake"`` | ``"minio"``).
    ``bucket_prefix`` is concatenated with the artifact class to form bucket
    names per SRS §8.1 (e.g. ``agentickb-dev-source``).

    Fake-only fields: ``root_path`` (filesystem root for objects + sidecars).
    MinIO-only fields: ``endpoint`` / ``access_key`` / ``secret_key`` /
    ``secure`` / ``region``.
    """

    provider: str = "fake"
    bucket_prefix: str = "agentickb-dev-"
    # fake
    root_path: str = "./.object_store"
    # minio
    endpoint: str = "localhost:9000"
    access_key: str = ""
    secret_key: str = ""
    secure: bool = False
    region: str | None = None
    # Free-form pass-through metadata (never credentials).
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.provider not in VALID_PROVIDERS:
            raise ValueError(
                f"unknown object store provider: {self.provider!r} "
                f"(valid: {sorted(VALID_PROVIDERS)})"
            )
        if not _VALID_BUCKET_NAME.match(self.bucket_prefix.rstrip("-") + "x"):
            # The prefix plus a trailing artifact class must form a valid S3
            # bucket name; we validate the prefix stem loosely here.
            if not self.bucket_prefix:
                raise ValueError("bucket_prefix must not be empty")

    def __repr__(self) -> str:  # noqa: D401 - exclude secrets
        # Never include access_key / secret_key in repr (logs, tracebacks).
        return (
            f"ObjectStoreConfig(provider={self.provider!r}, "
            f"bucket_prefix={self.bucket_prefix!r}, root_path={self.root_path!r}, "
            f"endpoint={self.endpoint!r}, secure={self.secure!r}, "
            f"region={self.region!r}, "
            f"access_key={'***set***' if self.access_key else '<empty>'}, "
            f"secret_key={'***set***' if self.secret_key else '<empty>'})"
        )

    # -- factories ---------------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ObjectStoreConfig:
        """Build a config from a parsed dict (e.g. one ``storage.yaml``)."""
        known = {
            "provider",
            "bucket_prefix",
            "root_path",
            "endpoint",
            "access_key",
            "secret_key",
            "secure",
            "region",
            "extra",
        }
        kwargs: dict[str, Any] = {}
        passthrough: dict[str, Any] = {}
        for key, value in d.items():
            if key in known:
                kwargs[key] = value
            else:
                passthrough[key] = value
        if passthrough and "extra" not in kwargs:
            kwargs["extra"] = passthrough
        elif passthrough:
            merged = dict(kwargs["extra"])
            merged.update(passthrough)
            kwargs["extra"] = merged
        return cls(**kwargs)

    @classmethod
    def from_yaml(cls, path: str | Path) -> ObjectStoreConfig:
        """Load a config from a YAML file.

        The file may have a top-level ``object_store:`` mapping or be flat.
        """
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        if isinstance(data, dict) and "object_store" in data and isinstance(data["object_store"], dict):
            data = data["object_store"]
        if not isinstance(data, dict):
            raise ValueError(f"invalid storage config at {path}: expected a mapping")
        return cls.from_dict(data)
