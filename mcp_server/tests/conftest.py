"""Shared fixtures.

The paradigm catalog is cached in a module-level dict with a 30s TTL, so without resetting it
between tests one test's fetch answers another test's question — and the suite starts depending on
execution order. That is exactly how the first run of these tests produced ``available_paradigms:
[]`` for a case whose fake transport would have refused the catalog request outright.
"""

from __future__ import annotations

import pytest

from mcp_server import client as mcp_client


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    mcp_client._catalog_cache["entries"] = None
    mcp_client._catalog_cache["fetched_at"] = 0.0
    yield
    mcp_client._catalog_cache["entries"] = None
    mcp_client._catalog_cache["fetched_at"] = 0.0
