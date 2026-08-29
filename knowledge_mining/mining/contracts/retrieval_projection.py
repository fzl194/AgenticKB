"""统一 RetrievalRepresentation 契约（批次8 M2，24 号 §5.4）.

检索表示是「可搜索代理」，不是原始内容真相：
- 原始证据在 ParseIR/raw segments（源证据面）；
- 表示命中后由检索侧 evidence_hydrate 按 target 回源（25 号 §6.8）；
- 多种表示可指向同一证据（canonical_evidence_id 聚合键，融合阶段按
  canonical 合并不能重复占位）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# 类型矩阵（24 号 §5.4）：heading/navigation 默认不单独形成正文表示，
# 只作为结构节点与 facet；query_alias/summary_alias 由 M3 增强算子产出。
REPRESENTATION_TYPES = (
    "prose",
    "section",
    "document",
    "table",
    "table_row",
    "list_group",
    "code_block",
    "formula",
    "figure_caption",
    "query_alias",
    "summary_alias",
)

PROJECTOR_NAME = "retrieval_unit_project"
PROJECTOR_VERSION = "1"


@dataclass(frozen=True)
class RetrieRepresentation:
    """一条可搜索表示（§5.4 契约字段全集）.

    - ``representation_type``：本模块固定枚举（矩阵行）；
    - ``content_type``：源内容类型（paragraph/table/heading…），不与
      representation type 混用；
    - ``content_text``：可检索文本；不得成为无来源的新事实；
    - ``structural_context``：标题面包屑/表头/caption 等确定性上下文；
    - ``target_type/target_ref``：hydrate 解析的 canonical target；
    - ``canonical_evidence_id``：同源 raw/alias/多视图聚合键；
    - ``lexical/dense_eligible``：分别声明是否进 FTS / 向量；
    - ``returnable``：能否直接作证据返回（alias 型恒 False）。
    """

    representation_id: str
    representation_type: str
    content_type: str
    content_text: str
    target_type: str
    target_ref: str
    canonical_evidence_id: str
    structural_context: str = ""
    source_refs: tuple[Mapping[str, Any], ...] = ()
    parent_ref: str | None = None
    container_ref: str | None = None
    context_group_id: str | None = None
    ordinal: int = 0
    lexical_eligible: bool = True
    dense_eligible: bool = True
    returnable: bool = True
    facets: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(
        default_factory=lambda: {
            "projector": PROJECTOR_NAME,
            "projector_version": PROJECTOR_VERSION,
        }
    )


__all__ = [
    "PROJECTOR_NAME",
    "PROJECTOR_VERSION",
    "REPRESENTATION_TYPES",
    "RetrieRepresentation",
]
