"""Run Mining API: python -m knowledge_mining.mining.api"""
import asyncio
import copy
import logging
import os
import sys

# Windows: psycopg async requires SelectorEventLoop, not ProactorEventLoop.
# uvicorn hardcodes ProactorEventLoop on Windows, so we monkey-patch its factory.
if sys.platform == "win32":
    import uvicorn.loops.asyncio as _uv_loop  # noqa: E402

    _uv_loop.asyncio_loop_factory = lambda use_subprocess=False: asyncio.SelectorEventLoop

import uvicorn  # noqa: E402
from uvicorn.config import LOGGING_CONFIG  # noqa: E402

from knowledge_mining.mining.api.app import app  # noqa: E402

_DATEFMT = "%Y-%m-%d %H:%M:%S"
_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()


def _timestamped_log_config() -> dict:
    """Application + uvicorn logging with timestamps (see llm_service for rationale)."""
    logging.basicConfig(
        level=_LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt=_DATEFMT,
    )
    cfg = copy.deepcopy(LOGGING_CONFIG)
    cfg["formatters"]["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    cfg["formatters"]["default"]["datefmt"] = _DATEFMT
    cfg["formatters"]["access"]["fmt"] = (
        '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    )
    cfg["formatters"]["access"]["datefmt"] = _DATEFMT
    for _logger in cfg.get("loggers", {}).values():
        _logger["level"] = _LOG_LEVEL
    return cfg


if __name__ == "__main__":
    # 从主控制服务拉挖掘服务配置（含 port），仿 llm_service 启动范式。
    from knowledge_mining.mining.infra.control_plane import fetch_mining_service_config
    from knowledge_mining.mining.infra.mining_config import MiningConfig

    fetch_mining_service_config()
    port = MiningConfig().port
    uvicorn.run(app, host="0.0.0.0", port=port, log_config=_timestamped_log_config())
