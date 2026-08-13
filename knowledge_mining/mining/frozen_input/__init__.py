"""Frozen Input Binding + Safe Intake + SourceArtifactReader (M1.4 / WP1D).

This package implements the three pieces of SRS §C02 + §C03 (safe subset) +
§3.2 that run a knowledge update against an *immutable snapshot* of a document
rather than its live ``current`` pointer:

- ``contracts``  — ``FrozenInput`` / ``IntakeVerdict`` frozen dataclasses and
  the intake / staleness error types.
- ``safe_intake`` — pure-stdlib MIME / format / archive / path-traversal
  inspection (SRS §2.4, §C03 safety subset).
- ``service`` — ``FrozenInputService.freeze`` / ``check_stale``: snapshot the
  ``(storage_object_id, raw_hash, content_revision)`` triple and detect
  concurrent edits before the parse is committed (SRS §3.2).
- ``source_reader`` — ``ObjectStoreSourceArtifactReader``: stream / materialize
  the frozen object bytes with streaming sha256 verification (D-020,
  SRS §C00/§10.2).

Design (ADR-0003 D-024):
- Lives in its own package so the legacy ``mining/jobs/run.py`` freeze path
  (which freezes ``raw_content_hash`` / domain / channel / ontology) is left
  untouched during migration; the two coexist until the jobs layer migrates.
- Pure stdlib throughout — no python-magic, no PG, no MinIO SDK at import
  time. Tests use the in-memory ``DocumentCurrentContentRepository`` +
  ``StorageObjectRepository`` plus ``FakeObjectStore``, so the whole suite is
  green without PostgreSQL (per the M1.4 environment constraint).
- All functions < 50 lines, files < 800 lines, fully type-annotated.
"""
from __future__ import annotations

from knowledge_mining.mining.frozen_input.contracts import (
    FrozenInput,
    FrozenInputStale,
    IntakeVerdict,
    UnsafeFile,
    UnsupportedFile,
)
from knowledge_mining.mining.frozen_input.safe_intake import SafeIntake
from knowledge_mining.mining.frozen_input.service import FrozenInputService
from knowledge_mining.mining.frozen_input.source_reader import (
    ObjectStoreSourceArtifactReader,
)

__all__ = [
    "FrozenInput",
    "FrozenInputService",
    "FrozenInputStale",
    "IntakeVerdict",
    "ObjectStoreSourceArtifactReader",
    "SafeIntake",
    "UnsafeFile",
    "UnsupportedFile",
]
