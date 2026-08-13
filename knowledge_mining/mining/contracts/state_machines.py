"""Entity state machines for the document-parse platform (WP0.5).

This module is the single source of truth for *which states an entity may be
in* and *which transitions between those states are legal*. It mirrors the
state machines defined in
`docs/文档解析平台化-能力规格与工作拆解.md` §9.0A–§9.5, and the DB ``CHECK``
constraints that will be added in WP0.4.

Design (ADR-0003 D-001):
- Pure stdlib. No business-layer / DB / FastAPI imports.
- Frozen constants only — ``frozenset`` of state strings and
  ``frozenset[tuple[str, str]]`` of legal ``(from, to)`` edges.
- Upload Session / Storage Object state *sets* are re-exported from
  :mod:`contracts.storage.enums` (single source of truth); the new Document
  Content / Parse Run / Snapshot Commit sets are defined here.

References:
- SRS §9.0A (Upload Session), §9.0B (Storage Object), §9.1 (Document Content),
  §9.2 (Parse Run), §9.3 (Document Snapshot), §9.4 (Snapshot Commit),
  §9.5 (recovery strategies).
- ADR-0003 D-001 (frozen dataclass + ``VALID_*`` frozenset style), D-006
  (contract-layer unit tests run in full).
"""
from __future__ import annotations

# Re-export the storage-layer state sets so callers can import every entity's
# valid-state set from one place without creating a second definition.
from knowledge_mining.mining.contracts.storage.enums import (
    VALID_STORAGE_OBJECT_STATES,
    VALID_UPLOAD_SESSION_STATES,
)

# ---------------------------------------------------------------------------
# Valid state sets
# ---------------------------------------------------------------------------

# Upload Session / Storage Object: defined in storage.enums (§9.0A / §9.0B).
# Re-exported here under entity-aligned names for the LEGAL_TRANSITIONS table.
VALID_UPLOAD_SESSION_STATES: frozenset[str] = VALID_UPLOAD_SESSION_STATES  # noqa: F811
VALID_STORAGE_OBJECT_STATES: frozenset[str] = VALID_STORAGE_OBJECT_STATES  # noqa: F811

# Document Content states (SRS §9.1).
# READY -> UPDATING -> READY
#       -> UPDATE_FAILED
# READY -> DELETED -> READY (restored)
VALID_DOCUMENT_CONTENT_STATES: frozenset[str] = frozenset({
    "READY",
    "UPDATING",
    "UPDATE_FAILED",
    "DELETED",
})

# Parse Run states (SRS §9.2).
# QUEUED -> INSPECTING -> PLANNED -> PARSING -> NORMALIZING -> RECONCILING
#        -> EVALUATING
# EVALUATING -> REPAIRING -> EVALUATING
# EVALUATING -> FALLING_BACK -> PARSING
# EVALUATING -> SUCCEEDED / FAILED
# any non-terminal -> CANCELLED
VALID_PARSE_RUN_STATES: frozenset[str] = frozenset({
    "QUEUED",
    "INSPECTING",
    "PLANNED",
    "PARSING",
    "NORMALIZING",
    "RECONCILING",
    "EVALUATING",
    "REPAIRING",
    "FALLING_BACK",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
})

# Snapshot Commit states (SRS §9.4).
# STAGING_ARTIFACTS -> COMPILING -> READY
#                    -> FAILED
#                    -> CANCELLED
# READY is immutable (terminal).
VALID_SNAPSHOT_COMMIT_STATES: frozenset[str] = frozenset({
    "STAGING_ARTIFACTS",
    "COMPILING",
    "READY",
    "FAILED",
    "CANCELLED",
})


# ---------------------------------------------------------------------------
# Entity registry
# ---------------------------------------------------------------------------

# Map entity name -> valid state set. Used for unknown-state rejection and as
# the source of truth for TERMINAL_STATES / LEGAL_TRANSITIONS keys.
_ENTITY_STATE_SETS: dict[str, frozenset[str]] = {
    "upload_session": VALID_UPLOAD_SESSION_STATES,
    "storage_object": VALID_STORAGE_OBJECT_STATES,
    "document_content": VALID_DOCUMENT_CONTENT_STATES,
    "parse_run": VALID_PARSE_RUN_STATES,
    "snapshot_commit": VALID_SNAPSHOT_COMMIT_STATES,
}

# Public alias requested by the task spec (dict[str, frozenset[str]] shape).
VALID_STATES_BY_ENTITY: dict[str, frozenset[str]] = dict(_ENTITY_STATE_SETS)


# ---------------------------------------------------------------------------
# Legal transition graphs (SRS §9)
# ---------------------------------------------------------------------------

# Upload Session (§9.0A):
#   INITIATED -> UPLOADING -> OBJECT_STAGED -> VERIFYING -> COMMITTED
#   INITIATED / UPLOADING / OBJECT_STAGED / VERIFYING -> ABORTED
#   INITIATED / UPLOADING / OBJECT_STAGED / VERIFYING -> EXPIRED
#   OBJECT_STAGED / VERIFYING -> REJECTED
_UPLOAD_SESSION_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("INITIATED", "UPLOADING"),
    ("UPLOADING", "OBJECT_STAGED"),
    ("OBJECT_STAGED", "VERIFYING"),
    ("VERIFYING", "COMMITTED"),
    # Abort paths: any non-terminal pre-commit state may abort.
    ("INITIATED", "ABORTED"),
    ("UPLOADING", "ABORTED"),
    ("OBJECT_STAGED", "ABORTED"),
    ("VERIFYING", "ABORTED"),
    # Expiry paths: any non-terminal pre-commit state may expire.
    ("INITIATED", "EXPIRED"),
    ("UPLOADING", "EXPIRED"),
    ("OBJECT_STAGED", "EXPIRED"),
    ("VERIFYING", "EXPIRED"),
    # Rejection: verification gate may reject.
    ("OBJECT_STAGED", "REJECTED"),
    ("VERIFYING", "REJECTED"),
})

# Storage Object (§9.0B):
#   STAGING -> AVAILABLE -> DELETING -> DELETED
#   STAGING / AVAILABLE -> QUARANTINED
#   AVAILABLE -> MISSING / CORRUPT
_STORAGE_OBJECT_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("STAGING", "AVAILABLE"),
    ("AVAILABLE", "DELETING"),
    ("DELETING", "DELETED"),
    ("STAGING", "QUARANTINED"),
    ("AVAILABLE", "QUARANTINED"),
    ("AVAILABLE", "MISSING"),
    ("AVAILABLE", "CORRUPT"),
})

# Document Content (§9.1):
#   READY -> UPDATING -> READY
#         -> UPDATE_FAILED
#   READY -> DELETED -> READY (restored)
#   UPDATE_FAILED -> READY (operator re-arms after fixing the edit pipeline)
_DOCUMENT_CONTENT_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("READY", "UPDATING"),
    ("UPDATING", "READY"),
    ("UPDATING", "UPDATE_FAILED"),
    ("READY", "DELETED"),
    ("DELETED", "READY"),
    ("UPDATE_FAILED", "READY"),
})

# Parse Run (§9.2): forward-only; REPAIR/FALLING_BACK are bounded loops.
# any non-terminal -> CANCELLED (explicitly enumerated to stay a closed set).
_PARSE_RUN_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    # Forward spine.
    ("QUEUED", "INSPECTING"),
    ("INSPECTING", "PLANNED"),
    ("PLANNED", "PARSING"),
    ("PARSING", "NORMALIZING"),
    ("NORMALIZING", "RECONCILING"),
    ("RECONCILING", "EVALUATING"),
    # Quality-gate loops.
    ("EVALUATING", "REPAIRING"),
    ("REPAIRING", "EVALUATING"),
    ("EVALUATING", "FALLING_BACK"),
    ("FALLING_BACK", "PARSING"),
    # Terminal outcomes from the evaluation gate.
    ("EVALUATING", "SUCCEEDED"),
    ("EVALUATING", "FAILED"),
    # Cancellation from any non-terminal state.
    ("QUEUED", "CANCELLED"),
    ("INSPECTING", "CANCELLED"),
    ("PLANNED", "CANCELLED"),
    ("PARSING", "CANCELLED"),
    ("NORMALIZING", "CANCELLED"),
    ("RECONCILING", "CANCELLED"),
    ("EVALUATING", "CANCELLED"),
    ("REPAIRING", "CANCELLED"),
    ("FALLING_BACK", "CANCELLED"),
})

# Snapshot Commit (§9.4):
#   STAGING_ARTIFACTS -> COMPILING -> READY
#                      -> FAILED
#                      -> CANCELLED
#   COMPILING -> FAILED / CANCELLED
# READY is immutable (terminal); no outgoing edges.
_SNAPSHOT_COMMIT_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("STAGING_ARTIFACTS", "COMPILING"),
    ("COMPILING", "READY"),
    ("STAGING_ARTIFACTS", "FAILED"),
    ("STAGING_ARTIFACTS", "CANCELLED"),
    ("COMPILING", "FAILED"),
    ("COMPILING", "CANCELLED"),
})

LEGAL_TRANSITIONS: dict[str, frozenset[tuple[str, str]]] = {
    "upload_session": _UPLOAD_SESSION_TRANSITIONS,
    "storage_object": _STORAGE_OBJECT_TRANSITIONS,
    "document_content": _DOCUMENT_CONTENT_TRANSITIONS,
    "parse_run": _PARSE_RUN_TRANSITIONS,
    "snapshot_commit": _SNAPSHOT_COMMIT_TRANSITIONS,
}


# ---------------------------------------------------------------------------
# Terminal states (SRS §9)
# ---------------------------------------------------------------------------

# Upload Session (§9.0A): COMMITTED / ABORTED / EXPIRED / REJECTED.
_UPLOAD_SESSION_TERMINAL: frozenset[str] = frozenset({
    "COMMITTED", "ABORTED", "EXPIRED", "REJECTED",
})

# Storage Object (§9.0B): DELETED is the only hard terminal. QUARANTINED,
# MISSING and CORRUPT are incident states — they block new references but are
# not necessarily final (an operator may quarantine-then-delete, or restore a
# MISSING object from replica). Only DELETED has no outgoing legal edge.
_STORAGE_OBJECT_TERMINAL: frozenset[str] = frozenset({"DELETED"})

# Document Content (§9.1): no true terminal — even DELETED can be restored.
# Exposed as the empty set so is_terminal() returns False for every state.
_DOCUMENT_CONTENT_TERMINAL: frozenset[str] = frozenset()

# Parse Run (§9.2): SUCCEEDED / FAILED / CANCELLED are terminal.
_PARSE_RUN_TERMINAL: frozenset[str] = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})

# Snapshot Commit (§9.4): READY is immutable; FAILED / CANCELLED are terminal.
_SNAPSHOT_COMMIT_TERMINAL: frozenset[str] = frozenset({"READY", "FAILED", "CANCELLED"})

TERMINAL_STATES: dict[str, frozenset[str]] = {
    "upload_session": _UPLOAD_SESSION_TERMINAL,
    "storage_object": _STORAGE_OBJECT_TERMINAL,
    "document_content": _DOCUMENT_CONTENT_TERMINAL,
    "parse_run": _PARSE_RUN_TERMINAL,
    "snapshot_commit": _SNAPSHOT_COMMIT_TERMINAL,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class IllegalTransition(ValueError):
    """Raised when a state transition violates the entity's state machine.

    Carries the ``entity``, ``from_state`` and ``to_state`` as attributes so
    callers (and tests) can inspect the offending transition without parsing
    the message.
    """

    def __init__(self, entity: str, from_state: str, to_state: str, reason: str) -> None:
        self.entity = entity
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        super().__init__(
            f"Illegal transition for entity {entity!r}: "
            f"{from_state!r} -> {to_state!r} ({reason})"
        )


def is_legal_transition(entity: str, from_state: str, to_state: str) -> bool:
    """Return True iff ``from_state -> to_state`` is legal for ``entity``.

    Returns False (rather than raising) when:
    - ``entity`` is not a known entity name, or
    - ``from_state`` / ``to_state`` are not members of the entity's valid
      state set, or
    - the ``(from_state, to_state)`` pair is not in ``LEGAL_TRANSITIONS``.
    """
    valid_states = _ENTITY_STATE_SETS.get(entity)
    if valid_states is None:
        return False
    if from_state not in valid_states or to_state not in valid_states:
        return False
    return (from_state, to_state) in LEGAL_TRANSITIONS[entity]


def assert_transition(entity: str, from_state: str, to_state: str) -> None:
    """Raise :class:`IllegalTransition` unless the transition is legal.

    Distinguishes three failure reasons in the exception message:
    - unknown entity name,
    - unknown state(s) for the entity,
    - legal states but the edge itself is forbidden (e.g. a terminal state
      has no outgoing edge, or a forward-only machine is asked to go back).
    """
    valid_states = _ENTITY_STATE_SETS.get(entity)
    if valid_states is None:
        raise IllegalTransition(
            entity, from_state, to_state,
            f"unknown entity; expected one of {sorted(_ENTITY_STATE_SETS)}",
        )
    if from_state not in valid_states:
        raise IllegalTransition(
            entity, from_state, to_state,
            f"unknown from_state for {entity}; valid={sorted(valid_states)}",
        )
    if to_state not in valid_states:
        raise IllegalTransition(
            entity, from_state, to_state,
            f"unknown to_state for {entity}; valid={sorted(valid_states)}",
        )
    if (from_state, to_state) not in LEGAL_TRANSITIONS[entity]:
        terminal = TERMINAL_STATES.get(entity, frozenset())
        if from_state in terminal:
            reason = f"{from_state!r} is a terminal state for {entity}"
        else:
            reason = (
                f"edge {from_state!r} -> {to_state!r} not in legal graph for "
                f"{entity} (see SRS §9)"
            )
        raise IllegalTransition(entity, from_state, to_state, reason)


def is_terminal(entity: str, state: str) -> bool:
    """Return True iff ``state`` is terminal for ``entity``.

    Returns False for an unknown entity (defensive: callers should validate
    the entity name separately via :data:`VALID_STATES_BY_ENTITY`).
    """
    return state in TERMINAL_STATES.get(entity, frozenset())


__all__ = [
    # state sets
    "VALID_UPLOAD_SESSION_STATES",
    "VALID_STORAGE_OBJECT_STATES",
    "VALID_DOCUMENT_CONTENT_STATES",
    "VALID_PARSE_RUN_STATES",
    "VALID_SNAPSHOT_COMMIT_STATES",
    "VALID_STATES_BY_ENTITY",
    # graphs
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATES",
    # helpers
    "IllegalTransition",
    "is_legal_transition",
    "assert_transition",
    "is_terminal",
]
