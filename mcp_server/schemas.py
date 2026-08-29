"""Pydantic models for MCP tool inputs and outputs."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints


# --- health_check output ---

class HealthResult(BaseModel):
    available: bool
    status: str = ""
    version: str = ""
    latency_ms: float = 0.0
    error: str = ""


# --- search_knowledge input (25 号 §7.1，批次8 R8) ---

DomainId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

ExpansionMode = Annotated[
    str,
    StringConstraints(strip_whitespace=True),
]


class SearchInput(BaseModel):
    """search_knowledge 入参（§7.1）。

    Agent 明确传入的 constraints 是 hard filters（FTS/dense 之前下推）；服务端不从
    query 自动推断任何约束。未传 = 宽检索，Agent 从结果与 inspect 反馈后再收窄。
    """

    query: str
    domain: DomainId
    #: Name (as listed in error hints) or id of a specific retrieval paradigm.
    #: Omitted = the KB binding / official default, i.e. exactly the pre-existing behaviour.
    paradigm: str | None = None
    #: §7.1 within：document_refs/section_refs/structure_ref/include_descendants 等
    within: dict | None = None
    #: §7.1 filters：relative_path_prefix/asset_types/evidence_types/date_range 等
    filters: dict | None = None
    #: §7.1 expansion：{"mode": auto|exact|window|parent|whole_document}
    expansion: dict | None = None
    #: §7.1 top_k：结果面上限（服务端按各阶段上限 clamp）
    top_k: int | None = Field(default=None, ge=1, le=200)
    debug: bool = False
