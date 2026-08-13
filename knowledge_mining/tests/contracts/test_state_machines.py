"""Pytest suite for the entity state machines (WP0.5).

References:
- SRS §9.0A (Upload Session), §9.0B (Storage Object), §9.1 (Document Content),
  §9.2 (Parse Run), §9.4 (Snapshot Commit), §9.5 (recovery).
- ADR-0003 D-001 (frozen frozenset style), D-006 (contract tests run in full).
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.contracts.state_machines import (
    IllegalTransition,
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    VALID_DOCUMENT_CONTENT_STATES,
    VALID_SNAPSHOT_COMMIT_STATES,
    VALID_STATES_BY_ENTITY,
    assert_transition,
    is_legal_transition,
    is_terminal,
)


# ---------------------------------------------------------------------------
# Upload Session (SRS §9.0A)
# ---------------------------------------------------------------------------

class TestUploadSession:
    ENTITY = "upload_session"

    @pytest.mark.parametrize("frm,to", [
        ("INITIATED", "UPLOADING"),
        ("UPLOADING", "OBJECT_STAGED"),
        ("OBJECT_STAGED", "VERIFYING"),
        ("VERIFYING", "COMMITTED"),
        ("INITIATED", "ABORTED"),
        ("UPLOADING", "ABORTED"),
        ("OBJECT_STAGED", "ABORTED"),
        ("VERIFYING", "ABORTED"),
        ("INITIATED", "EXPIRED"),
        ("OBJECT_STAGED", "EXPIRED"),
        ("VERIFYING", "EXPIRED"),
        ("OBJECT_STAGED", "REJECTED"),
        ("VERIFYING", "REJECTED"),
    ])
    def test_legal_forward(self, frm, to):
        assert is_legal_transition(self.ENTITY, frm, to) is True
        assert_transition(self.ENTITY, frm, to)  # must not raise

    @pytest.mark.parametrize("frm,to", [
        # COMMITTED is terminal — cannot restart.
        ("COMMITTED", "UPLOADING"),
        ("COMMITTED", "OBJECT_STAGED"),
        # ABORTED/EXPIRED/REJECTED are terminal.
        ("ABORTED", "UPLOADING"),
        ("EXPIRED", "VERIFYING"),
        ("REJECTED", "COMMITTED"),
        # Cannot skip the verify gate.
        ("OBJECT_STAGED", "COMMITTED"),
        # Cannot go backwards.
        ("UPLOADING", "INITIATED"),
        ("VERIFYING", "OBJECT_STAGED"),
        # REJECTED is only reachable from OBJECT_STAGED / VERIFYING.
        ("INITIATED", "REJECTED"),
    ])
    def test_illegal(self, frm, to):
        assert is_legal_transition(self.ENTITY, frm, to) is False
        with pytest.raises(IllegalTransition):
            assert_transition(self.ENTITY, frm, to)

    @pytest.mark.parametrize("state", ["COMMITTED", "ABORTED", "EXPIRED", "REJECTED"])
    def test_terminal(self, state):
        assert is_terminal(self.ENTITY, state) is True

    @pytest.mark.parametrize("state", [
        "INITIATED", "UPLOADING", "OBJECT_STAGED", "VERIFYING",
    ])
    def test_non_terminal(self, state):
        assert is_terminal(self.ENTITY, state) is False


# ---------------------------------------------------------------------------
# Storage Object (SRS §9.0B)
# ---------------------------------------------------------------------------

class TestStorageObject:
    ENTITY = "storage_object"

    @pytest.mark.parametrize("frm,to", [
        ("STAGING", "AVAILABLE"),
        ("AVAILABLE", "DELETING"),
        ("DELETING", "DELETED"),
        ("STAGING", "QUARANTINED"),
        ("AVAILABLE", "QUARANTINED"),
        ("AVAILABLE", "MISSING"),
        ("AVAILABLE", "CORRUPT"),
    ])
    def test_legal(self, frm, to):
        assert is_legal_transition(self.ENTITY, frm, to) is True
        assert_transition(self.ENTITY, frm, to)

    @pytest.mark.parametrize("frm,to", [
        # DELETED is terminal — cannot resurrect.
        ("DELETED", "AVAILABLE"),
        ("DELETED", "STAGING"),
        # Quarantine is an incident state, not a delete path.
        ("QUARANTINED", "DELETED"),
        # Cannot skip AVAILABLE.
        ("STAGING", "DELETING"),
        # Incident states are only reachable from AVAILABLE.
        ("STAGING", "MISSING"),
        ("STAGING", "CORRUPT"),
        # No backwards edges.
        ("AVAILABLE", "STAGING"),
    ])
    def test_illegal(self, frm, to):
        assert is_legal_transition(self.ENTITY, frm, to) is False
        with pytest.raises(IllegalTransition):
            assert_transition(self.ENTITY, frm, to)

    def test_deleted_is_only_terminal(self):
        assert is_terminal(self.ENTITY, "DELETED") is True
        # Incident states block new references but are not terminal.
        for s in ("QUARANTINED", "MISSING", "CORRUPT"):
            assert is_terminal(self.ENTITY, s) is False


# ---------------------------------------------------------------------------
# Document Content (SRS §9.1)
# ---------------------------------------------------------------------------

class TestDocumentContent:
    ENTITY = "document_content"

    @pytest.mark.parametrize("frm,to", [
        ("READY", "UPDATING"),
        ("UPDATING", "READY"),
        ("UPDATING", "UPDATE_FAILED"),
        ("READY", "DELETED"),
        ("DELETED", "READY"),
        ("UPDATE_FAILED", "READY"),
    ])
    def test_legal(self, frm, to):
        assert is_legal_transition(self.ENTITY, frm, to) is True
        assert_transition(self.ENTITY, frm, to)

    @pytest.mark.parametrize("frm,to", [
        # Cannot edit from a non-READY state.
        ("UPDATING", "DELETED"),
        ("UPDATE_FAILED", "UPDATING"),
        ("DELETED", "UPDATING"),
        # Cannot jump from DELETED to UPDATE_FAILED.
        ("DELETED", "UPDATE_FAILED"),
        # No terminal states — every state must have a path back to READY.
    ])
    def test_illegal(self, frm, to):
        assert is_legal_transition(self.ENTITY, frm, to) is False
        with pytest.raises(IllegalTransition):
            assert_transition(self.ENTITY, frm, to)

    def test_no_terminal_states(self):
        # SRS §9.1: even DELETED can be restored, so no state is terminal.
        for state in VALID_DOCUMENT_CONTENT_STATES:
            assert is_terminal(self.ENTITY, state) is False


# ---------------------------------------------------------------------------
# Parse Run (SRS §9.2)
# ---------------------------------------------------------------------------

class TestParseRun:
    ENTITY = "parse_run"

    @pytest.mark.parametrize("frm,to", [
        # Forward spine.
        ("QUEUED", "INSPECTING"),
        ("INSPECTING", "PLANNED"),
        ("PLANNED", "PARSING"),
        ("PARSING", "NORMALIZING"),
        ("NORMALIZING", "RECONCILING"),
        ("RECONCILING", "EVALUATING"),
        # Quality-gate loops (bounded, forward-only graph).
        ("EVALUATING", "REPAIRING"),
        ("REPAIRING", "EVALUATING"),
        ("EVALUATING", "FALLING_BACK"),
        ("FALLING_BACK", "PARSING"),
        # Terminal outcomes.
        ("EVALUATING", "SUCCEEDED"),
        ("EVALUATING", "FAILED"),
    ])
    def test_legal_forward(self, frm, to):
        assert is_legal_transition(self.ENTITY, frm, to) is True
        assert_transition(self.ENTITY, frm, to)

    @pytest.mark.parametrize(
        "frm",
        [
            "QUEUED", "INSPECTING", "PLANNED", "PARSING",
            "NORMALIZING", "RECONCILING", "EVALUATING",
            "REPAIRING", "FALLING_BACK",
        ],
    )
    def test_cancel_from_any_non_terminal(self, frm):
        assert is_legal_transition(self.ENTITY, frm, "CANCELLED") is True

    @pytest.mark.parametrize("frm,to", [
        # Forward-only: no backwards edges.
        ("PLANNED", "INSPECTING"),
        ("NORMALIZING", "PARSING"),
        ("EVALUATING", "RECONCILING"),
        # Cannot shortcut to EVALUATING.
        ("INSPECTING", "EVALUATING"),
        ("PARSING", "EVALUATING"),
        # REPAIR / FALLING_BACK only leave from EVALUATING.
        ("RECONCILING", "REPAIRING"),
        ("NORMALIZING", "FALLING_BACK"),
        # Cannot fall back into INSPECTING (only into PARSING).
        ("FALLING_BACK", "INSPECTING"),
        # Cannot repair-loop back into PARSING directly.
        ("REPAIRING", "PARSING"),
        # Terminal states have no outgoing edges.
        ("SUCCEEDED", "EVALUATING"),
        ("FAILED", "EVALUATING"),
        ("CANCELLED", "EVALUATING"),
        ("SUCCEEDED", "CANCELLED"),
    ])
    def test_illegal(self, frm, to):
        assert is_legal_transition(self.ENTITY, frm, to) is False
        with pytest.raises(IllegalTransition):
            assert_transition(self.ENTITY, frm, to)

    @pytest.mark.parametrize("state", ["SUCCEEDED", "FAILED", "CANCELLED"])
    def test_terminal(self, state):
        assert is_terminal(self.ENTITY, state) is True

    @pytest.mark.parametrize("state", [
        "QUEUED", "INSPECTING", "PLANNED", "PARSING",
        "NORMALIZING", "RECONCILING", "EVALUATING",
        "REPAIRING", "FALLING_BACK",
    ])
    def test_non_terminal(self, state):
        assert is_terminal(self.ENTITY, state) is False


# ---------------------------------------------------------------------------
# Snapshot Commit (SRS §9.4)
# ---------------------------------------------------------------------------

class TestSnapshotCommit:
    ENTITY = "snapshot_commit"

    @pytest.mark.parametrize("frm,to", [
        ("STAGING_ARTIFACTS", "COMPILING"),
        ("COMPILING", "READY"),
        ("STAGING_ARTIFACTS", "FAILED"),
        ("STAGING_ARTIFACTS", "CANCELLED"),
        ("COMPILING", "FAILED"),
        ("COMPILING", "CANCELLED"),
    ])
    def test_legal(self, frm, to):
        assert is_legal_transition(self.ENTITY, frm, to) is True
        assert_transition(self.ENTITY, frm, to)

    @pytest.mark.parametrize("frm,to", [
        # READY is immutable — the strongest invariant in the platform.
        ("READY", "COMPILING"),
        ("READY", "FAILED"),
        ("READY", "CANCELLED"),
        ("READY", "STAGING_ARTIFACTS"),
        # Cannot skip COMPILING.
        ("STAGING_ARTIFACTS", "READY"),
        # Cannot restart from terminal states.
        ("FAILED", "COMPILING"),
        ("CANCELLED", "STAGING_ARTIFACTS"),
        # No backwards edges.
        ("COMPILING", "STAGING_ARTIFACTS"),
    ])
    def test_illegal(self, frm, to):
        assert is_legal_transition(self.ENTITY, frm, to) is False
        with pytest.raises(IllegalTransition):
            assert_transition(self.ENTITY, frm, to)

    @pytest.mark.parametrize("state", ["READY", "FAILED", "CANCELLED"])
    def test_terminal(self, state):
        assert is_terminal(self.ENTITY, state) is True

    def test_ready_is_strongest_terminal(self):
        # SRS §9.4: READY is immutable — extra emphasis.
        assert is_terminal(self.ENTITY, "READY") is True
        assert all(
            ("READY", to) not in LEGAL_TRANSITIONS[self.ENTITY]
            for to in VALID_SNAPSHOT_COMMIT_STATES
        )


# ---------------------------------------------------------------------------
# Cross-entity guards: unknown entity / unknown state
# ---------------------------------------------------------------------------

class TestGuards:
    def test_unknown_entity_is_legal_false(self):
        assert is_legal_transition("martian_session", "A", "B") is False

    def test_unknown_entity_assert_raises(self):
        with pytest.raises(IllegalTransition) as excinfo:
            assert_transition("martian_session", "A", "B")
        assert excinfo.value.entity == "martian_session"
        assert "unknown entity" in excinfo.value.reason

    def test_unknown_from_state(self):
        with pytest.raises(IllegalTransition) as excinfo:
            assert_transition("upload_session", "BOGUS", "UPLOADING")
        assert "unknown from_state" in excinfo.value.reason

    def test_unknown_to_state(self):
        with pytest.raises(IllegalTransition) as excinfo:
            assert_transition("parse_run", "QUEUED", "BOGUS")
        assert "unknown to_state" in excinfo.value.reason

    def test_is_terminal_unknown_entity_is_false(self):
        assert is_terminal("martian_session", "ANYTHING") is False

    def test_every_entity_has_transitions_and_terminals(self):
        # Registry consistency: both dicts must cover the same entity set.
        assert set(LEGAL_TRANSITIONS) == set(TERMINAL_STATES)
        assert set(LEGAL_TRANSITIONS) == set(VALID_STATES_BY_ENTITY)
        # Every legal edge endpoint must be in the entity's valid-state set.
        for entity, edges in LEGAL_TRANSITIONS.items():
            valid = VALID_STATES_BY_ENTITY[entity]
            for frm, to in edges:
                assert frm in valid, f"{entity}: {frm} not in valid set"
                assert to in valid, f"{entity}: {to} not in valid set"
        # Every terminal state must be in the entity's valid-state set.
        for entity, terminals in TERMINAL_STATES.items():
            valid = VALID_STATES_BY_ENTITY[entity]
            for s in terminals:
                assert s in valid, f"{entity}: terminal {s} not in valid set"
