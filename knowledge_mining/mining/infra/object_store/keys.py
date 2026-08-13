"""Pure key-layout helpers for the Object Store (M1.1, WP1A).

SRS §8.1 specifies the content-addressed object key layout
``{prefix}/{ab}/{cd}/{sha256}`` where ``ab`` / ``cd`` are the first two / next
two hex chars of the SHA-256. This spreads objects across a shallow directory
tree so listings and sharded scans stay balanced.

These functions are pure and dependency-free so they can be unit-tested in
isolation.
"""
from __future__ import annotations

import re

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def build_object_key(
    artifact_class: str,
    content_sha256: str,
    prefix: str = "v1",
) -> str:
    """Build an immutable content-addressed object key (SRS §8.1).

    Layout: ``{prefix}/{ab}/{cd}/{sha256}`` where ``ab`` = sha256[:2] and
    ``cd`` = sha256[2:4]. The key carries no business semantics (SRS §3.1A).

    Raises ``ValueError`` if ``content_sha256`` is not a 64-char hex string.
    """
    if not _SHA256_HEX.match(content_sha256):
        raise ValueError(
            f"content_sha256 must be a 64-char lowercase hex sha256, got: {content_sha256!r}"
        )
    ab = content_sha256[:2]
    cd = content_sha256[2:4]
    head = prefix.strip("/")
    return f"{head}/{ab}/{cd}/{content_sha256}"


__all__ = ["build_object_key"]
