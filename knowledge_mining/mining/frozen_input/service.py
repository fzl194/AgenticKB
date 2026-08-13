"""``FrozenInputService`` — capture and staleness-check the run input (M1.4).

Implements SRS §3.2 (Frozen Source Binding) + §C02 (Frozen Input Binding
capability): at Mining Run start, snapshot the document's current
``(storage_object_id, raw_hash, content_revision)`` triple into a
``FrozenInput``; before the parse is committed, verify the document's live
revision still matches so a concurrent edit does not silently produce
knowledge from a stale baseline.

Layering (ADR-0003 D-024):
- Depends only on the M1.2 Repository Protocols
  (``DocumentCurrentContentRepository`` + ``StorageObjectRepository``) and the
  M1.1 ``ObjectStorePort``. No PG, no MinIO SDK at import time.
- ``freeze`` resolves the storage object record to capture location metadata
  for the ``SourceArtifactReader`` (avoids a second repo round-trip when the
  reader opens the stream).
- ``check_stale`` is *advisory*: it raises ``FrozenInputStale`` and lets the
  caller decide whether to retain the Snapshot (without auto-promoting) or
  discard it — matching SRS §3.2 ("不得自动成为最新知识").

Coexistence with legacy:
- The legacy ``mining/jobs/run.py`` path freezes a different tuple
  (``raw_content_hash`` / domain / channel / ontology) and is intentionally
  untouched during migration. This service is the *new* binding the
  platform-layer parser will consume once it migrates; both can coexist
  because they read different fields off the same document row.

References:
- SRS §3.2, §9.5, §C02.
- ADR-0003 D-020 (location addressing), D-022 (Repository Protocols),
  D-024 (this package).
"""
from __future__ import annotations

from datetime import datetime, timezone

from knowledge_mining.mining.contracts.file_management import (
    DocumentCurrentContent,
    DocumentCurrentContentRepository,
    StorageObjectRecord,
    StorageObjectRepository,
)
from knowledge_mining.mining.contracts.storage.errors import StorageObjectMissing
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.frozen_input.contracts import (
    FrozenInput,
    FrozenInputStale,
)

#: Storage-object lifecycle states that are safe to bind a run to. Only
#: ``AVAILABLE`` objects have verified bytes (SRS §9.0B). ``STAGING`` /
#: ``QUARANTINED`` / ``MISSING`` / ``CORRUPT`` must not be frozen.
_BINDABLE_STORAGE_STATE = "AVAILABLE"


def _utcnow_iso() -> str:
    """Return current UTC time as ISO-8601 string (test-overridable)."""
    return datetime.now(timezone.utc).isoformat()


class FrozenInputService:
    """Capture and validate the frozen input binding for a Mining Run.

    Construct-injected with the three ports it needs; the service itself is
    stateless and safe to share across requests.
    """

    __slots__ = ("_documents", "_storage_objects", "_object_store")

    def __init__(
        self,
        documents: DocumentCurrentContentRepository,
        storage_objects: StorageObjectRepository,
        object_store: ObjectStorePort,
    ) -> None:
        self._documents = documents
        self._storage_objects = storage_objects
        self._object_store = object_store

    # -- freeze ------------------------------------------------------------

    async def freeze(self, document_id: str) -> FrozenInput:
        """Snapshot the document's current content binding (SRS §3.2).

        Steps:
        1. Read the document's current-content pointer. Missing document →
           ``StorageObjectMissing`` (re-using the storage error so the
           existing 404/409 mapping applies).
        2. Resolve the storage object record. Missing or non-AVAILABLE →
           ``StorageObjectMissing`` (the object is not bindable; SRS §9.0B).
        3. Build and return the immutable ``FrozenInput`` including the
           resolved location so the reader can skip a second repo lookup.
        """
        current = await self._documents.get(document_id)
        if current is None:
            raise StorageObjectMissing(
                document_id,
                f"document {document_id!r} has no current content to freeze",
            )

        record = await self._storage_objects.get(current.storage_object_id)
        if record is None:
            raise StorageObjectMissing(
                current.storage_object_id,
                f"storage object {current.storage_object_id!r} not registered",
            )
        if record.state != _BINDABLE_STORAGE_STATE:
            raise StorageObjectMissing(
                record.id,
                f"storage object {record.id!r} state is {record.state!r}, "
                f"only {_BINDABLE_STORAGE_STATE!r} may be frozen",
            )

        return self._build_frozen(current, record, _utcnow_iso())

    @staticmethod
    def _build_frozen(
        current: DocumentCurrentContent,
        record: StorageObjectRecord,
        captured_at: str,
    ) -> FrozenInput:
        """Assemble the ``FrozenInput`` from the resolved records."""
        return FrozenInput(
            document_id=current.document_id,
            source_storage_object_id=current.storage_object_id,
            source_raw_hash=current.source_raw_hash,
            source_content_revision=current.content_revision,
            mime=record.mime or "application/octet-stream",
            size=record.size,
            original_filename="",  # not carried on the storage-object record
            captured_at=captured_at,
            provider=record.provider,
            bucket=record.bucket,
            object_key=record.object_key,
            object_version_id=record.object_version_id,
        )

    # -- staleness ---------------------------------------------------------

    async def check_stale(self, frozen: FrozenInput) -> None:
        """Raise ``FrozenInputStale`` if the document moved past the freeze.

        Called by the parse-commit path (SRS §3.2 / §9.5). If the live
        ``content_revision`` differs from ``frozen.source_content_revision``,
        a concurrent edit advanced the document and the produced Snapshot
        MUST NOT auto-promote — the caller marks it stale and re-queues.
        """
        current = await self._documents.get(frozen.document_id)
        if current is None:
            # Document was deleted between freeze and commit. Treat as stale:
            # the input no longer exists at the recorded revision.
            raise FrozenInputStale(
                document_id=frozen.document_id,
                frozen_revision=frozen.source_content_revision,
                current_revision=-1,
                message=(
                    f"document {frozen.document_id!r} disappeared "
                    f"after freeze (was revision {frozen.source_content_revision})"
                ),
            )
        if current.content_revision != frozen.source_content_revision:
            raise FrozenInputStale(
                document_id=frozen.document_id,
                frozen_revision=frozen.source_content_revision,
                current_revision=current.content_revision,
            )


__all__ = ["FrozenInputService"]
