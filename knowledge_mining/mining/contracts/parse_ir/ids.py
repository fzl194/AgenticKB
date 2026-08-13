"""Deterministic, scope-stable element identifiers (SRS §2.1, §4.7).

SRS §2.1 invariant: "一个 Snapshot 的 Parse IR 内 element id 不因数据库分页
或切片变化而变化". `stable_element_id` produces ids that depend only on
(scope, order_index, salt) — never on the current page/batch offset — so the
same logical element yields the same id on every re-parse of the same content.

Algorithm (v0.1):
  f"{scope}-e-{order_index:05d}"          when salt == ""
  sha1(f"{scope}|{order_index}|{salt}")[:16]
                                          otherwise (content-hashed short id)

The order-only form is preferred for normalizers that emit elements in a
stable reading order; the salted form is for cases where the normalizer must
fold content into the id to disambiguate siblings that share order_index
across re-runs.
"""
from __future__ import annotations

import hashlib


def stable_element_id(scope: str, order_index: int, salt: str = "") -> str:
    """Return a deterministic element id stable within a snapshot scope.

    Args:
        scope: Stable scope key for the snapshot/parse run (e.g. snapshot id,
            or a document+run composite key). Must be non-empty.
        order_index: Zero-based reading-order position of the element within
            the scope. Negative values are rejected.
        salt: Optional content salt. When empty, the id is purely positional;
            when provided, the id folds content in so siblings with the same
            order_index but different text still differ.

    Returns:
        A string id. Stable across re-parses of the same (scope, order_index,
        salt) triple.

    Raises:
        ValueError: If ``scope`` is empty or ``order_index`` is negative.
    """
    if not scope:
        raise ValueError("stable_element_id requires a non-empty scope")
    if order_index < 0:
        raise ValueError(f"order_index must be >= 0, got {order_index}")

    if not salt:
        return f"{scope}-e-{order_index:05d}"

    digest = hashlib.sha1(f"{scope}|{order_index}|{salt}".encode("utf-8")).hexdigest()
    return f"{scope}-e-{digest[:16]}"
