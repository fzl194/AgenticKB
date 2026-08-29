"""批次8 M6（24 号 §8）：4 套官方挖掘预置定义.

固定 system id、幂等 seeding、用户可归档不复活；hybrid_assets 为建库默认。
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_WORKFLOW_ID = "system-hybrid-assets"


@dataclass(frozen=True)
class MiningPreset:
    workflow_id: str
    name: str
    description: str
    template_key: str
    is_system_default: bool


MINING_PRESETS: tuple[MiningPreset, ...] = (
    MiningPreset(
        workflow_id="system-lexical-assets",
        name="轻量关键词资产",
        description="仅解析/切片/搜索投影（无向量）：低成本、无 embedding 服务的 lexical-only 场景。",
        template_key="lexical_assets",
        is_system_default=False,
    ),
    MiningPreset(
        workflow_id="system-hybrid-assets",
        name="标准混合资产",
        description="零 LLM 默认线：结构保真切片 + 类型化搜索投影 + 策略化向量。通用知识库默认。",
        template_key="hybrid_assets",
        is_system_default=True,
    ),
    MiningPreset(
        workflow_id="system-query-alias-assets",
        name="问题别名增强资产",
        description="标准混合链 + 离线问题别名生成（实验，LLM 不可用自动降级）。",
        template_key="query_alias_assets",
        is_system_default=False,
    ),
    MiningPreset(
        workflow_id="system-longdoc-assets",
        name="长文档全局增强资产",
        description="标准混合链 + 标题树层级摘要（实验，LLM 不可用自动降级）。",
        template_key="longdoc_assets",
        is_system_default=False,
    ),
)


__all__ = ["DEFAULT_WORKFLOW_ID", "MINING_PRESETS", "MiningPreset"]
