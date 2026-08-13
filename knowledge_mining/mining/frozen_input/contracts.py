"""Contracts for the Frozen Input layer (M1.4 / WP1D; SRS §3.2, §C02, §2.4).

This module is the hexagonal seam above the file-management repositories and
the object store. It defines:

- The frozen *binding* a Mining Run captures at start time (``FrozenInput``)
  so the parser keeps reading the same bytes even when the user edits the
  document mid-run (SRS §3.2: "current 切到 B, 本 Run 仍处理 A").
- The safe-intake verdict (``IntakeVerdict``) produced by pure signature /
  archive inspection (SRS §C03 safe subset, §2.4 security invariants).
- The error types raised by intake (``UnsupportedFile`` / ``UnsafeFile``) and
  by post-freeze staleness checks (``FrozenInputStale``).

Design (ADR-0003 D-024):
- Pure stdlib frozen dataclasses, consistent with
  ``contracts/storage/types.py`` (D-001).
- Intake / staleness errors subclass ``FileManagementError`` so the existing
  ``StorageError`` -> HTTP mapping (SRS §C01 table) extends to this layer
  uniformly: ``unsupported_file`` / ``unsafe_file`` -> 422,
  ``frozen_input_stale`` -> 409.
- ``IntakeVerdict`` is *separate* from ``FrozenInput``: verdicts are produced
  by stateless byte inspection (pre-freeze admission), bindings are produced
  by snapshotting repository state (post-admission freeze). Mixing them would
  couple two unrelated concerns.

References:
- SRS §3.2 (Frozen Source Binding), §2.4 (security invariants),
  §C02 (Frozen Input Binding capability), §C03 (File Inspector safe subset).
- ADR-0003 D-020 (SourceArtifactReader = Repository+ObjectStore composition),
  D-022 (Repository Protocol layering), D-024 (this package).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from knowledge_mining.mining.contracts.file_management import FileManagementError


# ---------------------------------------------------------------------------
# Errors (SRS §C01 -> HTTP: 422 / 409)
# ---------------------------------------------------------------------------


class UnsupportedFile(FileManagementError):
    """File is not in the supported format set (SRS §C03, 422).

    Raised when the detected MIME / format is outside the platform's parser
    coverage (e.g. an executable, an unknown binary). Distinct from
    ``UnsafeFile``: unsupported files are rejected by *policy*, unsafe files
    are rejected by *security*.
    """

    code = "unsupported_file"

    def __init__(self, message: str = "", *, detected_mime: str = "") -> None:
        self.detected_mime = detected_mime
        msg = message or f"unsupported file (mime={detected_mime!r})"
        super().__init__(msg)


class UnsafeFile(FileManagementError):
    """File fails a security invariant (SRS §2.4, 422).

    Raised for path traversal in archive members, archive limits exceeded
    (member count / expanded size / compression ratio), and encrypted files
    whose policy forbids processing. The ``reason`` field carries the stable
    machine-readable sub-reason for audit / metrics.
    """

    code = "unsafe_file"

    def __init__(
        self,
        message: str = "",
        *,
        reason: str = "",
    ) -> None:
        self.reason = reason or "unsafe_file"
        msg = message or f"unsafe file ({self.reason})"
        super().__init__(msg)


class FrozenInputStale(FileManagementError):
    """The frozen input's content_revision no longer matches the document's.

    Raised by ``FrozenInputService.check_stale`` when, between freeze time and
    parse-commit time, another writer advanced the document's
    ``content_revision`` (SRS §3.2 / §9.5: "完成时输入已过期则不自动发布").
    The produced Snapshot may be retained, but it MUST NOT auto-promote to
    the latest knowledge — the caller marks it stale and re-queues against B.
    """

    code = "frozen_input_stale"

    def __init__(
        self,
        document_id: str,
        frozen_revision: int,
        current_revision: int,
        message: str = "",
    ) -> None:
        self.document_id = document_id
        self.frozen_revision = frozen_revision
        self.current_revision = current_revision
        msg = message or (
            f"frozen input stale for document {document_id!r}: "
            f"frozen revision {frozen_revision}, current {current_revision}"
        )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Safe-intake verdict (SRS §C03 safe subset, §2.4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntakeVerdict:
    """Result of stateless byte inspection (SRS §C03 safe subset).

    Produced by ``SafeIntake.inspect`` over the first N bytes plus the
    declared filename / size. ``ok`` is True only when the file is both
    *supported* and *safe*; callers that need finer-grained routing read the
    individual fields (``detected_mime`` / ``detected_format`` /
    ``encrypted`` / ``is_archive``).

    ``errors`` and ``warnings`` are tuples (immutable) of human-readable
    strings suitable for the audit event payload (SRS §2.4).
    """

    ok: bool
    detected_mime: str
    detected_format: str
    encrypted: bool
    is_archive: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Frozen binding (SRS §3.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenInput:
    """Immutable snapshot of the bytes a Mining Run will parse (SRS §3.2).

    Captured at run-start by ``FrozenInputService.freeze``. The parser /
    materializer reads exclusively off these fields for the lifetime of the
    run; the document's live ``current`` pointer is allowed to move to a new
    Storage Object (content revision bump) without affecting this run.

    The binding is the *minimum* information needed to (a) resolve the object
    location via the StorageObjectRepository, (b) verify the streamed bytes
    against ``source_raw_hash``, and (c) detect post-freeze staleness by
    comparing ``source_content_revision``.
    """

    document_id: str
    source_storage_object_id: str
    source_raw_hash: str
    source_content_revision: int
    mime: str
    size: int
    original_filename: str
    captured_at: str
    # Object location snapshot for convenience / SourceArtifactReader use.
    # Resolved at freeze time so the reader does not need a second repo round
    # trip; the authoritative source is still ``source_storage_object_id``.
    provider: str = ""
    bucket: str = ""
    object_key: str = ""
    object_version_id: str | None = None


__all__ = [
    "FrozenInput",
    "FrozenInputStale",
    "IntakeVerdict",
    "UnsafeFile",
    "UnsupportedFile",
]
