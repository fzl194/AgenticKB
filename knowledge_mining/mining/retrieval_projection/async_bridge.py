"""同步门面用的事件循环桥（批次8 M4）：复用 new_chain_services._run_sync."""
from __future__ import annotations

from typing import Any


def run_sync(coro: Any) -> Any:
    from knowledge_mining.mining.workflow.new_chain_services import _run_sync

    return _run_sync(coro)


__all__ = ["run_sync"]
