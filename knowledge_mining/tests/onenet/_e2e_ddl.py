# -*- coding: utf-8 -*-
"""真库 E2E 共享 DDL 铺设（跨测试文件会话级一次——个别 trigger 非幂等）."""
from __future__ import annotations

import os

_DDL_APPLIED = False


async def ensure_ddl(dsn: str | None = None) -> None:
    """把生产 DDL 链原样铺进测试库（进程级一次；重铺会 trigger DuplicateObject）."""
    global _DDL_APPLIED
    if _DDL_APPLIED:
        return
    from knowledge_mining.mining.infra.pg_schema import domain_schema_paths
    import psycopg

    async with await psycopg.AsyncConnection.connect(
            dsn or os.environ["ONENET_E2E_PG_DSN"], autocommit=True) as conn:
        for p in domain_schema_paths():
            await conn.execute(p.read_text(encoding="utf-8"))
    _DDL_APPLIED = True
