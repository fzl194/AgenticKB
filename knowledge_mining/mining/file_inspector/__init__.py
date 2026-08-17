"""File Inspector + Parser Router（M3 WP5/WP6, SRS §C03 / §C05 初版）.

- :class:`FileInspector` 从原始 bytes 探测文档画像（格式/容器数/加密/
  文本层），为 Router 与 Operator 提供确定性路由依据（SRS §4.2）。
- :class:`ParserRouter` 基于 :class:`DocumentProfile` + Backend Registry
  产出 :class:`RouteDecision`（primary/fallback/reason codes）。
"""
from knowledge_mining.mining.file_inspector.inspect import (
    INSPECTOR_VERSION,
    DocumentProfile,
    FileInspector,
)
from knowledge_mining.mining.file_inspector.router import (
    ParserRouter,
    RouteDecision,
)

__all__ = [
    "INSPECTOR_VERSION",
    "DocumentProfile",
    "FileInspector",
    "ParserRouter",
    "RouteDecision",
]
