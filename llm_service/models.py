from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from pydantic import field_validator
from pydantic import model_validator


# --- Request models ---


class TaskSubmitRequest(BaseModel):
    caller_service: str | None = Field(default=None, min_length=1, max_length=64)
    knowledge_domain: str | None = Field(default=None, min_length=1, max_length=128)
    caller_domain: str | None = Field(default=None, min_length=1, max_length=64)
    pipeline_stage: str = Field(..., pattern=r"^[a-z][a-z0-9_]{1,63}$")
    template_key: str | None = None
    input: dict[str, Any] | None = None
    messages: list[dict[str, Any]] | None = None
    params: dict[str, Any] | None = None
    # Optional override: key under provider.models (e.g. "glm-4.5v") or API model id.
    # Used for vision / alternate chat models without changing active_model.
    model: str | None = Field(default=None, min_length=1, max_length=128)
    expected_output_type: str | None = Field(
        default=None, pattern=r"^(json_object|json_array|text)$"
    )
    output_schema: dict[str, Any] | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] | None = None
    max_attempts: int = Field(default=3, ge=1, le=10)
    priority: int = Field(default=100, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_caller_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if not value.get("caller_service") and value.get("caller_domain"):
            value = dict(value)
            value["caller_service"] = value["caller_domain"]
        return value

    @model_validator(mode="after")
    def _validate_caller_service_present(self) -> "TaskSubmitRequest":
        if not self.caller_service:
            raise ValueError("caller_service is required")
        return self


class EmbeddingTaskRequest(BaseModel):
    input: list[str] | str
    model: str | None = None
    dimensions: int | None = Field(default=None, ge=1)
    caller_service: str | None = Field(default="model", min_length=1, max_length=64)
    knowledge_domain: str | None = Field(default=None, min_length=1, max_length=128)
    caller_domain: str | None = Field(default=None, min_length=1, max_length=64)
    pipeline_stage: str = Field(default="embedding", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    idempotency_key: str | None = None
    max_attempts: int = Field(default=2, ge=1, le=5)
    priority: int = Field(default=100, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_caller_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if not value.get("caller_service") and value.get("caller_domain"):
            value = dict(value)
            value["caller_service"] = value["caller_domain"]
        return value

    @field_validator("input", mode="before")
    @classmethod
    def _normalize_input(cls, value: list[str] | str) -> list[str]:
        if isinstance(value, str):
            return [value]
        return value


class RerankTaskRequest(BaseModel):
    query: str = Field(..., min_length=1)
    documents: list[str]
    model: str | None = None
    top_n: int | None = Field(default=None, ge=1)
    caller_service: str | None = Field(default="model", min_length=1, max_length=64)
    knowledge_domain: str | None = Field(default=None, min_length=1, max_length=128)
    caller_domain: str | None = Field(default=None, min_length=1, max_length=64)
    pipeline_stage: str = Field(default="rerank", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    idempotency_key: str | None = None
    max_attempts: int = Field(default=2, ge=1, le=5)
    priority: int = Field(default=100, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_caller_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if not value.get("caller_service") and value.get("caller_domain"):
            value = dict(value)
            value["caller_service"] = value["caller_domain"]
        return value

    @field_validator("documents")
    @classmethod
    def _validate_documents(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("documents must not be empty")
        return value


class EmbeddingRequest(BaseModel):
    input: list[str] | str = Field(..., max_length=100)
    model: str | None = None
    dimensions: int | None = Field(default=None, ge=1)
    caller_service: str | None = Field(default="model", min_length=1, max_length=64)
    knowledge_domain: str | None = Field(default=None, min_length=1, max_length=128)
    caller_domain: str | None = Field(default=None, min_length=1, max_length=64)
    pipeline_stage: str = Field(default="embedding", pattern=r"^[a-z][a-z0-9_]{1,63}$")

    @model_validator(mode="before")
    @classmethod
    def _normalize_caller_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if not value.get("caller_service") and value.get("caller_domain"):
            value = dict(value)
            value["caller_service"] = value["caller_domain"]
        return value

    @field_validator("input", mode="before")
    @classmethod
    def _normalize_input(cls, value: list[str] | str) -> list[str]:
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("input")
    @classmethod
    def _validate_input(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("input must not be empty")
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("input items must be non-empty strings")
        return value


class EmbeddingData(BaseModel):
    index: int
    embedding: list[float]


class EmbeddingResponse(BaseModel):
    model: str
    data: list[EmbeddingData]
    usage: dict[str, Any] | None = None


class RerankRequest(BaseModel):
    query: str = Field(..., min_length=1)
    documents: list[str] = Field(..., max_length=200)
    model: str | None = None
    top_n: int | None = Field(default=None, ge=1)
    caller_service: str | None = Field(default="model", min_length=1, max_length=64)
    knowledge_domain: str | None = Field(default=None, min_length=1, max_length=128)
    caller_domain: str | None = Field(default=None, min_length=1, max_length=64)
    pipeline_stage: str = Field(default="rerank", pattern=r"^[a-z][a-z0-9_]{1,63}$")

    @model_validator(mode="before")
    @classmethod
    def _normalize_caller_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if not value.get("caller_service") and value.get("caller_domain"):
            value = dict(value)
            value["caller_service"] = value["caller_domain"]
        return value

    @field_validator("documents")
    @classmethod
    def _validate_documents(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("documents must not be empty")
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("documents must be non-empty strings")
        return value


class RerankResult(BaseModel):
    index: int
    relevance_score: float
    document: str | None = None


class RerankResponse(BaseModel):
    model: str
    results: list[RerankResult]


# --- Response dataclasses ---


@dataclass
class ParsedResult:
    parse_status: str  # succeeded | failed | schema_invalid
    parsed_output: dict | list | None = None
    text_output: str | None = None
    validation_errors: list[str] = field(default_factory=list)


@dataclass
class ErrorInfo:
    error_type: str
    error_message: str


@dataclass
class TaskSubmitResponse:
    task_id: str
    status: str
    idempotency_key: str | None
    created_at: str


@dataclass
class ExecuteResponse:
    task_id: str
    status: str  # succeeded | failed | timeout
    result: ParsedResult | None
    attempts: int
    total_tokens: int | None
    latency_ms: int | None
    error: ErrorInfo | None


@dataclass
class TaskDetail:
    task_id: str
    caller_service: str
    knowledge_domain: str | None
    pipeline_stage: str
    status: str
    metadata: dict[str, Any] | None
    attempt_count: int
    max_attempts: int
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None


@dataclass
class AttemptDetail:
    attempt_id: str
    attempt_no: int
    status: str
    error_type: str | None
    error_message: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int | None
    started_at: str
    finished_at: str | None
