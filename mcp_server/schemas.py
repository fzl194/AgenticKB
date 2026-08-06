"""Pydantic models for MCP tool inputs and outputs."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints


# --- health_check output ---

class HealthResult(BaseModel):
    available: bool
    status: str = ""
    version: str = ""
    latency_ms: float = 0.0
    error: str = ""


# --- search_knowledge input ---

class EntityRef(BaseModel):
    type: str = ""
    name: str
    normalized_name: str = ""


DomainId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class SearchInput(BaseModel):
    query: str
    domain: DomainId
    #: Name (as listed in ``_retrieval.available_paradigms``) or id of a specific retrieval
    #: paradigm. Omitted = the domain's default, i.e. exactly the pre-existing behaviour.
    paradigm: str | None = None
    scope: dict | None = None
    entities: list[EntityRef] | None = None
    debug: bool = False


# --- get_segment_fulltext input ---

class SegmentRef(BaseModel):
    """One thing to expand, taken straight off a search result item.

    ``type`` is the item's ``kind`` (``retrieval_unit`` for seeds, ``raw_segment`` for the context
    and support items around them) and ``id`` is its ``id``. Required rather than inferred: ids
    carry no reliable prefix, and guessing wrong searches the wrong table and reports a miss.
    """

    type: str
    id: str


class FullTextInput(BaseModel):
    domain: DomainId
    refs: list[SegmentRef]
    #: The paradigm whose corpus produced these ids, copied verbatim from the search result's
    #: ``_retrieval.paradigm_id``. Id only, never a name: this is a machine hand-back, and every
    #: additional accepted form is another way for it to fail to resolve. Omitted = look up the
    #: domain's default paradigm, which is right only when the search did the same.
    paradigm_id: str | None = None
    #: ``window`` 额外带回前后相邻片段。切分边界常把一句话劈成两段，只看命中段读起来是断的。
    granularity: Literal["segment", "window"] = "segment"
    window_radius: int = Field(default=1, ge=1, le=5)
