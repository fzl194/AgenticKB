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


_OBJECT_ROOT: str | None = None


def _shared_object_root() -> str:
    """进程级共享 Fake 对象根目录，atexit 清理（避免每用例泄漏临时目录）."""
    global _OBJECT_ROOT
    if _OBJECT_ROOT is None:
        import atexit
        import shutil
        import tempfile

        root = tempfile.mkdtemp(prefix="kb-test-objstore-")
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        _OBJECT_ROOT = root
    return _OBJECT_ROOT


def attach_object_store(app, root: str | None = None) -> None:
    """测试 app 挂上 kb/deps.py 需要的对象存储 state（Fake 文件实现）.

    生产由 api/app.py lifespan 装配 MinIO；测试 app 没有 lifespan，统一
    用同一 Port 的 Fake 实现注入，保持「无静默落本地盘」的生产语义。
    """
    from knowledge_mining.mining.infra.object_store.config import (
        ObjectStoreConfig,
    )
    from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore

    root = root or _shared_object_root()
    config = ObjectStoreConfig(
        provider="fake", bucket_prefix="kb-test-", root_path=root,
    )
    app.state.object_store = FakeObjectStore(root)
    app.state.object_store_config = config
