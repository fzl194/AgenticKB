"""Shadow Parse layer (M2 → M4, SRS §2.2 / §C08 / §9.2).

影子解析写入链路：Parse IR 制品落对象存储 parse bucket + ``asset_parse_runs``
PG 投影。M2 阶段与现有发布链路硬隔离；M4 起承载完整 Parse Run 状态机
（含 SUPERSEDED），转正由 ``snapshot_store`` 层
负责（该层仍不写 raw_segments / mining_run_documents）。
"""
from knowledge_mining.mining.shadow_parse.contracts import (
    PARSE_RUN_STATUSES,
    ParseRunRecord,
    ParseRunRepository,
    SHADOW_PARSE_STATUSES,
    ShadowParseResult,
)
from knowledge_mining.mining.shadow_parse.repositories_memory import MemoryParseRunRepository
from knowledge_mining.mining.shadow_parse.service import ShadowParseService

__all__ = [
    "MemoryParseRunRepository",
    "PARSE_RUN_STATUSES",
    "ParseRunRecord",
    "ParseRunRepository",
    "SHADOW_PARSE_STATUSES",
    "ShadowParseResult",
    "ShadowParseService",
]
