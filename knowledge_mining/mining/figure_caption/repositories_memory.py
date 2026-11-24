"""图注缓存的内存实现（测试/开发；生产走 repositories_pg）。"""
from __future__ import annotations


class MemoryFigureCaptionCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def put(self, key: str, caption: str, **_: object) -> None:
        self.store[key] = caption


__all__ = ["MemoryFigureCaptionCache"]
