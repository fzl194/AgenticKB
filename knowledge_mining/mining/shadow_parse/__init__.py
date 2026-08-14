"""Shadow Parse layer (M2, SRS §2.2 / §C08).

影子解析写入链路：Parse IR 制品落对象存储 parse bucket + ``asset_parse_runs``
PG 投影。与现有发布链路硬隔离（不写 snapshots / raw_segments /
mining_run_documents，M2 退出条件）。
"""
from knowledge_mining.mining.shadow_parse.contracts import (
    ParseRunRecord,
    ParseRunRepository,
    SHADOW_PARSE_STATUSES,
    ShadowParseResult,
)
from knowledge_mining.mining.shadow_parse.repositories_memory import (
    MemoryParseRunRepository,
)
from knowledge_mining.mining.shadow_parse.service import ShadowParseService

__all__ = [
    "MemoryParseRunRepository",
    "ParseRunRecord",
    "ParseRunRepository",
    "SHADOW_PARSE_STATUSES",
    "ShadowParseResult",
    "ShadowParseService",
]
