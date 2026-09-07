"""A1 来源记录面（37/38 号）：IR 位置实值 → 表示级 locator 的物化/重放."""
from knowledge_mining.mining.source_locator.extract import (
    ALIAS_REPRESENTATION_TYPES,
    LOCATOR_VERSION,
    LocatorRecord,
    PRECISE_LOCATOR_KINDS,
    extract_locator_records,
    source_format_of,
)

__all__ = [
    "ALIAS_REPRESENTATION_TYPES",
    "LOCATOR_VERSION",
    "LocatorRecord",
    "PRECISE_LOCATOR_KINDS",
    "extract_locator_records",
    "source_format_of",
]
