from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_mining.mining.workflow.templates import builtin_templates

#: 29号复审 R01：模板键唯一真相源 = templates.builtin_templates()——
#: API DTO / UI 类型 / service create 共用；旧七套键给显式退役错误。
_RETIRED_TEMPLATE_KEYS = frozenset({
    "minimal", "fast_retrieval", "discourse_only", "entity_graph",
    "hybrid_knowledge", "ontology_only", "full",
})


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateWorkflowRequest(WorkflowRequest):
    name: str = Field(min_length=1, max_length=120)
    schema_version: str = "1.0"
    description: str | None = None
    template_key: str = "hybrid_assets"
    graph: dict[str, Any] | None = None
    created_by: str | None = None

    @field_validator("template_key")
    @classmethod
    def _template_key_must_be_current(cls, value: str) -> str:
        if value in _RETIRED_TEMPLATE_KEYS:
            raise ValueError(f"template_retired:{value}")
        if value not in builtin_templates():
            raise ValueError(f"unknown_template:{value}")
        return value


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
