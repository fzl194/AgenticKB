from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateWorkflowRequest(WorkflowRequest):
    name: str = Field(min_length=1, max_length=120)
    schema_version: str = "1.0"
    description: str | None = None
    template_key: Literal[
        "minimal",
        "fast_retrieval",
        "discourse_only",
        "entity_graph",
        "hybrid_knowledge",
        "ontology_only",
        "full",
    ] = "full"
    graph: dict[str, Any] | None = None
    created_by: str | None = None


class SaveDraftRequest(WorkflowRequest):
    graph: dict[str, Any]
    expected_revision: int = Field(ge=0)
    updated_by: str | None = None


class ValidateWorkflowRequest(WorkflowRequest):
    graph: dict[str, Any] | None = None


class PublishWorkflowRequest(WorkflowRequest):
    expected_revision: int = Field(ge=0)
    release_notes: str | None = None
    created_by: str | None = None


class RestoreDraftRequest(WorkflowRequest):
    expected_revision: int = Field(ge=0)
    updated_by: str | None = None


class CloneWorkflowRequest(WorkflowRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    source_version: int | None = Field(default=None, ge=1)
    created_by: str | None = None


class ArchiveWorkflowRequest(WorkflowRequest):
    updated_by: str | None = None
