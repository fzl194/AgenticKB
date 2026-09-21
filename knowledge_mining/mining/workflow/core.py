from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum, StrEnum
from types import MappingProxyType
from typing import Any, Mapping


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_thaw_value(item) for item in value), key=str)
    return value


class SlotType(str, Enum):
    INPUT_SPEC = "INPUT_SPEC"
    RAW_FILE_BATCH = "RAW_FILE_BATCH"
    DOCUMENT_BATCH = "DOCUMENT_BATCH"
    FINALIZE_INPUT = "FINALIZE_INPUT"
    FINALIZE_RESULT = "FINALIZE_RESULT"


class ExecutionZone(str, Enum):
    INPUT = "input"
    DOCUMENT = "document"
    GLOBAL = "global"


class EditPolicy(str, Enum):
    FIXED = "fixed"
    PROTECTED = "protected"
    EDITABLE = "editable"


class ErrorPolicy(str, Enum):
    FAIL_FAST = "FAIL_FAST"
    SKIP_DOCUMENT = "SKIP_DOCUMENT"
    SKIP_WITH_EMPTY = "SKIP_WITH_EMPTY"
    FALLBACK = "FALLBACK"
    PAUSE_FOR_REVIEW = "PAUSE_FOR_REVIEW"


class OperatorStatus(StrEnum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    FALLBACK = "fallback"
    FAILED = "failed"
    PAUSED = "paused"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class OperatorWarning:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", _freeze_value(self.details))


@dataclass(frozen=True)
class OperatorResult:
    outputs: object | None
    capabilities: frozenset[str]
    status: OperatorStatus
    warnings: tuple[OperatorWarning, ...] = ()
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "warnings", tuple(self.warnings))


@dataclass(frozen=True)
class DocumentState:
    run_document_id: str
    doc_key: str
    context: Any
    capabilities: frozenset[str] = frozenset()
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.run_document_id:
            raise ValueError("run_document_id is required")
        if not self.doc_key:
            raise ValueError("doc_key is required")
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "tags", tuple(self.tags))

    def fork(self) -> "DocumentState":
        return DocumentState(
            self.run_document_id,
            self.doc_key,
            deepcopy(self.context),
            self.capabilities,
            self.tags,
        )

    def with_context(
        self, context: Any, *, capabilities: frozenset[str] = frozenset()
    ) -> "DocumentState":
        return DocumentState(
            self.run_document_id,
            self.doc_key,
            context,
            self.capabilities | capabilities,
            self.tags,
        )

    @staticmethod
    def batch(states: list["DocumentState"] | tuple["DocumentState", ...]) -> tuple["DocumentState", ...]:
        result = tuple(states)
        identities = [item.run_document_id for item in result]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate run_document_id in document batch")
        return result

    @staticmethod
    def merge_batches(
        batches: list[tuple["DocumentState", ...]],
    ) -> tuple["DocumentState", ...]:
        if not batches:
            return ()
        normalized = [DocumentState.batch(batch) for batch in batches]
        first = normalized[0]
        order = [item.run_document_id for item in first]
        expected = set(order)
        indexes = [
            {item.run_document_id: item for item in batch} for batch in normalized
        ]
        for index in indexes[1:]:
            if set(index) != expected:
                raise ValueError("document branch identity set mismatch")

        merged: list[DocumentState] = []
        for identity in order:
            states = [index[identity] for index in indexes]
            doc_key = states[0].doc_key
            if any(item.doc_key != doc_key for item in states[1:]):
                raise ValueError(f"doc_key drift for {identity}")
            tags: list[str] = []
            capabilities: set[str] = set()
            for item in states:
                capabilities.update(item.capabilities)
                for tag in item.tags:
                    if tag not in tags:
                        tags.append(tag)
            context = deepcopy(states[0].context)
            for item in states[1:]:
                context = _merge_document_context(context, item.context)
            merged.append(DocumentState(
                identity, doc_key, context, frozenset(capabilities), tuple(tags)
            ))
        return tuple(merged)


def _merge_document_context(left: Any, right: Any) -> Any:
    if left == right:
        return left
    if not hasattr(left, "with_updates") or not hasattr(right, "with_updates"):
        return deepcopy(right)
    updates: dict[str, Any] = {}
    for name in (
        "raw_file",
        "profile",
        "tree",
        "action",
        "existing_doc",
        "document_id",
        "snapshot_id",
    ):
        left_value = getattr(left, name, None)
        right_value = getattr(right, name, None)
        if left_value is None and right_value is not None:
            updates[name] = deepcopy(right_value)
    left_segments = tuple(getattr(left, "segments", ()) or ())
    right_segments = tuple(getattr(right, "segments", ()) or ())
    merged_segments = _merge_segments(left_segments, right_segments)
    if merged_segments:
        updates["segments"] = merged_segments

    for name, key in (
        ("relations", lambda item: (
            item.source_segment_key, item.target_segment_key, item.relation_type
        )),
        ("retrieval_units", lambda item: item.unit_key),
        ("embeddings", lambda item: item.get("unit_key")),
    ):
        combined = list(getattr(left, name, ()) or ())
        seen = {key(item) for item in combined}
        for item in getattr(right, name, ()) or ():
            if key(item) not in seen:
                combined.append(deepcopy(item))
                seen.add(key(item))
        if combined:
            updates[name] = tuple(combined)
    left_ids = dict(getattr(left, "seg_ids", {}) or {})
    left_ids.update(deepcopy(getattr(right, "seg_ids", {}) or {}))
    if left_ids:
        updates["seg_ids"] = left_ids
    if merged_segments:
        units = tuple(
            updates.get("retrieval_units", getattr(left, "retrieval_units", ()) or ())
        )
        if units:
            refs_by_segment = {
                f"{item.document_key}#{item.segment_index}": item.entity_refs_json
                for item in merged_segments
            }
            updates["retrieval_units"] = tuple(
                replace(
                    item,
                    entity_refs_json=deepcopy(
                        refs_by_segment.get(item.segment_key, item.entity_refs_json)
                    ),
                )
                for item in units
            )
    return left.with_updates(**updates) if updates else left


def _merge_segments(left: tuple[Any, ...], right: tuple[Any, ...]) -> tuple[Any, ...]:
    """Merge branch-local segment annotations by stable document/index identity."""

    if not left:
        return deepcopy(right)
    if not right:
        return deepcopy(left)
    identity = lambda item: (item.document_key, item.segment_index)
    left_keys = [identity(item) for item in left]
    right_by_key = {identity(item): item for item in right}
    if len(right_by_key) != len(right) or set(left_keys) != set(right_by_key):
        raise ValueError("document segment identity set mismatch")

    merged = []
    for left_item in left:
        right_item = right_by_key[identity(left_item)]
        refs = deepcopy(left_item.entity_refs_json)
        for ref in right_item.entity_refs_json:
            if ref not in refs:
                refs.append(deepcopy(ref))
        metadata = _merge_mappings(left_item.metadata_json, right_item.metadata_json)
        semantic_role = left_item.semantic_role
        if semantic_role == "unknown" and right_item.semantic_role != "unknown":
            semantic_role = right_item.semantic_role
        merged.append(replace(
            left_item,
            semantic_role=semantic_role,
            entity_refs_json=refs,
            metadata_json=metadata,
        ))
    return tuple(merged)


def _merge_mappings(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(dict(left))
    for key, value in right.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _merge_mappings(current, value)
        elif current in (None, "", [], {}, ()) or current == value:
            merged[key] = deepcopy(value)
        elif key not in merged:
            merged[key] = deepcopy(value)
        else:
            # Preserve the first predecessor on conflicts. This matches the DAG's
            # deterministic edge order while still adding annotations that exist
            # only on a later branch.
            continue
    return merged


@dataclass(frozen=True)
class OperatorRuntimeContext:
    domain: str
    channel: str
    domain_profile: Any
    asset_repository: Any
    runtime_repository: Any
    tracker: Any
    services: Any
    publish_lock_provider: Any
    cancellation_check: Any
    manifest: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest", _freeze_value(self.manifest))


@dataclass(frozen=True)
class SlotDecl:
    name: str
    type: SlotType
    required: bool = True
    variadic: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type.value,
            "required": self.required,
            "variadic": self.variadic,
            "description": self.description,
        }


@dataclass(frozen=True)
class MiningOperatorDef:
    type: str
    version: str
    display_name: str
    description: str
    category: str
    zone: ExecutionZone
    edit_policy: EditPolicy
    input_slots: tuple[SlotDecl, ...]
    output_slots: tuple[SlotDecl, ...]
    requires: frozenset[str] = frozenset()
    provides: frozenset[str] = frozenset()
    param_schema_json: Mapping[str, Any] = field(default_factory=dict)
    error_policy: ErrorPolicy = ErrorPolicy.FAIL_FAST
    unique: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_slots", tuple(self.input_slots))
        object.__setattr__(self, "output_slots", tuple(self.output_slots))
        object.__setattr__(self, "requires", frozenset(self.requires))
        object.__setattr__(self, "provides", frozenset(self.provides))
        object.__setattr__(self, "param_schema_json", _freeze_value(self.param_schema_json))

    def input_slot(self, name: str) -> SlotDecl | None:
        return next((slot for slot in self.input_slots if slot.name == name), None)

    def output_slot(self, name: str) -> SlotDecl | None:
        return next((slot for slot in self.output_slots if slot.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "version": self.version,
            "displayName": self.display_name,
            "description": self.description,
            "category": self.category,
            "zone": self.zone.value,
            "editPolicy": self.edit_policy.value,
            "inputSlots": [slot.to_dict() for slot in self.input_slots],
            "outputSlots": [slot.to_dict() for slot in self.output_slots],
            "requires": sorted(self.requires),
            "provides": sorted(self.provides),
            "paramSchemaJson": _thaw_value(self.param_schema_json),
            "errorPolicy": self.error_policy.value,
            "unique": self.unique,
        }
