"""研究算子隔离边界（批次8 M0，24 号文档 §5.10-§5.16）。

实体/本体/图谱生产链在当前版本不稳定且需人工审核，产品决策为：
代码保留用于研究，但从正式 catalog、API、UI、seed 和新范式编译可用集合
中全部移除——本模块是唯一的集中声明处。

约束（违反即 bug）：
- `builtin_catalog()` / `OPTIONS_BY_OPERATOR` / 正式 handler registry 永不包含这些类型；
- normalizer/compiler 不再为其自动注入节点或 requires；
- `asset_persist` / `mining_finalize` 不默认消费其产物，readiness 不声明 graph 能力；
- 未来启用必须重新完成「生产—持久化—检索消费—评测」全闭环设计，
  不允许仅把开关打开就恢复。
"""
from __future__ import annotations

RESEARCH_OPERATOR_TYPES = frozenset({
    "entity_extract",
    "entity_resolve",
    "entity_relation_extract",
    "entity_review_gate",
    "ontology_induction",
    "ontology_review_gate",
    "graph_write",
})
