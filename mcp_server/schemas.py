"""Pydantic models for MCP tool inputs and outputs."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints


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
