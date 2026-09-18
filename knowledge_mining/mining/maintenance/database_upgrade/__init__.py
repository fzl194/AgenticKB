"""Versioned database convergence and cutover support."""

from .contract import (
    CURRENT_SCHEMA_VERSION,
    EXPECTED_FORMAL_TABLES,
    EXPECTED_PHYSICAL_TABLES,
    RETIRED_TABLES,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "EXPECTED_FORMAL_TABLES",
    "EXPECTED_PHYSICAL_TABLES",
    "RETIRED_TABLES",
]
