"""KB tests — async pool fixture (mining API is async)."""
from __future__ import annotations

import asyncio
import sys

# psycopg async 在 Windows 默认 ProactorEventLoop 上无法连接（CLAUDE.md 已记：
# mining/llm_service 入口模块同样切到 SelectorEventLoop）。pytest-asyncio 默认用
# ProactorEventLoop，这里在收集期切策略，让 AsyncConnectionPool 能正常工作。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest_asyncio
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


@pytest_asyncio.fixture
async def async_pool(db_config, _ensure_schema):
    """Async pool bound to the test DB (reuses conftest db_config + _ensure_schema)."""
    pool = AsyncConnectionPool(
        db_config.conninfo,
        min_size=1,
        max_size=2,
        open=False,
        kwargs={"row_factory": dict_row},
    )
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()
