from __future__ import annotations

from typing import Any

from knowledge_mining.mining.infra.control_plane import get_mining_service_config

_DEFAULTS = {
    "max_sheets": 200,
    "max_nonempty_cells": 1_000_000,
    "table_chunk_target_tokens": 420,
}


class ExcelConfig:
    def __init__(self, **fields: Any) -> None:
        raw = fields or (get_mining_service_config().get("excel") or {})
        self.max_sheets = int(raw.get("max_sheets", _DEFAULTS["max_sheets"]))
        self.max_nonempty_cells = int(
            raw.get("max_nonempty_cells", _DEFAULTS["max_nonempty_cells"])
        )
        self.table_chunk_target_tokens = int(
            raw.get(
                "table_chunk_target_tokens",
                _DEFAULTS["table_chunk_target_tokens"],
            )
        )
        if min(
            self.max_sheets,
            self.max_nonempty_cells,
            self.table_chunk_target_tokens,
        ) <= 0:
            raise ValueError("Excel limits must be positive integers")
