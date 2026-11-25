"""asset_figure_caption_cache 的 PG 仓储（方案B）。

只缓存成功图注；get/put 均单连接单语句（对齐 PgSegmentStore 风格）。
键成分拆列落库：cache_key 是主键，成分列供审计/统计（键推导的单一事实
来源仍是 figure_caption/keys.py，仓储不做二次推导）。
"""
from __future__ import annotations

from typing import Any

from knowledge_mining.mining.figure_caption.service import FigureCaptionCache


class PgFigureCaptionCache:
    """``FigureCaptionCache`` 的 PG 实现。"""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def get(self, key: str) -> str | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT caption FROM asset_figure_caption_cache WHERE cache_key = %s",
                [key],
            )
            row = await cur.fetchone()
        value = row.get("caption") if row else None
        return value if isinstance(value, str) and value else None

    async def put(
        self,
        key: str,
        caption: str,
        *,
        model: str,
        domain: str = "",
        source_raw_hash: str = "",
        parser_fingerprint: str = "",
        figure_element_id: str = "",
        prompt_version: str = "",
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """INSERT INTO asset_figure_caption_cache (
                       cache_key, domain, source_raw_hash, parser_fingerprint,
                       figure_element_id, model, prompt_version, caption
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (cache_key) DO UPDATE SET caption = EXCLUDED.caption""",
                [
                    key, domain, source_raw_hash, parser_fingerprint,
                    figure_element_id, model, prompt_version, caption,
                ],
            )


__all__ = ["PgFigureCaptionCache"]
