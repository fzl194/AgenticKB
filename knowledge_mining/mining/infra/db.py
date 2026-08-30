"""Dual-database adapter for Mining v3.0 — PostgreSQL backend.

Provides two independent adapters:
- AssetCoreDB — reads/writes asset_core tables (documents, snapshots, segments, retrieval units, builds, releases)
- MiningRuntimeDB — reads/writes mining_runtime tables (runs, run_documents, stage_events)

Both adapters use psycopg[pool] ConnectionPool for connection management.
Public method signatures are identical to the SQLite version.
"""
from __future__ import annotations

import contextvars
import json
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..contracts.models import (
    MiningRunData,
    MiningRunDocumentData,
    StageEvent,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # knowledge_mining/mining/infra/ -> CoreMasterKB/


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _json_dumps(obj: Any) -> str:
    if obj is None:
        return "{}"
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _json_loads(raw: str | None) -> Any:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Base helper — PostgreSQL with ConnectionPool
# ---------------------------------------------------------------------------

def _retry_on_op_error(max_retries: int = 3, delay: float = 0.5):
    """Decorator: retry on psycopg OperationalError (transient connection issues)."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return fn(*args, **kwargs)
                except psycopg.OperationalError:
                    if attempt < max_retries - 1:
                        logger.warning("OperationalError in %s, retry %d/%d", fn.__name__, attempt + 1, max_retries)
                        time.sleep(delay * (attempt + 1))
                    else:
                        raise
        return wrapper
    return decorator


class _DB:
    """PostgreSQL database adapter using ConnectionPool."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool
        # Per-instance transaction connection slot. Each adapter (asset/runtime)
        # gets its own ContextVar so an open transaction on one DB never leaks
        # onto the other. None when no transaction is active in this context.
        self._tx_conn: contextvars.ContextVar = contextvars.ContextVar(
            f"tx_conn_{id(self)}", default=None
        )

    @classmethod
    def from_conninfo(cls, conninfo: str, *, pool_min: int = 2, pool_max: int = 10) -> "_DB":
        """Create adapter from a connection string."""
        pool = ConnectionPool(
            conninfo,
            min_size=pool_min,
            max_size=pool_max,
            open=False,
            # Validate connections before handing them out so stale/closed
            # connections (remote PG idle-timeout or restart) are discarded
            # and replaced transparently instead of raising OperationalError.
            check=ConnectionPool.check_connection,
            max_idle=300.0,
            kwargs={"row_factory": dict_row},
        )
        return cls(pool)

    def open(self) -> None:
        """Open the connection pool."""
        self._pool.open()

    def close(self) -> None:
        """Close the connection pool."""
        self._pool.close()

    @property
    def pool(self) -> ConnectionPool:
        return self._pool

    def _get_conn(self) -> psycopg.Connection:
        return self._pool.getconn()

    def _put_conn(self, conn: psycopg.Connection) -> None:
        self._pool.putconn(conn)

    # -- transaction support --

    @contextmanager
    def transaction(self):
        """Run a block of statements atomically on a single pooled connection.

        While active, every ``_execute`` / ``_fetchone`` / ``_fetchall`` and the
        ``delete_*`` helpers route to this one connection (so reads see the
        block's own uncommitted writes). On normal exit the whole block is
        committed; on any exception it is rolled back. Nested calls reuse the
        outer transaction (no inner BEGIN/COMMIT).
        """
        if self._tx_conn.get() is not None:
            # Already inside a transaction — reuse it.
            yield
            return
        with self._pool.connection() as conn:
            # psycopg connections default to autocommit=False; the pooled
            # context manager commits on clean exit and rolls back on error.
            # Guard against a misconfigured pool that would silently break atomicity.
            if conn.autocommit:
                raise RuntimeError(
                    "transaction() requires a non-autocommit connection; "
                    "the pool must not enable autocommit"
                )
            token = self._tx_conn.set(conn)
            try:
                yield
            finally:
                self._tx_conn.reset(token)

    @contextmanager
    def join_transaction(self, owner: "_DB"):
        """Route this adapter through another adapter's active transaction.

        Asset, graph, ontology, and runtime adapters may be distinct objects but
        still target the same Domain pool.  Explicit joining keeps their writes
        on one connection without ever leaking a Control-store transaction into
        a Domain adapter.
        """
        if self._pool is not owner._pool:
            raise ValueError("transaction participants must use the same pool")
        connection = owner._tx_conn.get()
        if connection is None:
            raise RuntimeError("transaction owner has no active transaction")
        current = self._tx_conn.get()
        if current is connection:
            yield
            return
        if current is not None:
            raise RuntimeError("adapter is already bound to another transaction")
        token = self._tx_conn.set(connection)
        try:
            yield
        finally:
            self._tx_conn.reset(token)

    # -- helpers --

    def _run(self, sql: str, params: tuple, *, fetch: str | None):
        """Dispatch a statement to the active transaction connection if one is
        open in this context, otherwise to a fresh pooled connection (with retry).

        ``fetch``: None (no result), 'one', 'all', or 'rowcount'.
        """
        tx_conn = self._tx_conn.get()
        if tx_conn is not None:
            # Inside a transaction: share the connection, do NOT commit here and
            # do NOT retry (a failed statement aborts the whole transaction).
            with tx_conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
                if fetch == "rowcount":
                    return cur.rowcount
                return None
        return self._run_pooled(sql, params, fetch=fetch)

    @_retry_on_op_error()
    def _run_pooled(self, sql: str, params: tuple, *, fetch: str | None):
        """Non-transactional path: each call gets its own connection and
        auto-commits on context exit. Retried on transient connection errors."""
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
                if fetch == "rowcount":
                    return cur.rowcount
                return None

    def _execute(self, sql: str, params: tuple = ()) -> None:
        self._run(sql, params, fetch=None)

    def _fetchone(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        return self._run(sql, params, fetch="one")

    def _fetchall(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        return self._run(sql, params, fetch="all")

    def commit(self) -> None:
        """No-op: non-transactional statements auto-commit; transaction() commits
        on block exit. Kept for backward compatibility with existing call sites."""


# ===================================================================
# AssetCoreDB
# ===================================================================

class AssetCoreDB(_DB):

    def __init__(self, pool: ConnectionPool) -> None:
        super().__init__(pool)
        self._domain_publish_locks: contextvars.ContextVar = contextvars.ContextVar(
            f"domain_publish_locks_{id(self)}", default=frozenset()
        )

    @contextmanager
    def transaction(self):
        """Reset the acquired publish-lock set for each outer transaction."""
        outer = self._tx_conn.get() is None
        token = (
            self._domain_publish_locks.set(frozenset())
            if outer
            else None
        )
        try:
            with super().transaction():
                yield
        finally:
            if token is not None:
                self._domain_publish_locks.reset(token)

    def acquire_domain_publish_lock(self, domain: str) -> None:
        """Serialize active-release changes for one domain in this transaction."""
        if self._tx_conn.get() is None:
            raise RuntimeError(
                "acquire_domain_publish_lock() must be called inside transaction()"
            )
        held_domains = self._domain_publish_locks.get()
        if domain in held_domains:
            return
        self._execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"asset-publish:{domain}",),
        )
        self._domain_publish_locks.set(held_domains | {domain})

    """Adapter for asset_core tables — Mining writes content assets here."""

    # -- source batches --

    def upsert_source_batch(
        self,
        *,
        domain: str,
        batch_id: str,
        batch_code: str,
        source_type: str,
        description: str | None = None,
        created_by: str | None = None,
        metadata_json: dict | None = None,
    ) -> str:
        now = _utcnow()
        self._execute(
            """INSERT INTO asset_source_batches (id, batch_code, source_type, domain, description, created_by, created_at, metadata_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT(id) DO UPDATE SET
                   batch_code=excluded.batch_code,
                   source_type=excluded.source_type,
                   description=excluded.description,
                   created_by=excluded.created_by,
                   metadata_json=excluded.metadata_json
               WHERE asset_source_batches.domain = excluded.domain""",
            (batch_id, batch_code, source_type, domain, description, created_by, now, _json_dumps(metadata_json)),
        )
        row = self._fetchone(
            "SELECT id FROM asset_source_batches WHERE id = %s AND domain = %s",
            (batch_id, domain),
        )
        if row is None:
            raise ValueError("domain_mismatch")
        return batch_id

    def get_source_batch(self, *, domain: str, batch_id: str) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT * FROM asset_source_batches WHERE id = %s AND domain = %s",
            (batch_id, domain),
        )

    def find_batch_by_code(self, *, domain: str, batch_code: str) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT * FROM asset_source_batches WHERE batch_code = %s AND domain = %s",
            (batch_code, domain),
        )

    # -- documents --

    def upsert_document(
        self,
        *,
        domain: str,
        document_id: str,
        document_key: str,
        document_name: str | None = None,
        document_type: str | None = None,
        metadata_json: dict | None = None,
    ) -> str:
        """Legacy 身份 upsert（仅 /api/runs 走，KB 路径不调）。

        G1 后 (domain, document_key) 唯一约束已让位于 (kb_id, document_key)，不能再
        用 ON CONFLICT(domain, document_key)。改为先查后插。
        H7：查同 (domain, document_key) 的任意已有行——优先 KB 拥有的（kb_id 非空），
        其次 legacy（kb_id NULL）。命中 KB 行则复用其 id 但不改元数据（KB 是真相源，
        legacy 不覆盖）；命中 legacy 行才更新；都没有才插 kb_id NULL 新行。避免为同一
        物理文件新建 NULL 分叉行（(kb_id, document_key) 唯一约束能容许多 NULL）。
        """
        existing = self._fetchone(
            """SELECT id, kb_id FROM asset_documents
               WHERE domain = %s AND document_key = %s
               ORDER BY kb_id NULLS LAST
               LIMIT 1""",
            (domain, document_key),
        )
        if existing:
            if existing["kb_id"] is None:
                self._execute(
                    """UPDATE asset_documents
                       SET document_name = COALESCE(%s, document_name),
                           document_type = COALESCE(%s, document_type),
                           metadata_json = %s
                       WHERE id = %s""",
                    (document_name, document_type, _json_dumps(metadata_json), existing["id"]),
                )
            return existing["id"]
        now = _utcnow()
        self._execute(
            """INSERT INTO asset_documents
                   (id, domain, document_key, document_name, document_type, metadata_json, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                document_id, domain, document_key, document_name, document_type,
                _json_dumps(metadata_json), now,
            ),
        )
        return document_id

    def get_document_by_key(self, *, domain: str, document_key: str) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT * FROM asset_documents WHERE domain = %s AND document_key = %s",
            (domain, document_key),
        )

    def get_document_by_storage_path(
        self, *, domain: str, storage_path: str
    ) -> dict[str, Any] | None:
        """按落盘绝对路径查文档身份。

        storage_path 含 ``<kb_id>`` 前缀 → 全库唯一，天然按 KB 隔离，消解
        「多库同 document_key」歧义（设计：身份/位置分离，挖掘 walk 时按位置找身份）。
        legacy 文档 storage_path 为 NULL，不会被命中（按 D2 不回落 document_key）。
        """
        return self._fetchone(
            "SELECT * FROM asset_documents WHERE domain = %s AND storage_path = %s",
            (domain, storage_path),
        )

    def get_document(self, *, domain: str, document_id: str) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT * FROM asset_documents WHERE id = %s AND domain = %s",
            (document_id, domain),
        )

    def get_document_storage_paths_by_ids(
        self, *, domain: str, document_ids: list[str]
    ) -> list[str]:
        """按文档 id 批量取 storage_path。

        选择性挖掘用：把前端勾选的 document id 翻成落盘绝对路径，在
        ``_prepare_document_states`` 里按位置过滤 ``ingest_directory`` 的扫描结果。
        只返回本域、storage_path 非空的行（legacy 行 storage_path 为 NULL，不参与）。
        """
        if not document_ids:
            return []
        rows = self._fetchall(
            "SELECT storage_path FROM asset_documents "
            "WHERE id = ANY(%s) AND domain = %s AND storage_path IS NOT NULL",
            (list(document_ids), domain),
        )
        return [r["storage_path"] for r in rows]

    def get_document_lifecycle_state(
        self,
        *,
        domain: str,
        channel: str,
        storage_path: str,
        normalized_content_hash: str,
    ) -> dict[str, Any] | None:
        """Return domain-local history and the published active selection.

        按 ``storage_path``（位置）定位身份，而非 ``document_key``。这样文件移动/改名
        后（位置变、document_key 冻结不变）仍能找到同一身份，挖掘历史不断链；且
        storage_path 全库唯一，消解多库同 key 歧义。返回的 ``document_key`` 是冻结键，
        供调用方写入 ``mining_run_documents``。legacy 文档（storage_path NULL）不会被命中。

        The active release is the only source of current truth.  A newer build
        or link that has not been published must not affect classification.
        """
        return self._fetchone(
            """SELECT documents.id AS document_id,
                      documents.domain AS document_domain,
                      documents.document_key,
                      history.historical_snapshot_id,
                      history.historical_snapshot_hash,
                      history.historical_link_id,
                      history.historical_source_batch_id,
                      COALESCE(history.historical_snapshot_complete, FALSE)
                          AS historical_snapshot_complete,
                      active.active_release_id,
                      active.active_build_id,
                      active.active_snapshot_id,
                      active.active_snapshot_hash,
                      active.active_source_batch_id,
                      COALESCE(active.active_snapshot_complete, FALSE)
                          AS active_snapshot_complete
               FROM asset_documents AS documents
               LEFT JOIN LATERAL (
                   SELECT historical_snapshots.id AS historical_snapshot_id,
                          historical_snapshots.normalized_content_hash
                              AS historical_snapshot_hash,
                          historical_links.id AS historical_link_id,
                          historical_batches.id AS historical_source_batch_id,
                          EXISTS (
                              SELECT 1
                              FROM asset_raw_segments AS historical_segments
                              WHERE historical_segments.document_snapshot_id =
                                    historical_snapshots.id
                          ) AS historical_snapshot_complete
                   FROM asset_document_snapshot_links AS historical_links
                   JOIN asset_document_snapshots AS historical_snapshots
                     ON historical_snapshots.id = historical_links.document_snapshot_id
                    AND historical_snapshots.domain = %s
                   LEFT JOIN asset_source_batches AS historical_batches
                     ON historical_batches.id = historical_links.source_batch_id
                    AND historical_batches.domain = %s
                   WHERE historical_links.document_id = documents.id
                     AND historical_snapshots.normalized_content_hash = %s
                     AND (
                         historical_links.source_batch_id IS NULL
                         OR historical_batches.id IS NOT NULL
                     )
                   ORDER BY historical_links.linked_at DESC, historical_links.id DESC
                   LIMIT 1
               ) AS history ON TRUE
               LEFT JOIN LATERAL (
                   SELECT releases.id AS active_release_id,
                          builds.id AS active_build_id,
                          active_snapshots.id AS active_snapshot_id,
                          active_snapshots.normalized_content_hash AS active_snapshot_hash,
                          active_batches.id AS active_source_batch_id,
                          EXISTS (
                              SELECT 1
                              FROM asset_raw_segments AS active_segments
                              WHERE active_segments.document_snapshot_id = active_snapshots.id
                          ) AS active_snapshot_complete
                   FROM asset_publish_releases AS releases
                   JOIN asset_builds AS builds
                     ON builds.id = releases.build_id
                    AND builds.domain = %s
                   JOIN asset_build_document_snapshots AS selections
                     ON selections.build_id = builds.id
                    AND selections.document_id = documents.id
                    AND selections.selection_status = 'active'
                   JOIN asset_document_snapshots AS active_snapshots
                     ON active_snapshots.id = selections.document_snapshot_id
                    AND active_snapshots.domain = %s
                   LEFT JOIN asset_source_batches AS active_batches
                     ON active_batches.id = selections.source_batch_id
                    AND active_batches.domain = %s
                   WHERE releases.domain = %s
                     AND releases.channel = %s
                     AND releases.status = 'active'
                     AND (
                         selections.source_batch_id IS NULL
                         OR active_batches.id IS NOT NULL
                     )
                   LIMIT 1
               ) AS active ON TRUE
               WHERE documents.domain = %s
                 AND documents.storage_path = %s""",
            (
                domain,
                domain,
                normalized_content_hash,
                domain,
                domain,
                domain,
                domain,
                channel,
                domain,
                storage_path,
            ),
        )

    # -- snapshots --

    def upsert_snapshot(
        self,
        *,
        domain: str,
        snapshot_id: str,
        normalized_content_hash: str,
        raw_content_hash: str,
        mime_type: str,
        title: str | None = None,
        scope_json: dict | None = None,
        tags_json: list | None = None,
        parser_profile_json: dict | None = None,
        metadata_json: dict | None = None,
        workflow_id: str | None = None,
        workflow_version: int | None = None,
        workflow_version_id: str | None = None,
        workflow_graph_hash: str | None = None,
    ) -> str:
        workflow_values = (
            workflow_id, workflow_version, workflow_version_id, workflow_graph_hash,
        )
        if any(value is not None for value in workflow_values) and not all(
            value is not None for value in workflow_values
        ):
            raise ValueError("Snapshot Workflow binding must be complete")
        now = _utcnow()
        self._execute(
            """INSERT INTO asset_document_snapshots
                   (id, domain, normalized_content_hash, raw_content_hash, mime_type, title,
                     scope_json, tags_json, parser_profile_json, metadata_json, created_at,
                     workflow_id, workflow_version, workflow_version_id, workflow_graph_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING""",
            (
                snapshot_id, domain, normalized_content_hash, raw_content_hash, mime_type, title,
                _json_dumps(scope_json), _json_dumps(tags_json),
                _json_dumps(parser_profile_json), _json_dumps(metadata_json), now,
                workflow_id, workflow_version, workflow_version_id, workflow_graph_hash,
            ),
        )
        row = self.get_snapshot_by_hash(
            domain=domain,
            normalized_content_hash=normalized_content_hash,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            workflow_graph_hash=workflow_graph_hash,
        )
        return row["id"] if row else snapshot_id

    def get_snapshot_by_hash(
        self,
        *,
        domain: str,
        normalized_content_hash: str,
        workflow_id: str | None = None,
        workflow_version: int | None = None,
        workflow_graph_hash: str | None = None,
    ) -> dict[str, Any] | None:
        if workflow_graph_hash is None:
            return self._fetchone(
                "SELECT * FROM asset_document_snapshots "
                "WHERE domain = %s AND normalized_content_hash = %s "
                "AND workflow_graph_hash IS NULL",
                (domain, normalized_content_hash),
            )
        return self._fetchone(
            "SELECT * FROM asset_document_snapshots "
            "WHERE domain = %s AND normalized_content_hash = %s "
            "AND workflow_id = %s AND workflow_version = %s "
            "AND workflow_graph_hash = %s",
            (
                domain, normalized_content_hash, workflow_id,
                workflow_version, workflow_graph_hash,
            ),
        )

    def get_snapshot(self, *, domain: str, snapshot_id: str) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT * FROM asset_document_snapshots WHERE id = %s AND domain = %s",
            (snapshot_id, domain),
        )

    # -- snapshot links --

    def insert_snapshot_link(
        self,
        *,
        domain: str,
        link_id: str,
        document_id: str,
        document_snapshot_id: str,
        source_batch_id: str | None,
        relative_path: str,
        source_uri: str,
        title: str | None = None,
        scope_json: dict | None = None,
        tags_json: list | None = None,
        metadata_json: dict | None = None,
    ) -> str:
        now = _utcnow()
        row = self._fetchone(
            """INSERT INTO asset_document_snapshot_links
                   (id, document_id, document_snapshot_id, source_batch_id, relative_path,
                     source_uri, title, scope_json, tags_json, linked_at, metadata_json)
               SELECT %s, documents.id, snapshots.id, batches.id, %s, %s, %s, %s, %s, %s, %s
               FROM asset_documents AS documents
               JOIN asset_document_snapshots AS snapshots
                 ON snapshots.id = %s AND snapshots.domain = %s
               LEFT JOIN asset_source_batches AS batches
                 ON batches.id = %s AND batches.domain = %s
               WHERE documents.id = %s
                 AND documents.domain = %s
                 AND (%s OR batches.id IS NOT NULL)
               RETURNING id""",
            (
                link_id, relative_path, source_uri, title, _json_dumps(scope_json),
                _json_dumps(tags_json), now, _json_dumps(metadata_json),
                document_snapshot_id, domain, source_batch_id, domain,
                document_id, domain, source_batch_id is None,
            ),
        )
        if row is None:
            raise ValueError("domain_mismatch")
        return link_id

    def get_active_link(self, *, domain: str, document_id: str) -> dict[str, Any] | None:
        return self._fetchone(
            """SELECT links.*, snapshots.normalized_content_hash
               FROM asset_document_snapshot_links AS links
               JOIN asset_documents AS documents
                 ON documents.id = links.document_id AND documents.domain = %s
               JOIN asset_document_snapshots AS snapshots
                 ON snapshots.id = links.document_snapshot_id AND snapshots.domain = %s
               LEFT JOIN asset_source_batches AS batches
                 ON batches.id = links.source_batch_id
               WHERE links.document_id = %s
                 AND (links.source_batch_id IS NULL OR batches.domain = %s)
               ORDER BY links.linked_at DESC
               LIMIT 1""",
            (domain, domain, document_id, domain),
        )

    def get_links_by_snapshot(self, *, domain: str, snapshot_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            """SELECT links.*
               FROM asset_document_snapshot_links AS links
               JOIN asset_documents AS documents
                 ON documents.id = links.document_id AND documents.domain = %s
               JOIN asset_document_snapshots AS snapshots
                 ON snapshots.id = links.document_snapshot_id AND snapshots.domain = %s
               LEFT JOIN asset_source_batches AS batches
                 ON batches.id = links.source_batch_id
               WHERE links.document_snapshot_id = %s
                 AND (links.source_batch_id IS NULL OR batches.domain = %s)
               ORDER BY links.linked_at DESC""",
            (domain, domain, snapshot_id, domain),
        )

    # -- raw segments --

    def insert_raw_segment(
        self,
        segment_id: str,
        document_snapshot_id: str,
        segment_key: str,
        segment_index: int,
        block_type: str = "unknown",
        semantic_role: str = "unknown",
        section_path: str | list | None = None,
        section_title: str | None = None,
        raw_text: str = "",
        normalized_text: str = "",
        content_hash: str = "",
        normalized_hash: str = "",
        token_count: int | None = None,
        structure_json: dict | None = None,
        source_offsets_json: dict | None = None,
        entity_refs_json: list | None = None,
        metadata_json: dict | None = None,
    ) -> str:
        sp = section_path if isinstance(section_path, str) else _json_dumps(section_path)
        self._execute(
            """INSERT INTO asset_raw_segments
                   (id, document_snapshot_id, segment_key, segment_index, block_type, semantic_role,
                    section_path, section_title, raw_text, normalized_text, content_hash, normalized_hash,
                    token_count, structure_json, source_offsets_json, entity_refs_json, metadata_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                segment_id, document_snapshot_id, segment_key, segment_index, block_type, semantic_role,
                sp, section_title, raw_text, normalized_text, content_hash, normalized_hash,
                token_count, _json_dumps(structure_json), _json_dumps(source_offsets_json),
                _json_dumps(entity_refs_json), _json_dumps(metadata_json),
            ),
        )
        return segment_id

    def delete_segments_by_snapshot(self, document_snapshot_id: str) -> int:
        return self._run(
            "DELETE FROM asset_raw_segments WHERE document_snapshot_id = %s",
            (document_snapshot_id,),
            fetch="rowcount",
        )

    def get_segments_by_snapshot(self, document_snapshot_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM asset_raw_segments WHERE document_snapshot_id = %s ORDER BY segment_index",
            (document_snapshot_id,),
        )

    def count_segments_by_snapshot(self, document_snapshot_id: str) -> int:
        row = self._fetchone(
            "SELECT COUNT(*) as cnt FROM asset_raw_segments WHERE document_snapshot_id = %s",
            (document_snapshot_id,),
        )
        return row["cnt"] if row else 0

    # -- segment relations --

    def insert_segment_relation(
        self,
        relation_id: str,
        document_snapshot_id: str,
        source_segment_id: str,
        target_segment_id: str,
        relation_type: str,
        weight: float = 1.0,
        confidence: float = 1.0,
        distance: int | None = None,
        metadata_json: dict | None = None,
    ) -> str:
        self._execute(
            """INSERT INTO asset_raw_segment_relations
                   (id, document_snapshot_id, source_segment_id, target_segment_id,
                    relation_type, weight, confidence, distance, metadata_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (source_segment_id, target_segment_id, relation_type)
               DO UPDATE SET
                   weight = EXCLUDED.weight,
                   confidence = EXCLUDED.confidence,
                   distance = EXCLUDED.distance,
                   metadata_json = EXCLUDED.metadata_json,
                   document_snapshot_id = EXCLUDED.document_snapshot_id""",
            (
                relation_id, document_snapshot_id, source_segment_id, target_segment_id,
                relation_type, weight, confidence, distance, _json_dumps(metadata_json),
            ),
        )
        return relation_id

    def delete_relations_by_snapshot(self, document_snapshot_id: str) -> int:
        return self._run(
            "DELETE FROM asset_raw_segment_relations WHERE document_snapshot_id = %s",
            (document_snapshot_id,),
            fetch="rowcount",
        )

    def get_relations_by_snapshot(self, document_snapshot_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM asset_raw_segment_relations WHERE document_snapshot_id = %s",
            (document_snapshot_id,),
        )

    # -- retrieval units --

    def insert_retrieval_unit(
        self,
        unit_id: str,
        document_snapshot_id: str,
        unit_key: str,
        unit_type: str,
        target_type: str,
        target_ref_json: dict | None = None,
        title: str | None = None,
        text: str = "",
        search_text: str = "",
        block_type: str = "unknown",
        semantic_role: str = "unknown",
        facets_json: dict | None = None,
        entity_refs_json: list | None = None,
        source_refs_json: dict | None = None,
        llm_result_refs_json: dict | None = None,
        source_segment_id: str | None = None,
        weight: float = 1.0,
        metadata_json: dict | None = None,
    ) -> str:
        now = _utcnow()
        self._execute(
            """INSERT INTO asset_retrieval_units
                   (id, document_snapshot_id, unit_key, unit_type, target_type, target_ref_json,
                    title, text, search_text, block_type, semantic_role,
                    facets_json, entity_refs_json, source_refs_json, llm_result_refs_json,
                    source_segment_id, weight, created_at, metadata_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                unit_id, document_snapshot_id, unit_key, unit_type, target_type,
                _json_dumps(target_ref_json), title, text, search_text, block_type, semantic_role,
                _json_dumps(facets_json), _json_dumps(entity_refs_json),
                _json_dumps(source_refs_json), _json_dumps(llm_result_refs_json),
                source_segment_id, weight, now, _json_dumps(metadata_json),
            ),
        )
        return unit_id

    def delete_retrieval_units_by_snapshot(self, document_snapshot_id: str) -> int:
        return self._run(
            "DELETE FROM asset_retrieval_units WHERE document_snapshot_id = %s",
            (document_snapshot_id,),
            fetch="rowcount",
        )

    def clear_snapshot_derived_assets(self, document_snapshot_id: str) -> None:
        """清空一个 snapshot 的全部派生资产，保留 snapshot 行本身与 document 身份。

        用于 force_redo 重跑前清理：persist_document_assets 见 snapshot 已有切片就会跳过持久化、
        且按 unit_key upsert 不会删除不再生成的旧单元（如 table_row）。先整体清空，重跑才干净。
        依赖：asset_retrieval_embeddings 由 retrieval_unit_id ON DELETE CASCADE 随单元删；
        asset_segment_entity_mentions 由 segment_id ON DELETE CASCADE 随切片删。
        """
        self._execute(
            "DELETE FROM asset_retrieval_units WHERE document_snapshot_id = %s",
            (document_snapshot_id,),
        )
        self._execute(
            "DELETE FROM asset_raw_segment_relations WHERE document_snapshot_id = %s",
            (document_snapshot_id,),
        )
        self._execute(
            "DELETE FROM asset_raw_segments WHERE document_snapshot_id = %s",
            (document_snapshot_id,),
        )

    def get_retrieval_units_by_snapshot(self, document_snapshot_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM asset_retrieval_units WHERE document_snapshot_id = %s",
            (document_snapshot_id,),
        )

    def count_retrieval_units_by_snapshot(self, document_snapshot_id: str) -> int:
        # 批次8 M5：优先 v2 表示；存量旧表兜底（clean break 后应为 0）。
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM asset_retrieval_units_v2 "
            "WHERE snapshot_id = %s",
            (document_snapshot_id,),
        )
        if row and int(row["n"]) > 0:
            return int(row["n"])
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM asset_retrieval_units "
            "WHERE document_snapshot_id = %s",
            (document_snapshot_id,),
        )
        return int(row["n"]) if row else 0

    def fetch_snapshot_readiness(
        self, snapshot_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """27号审查修复 B：批量取快照冻结 readiness（无行 = 缺失，不视为 ready）.

        供 finalize 发布门禁与 build 摘要聚合；readiness_json 由 psycopg
        反序列化为 dict（防御 str 形态）。
        """
        if not snapshot_ids:
            return {}
        rows = self._fetchall(
            "SELECT snapshot_id, readiness_json FROM asset_snapshot_readiness "
            "WHERE snapshot_id = ANY(%s)",
            (snapshot_ids,),
        )
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            facts = row.get("readiness_json")
            if isinstance(facts, str):
                try:
                    facts = json.loads(facts)
                except (json.JSONDecodeError, TypeError):
                    facts = None
            if isinstance(facts, dict):
                out[str(row["snapshot_id"])] = facts
        return out

    def count_embeddings_by_snapshot(self, document_snapshot_id: str) -> int:
        # 批次8 M5：优先 v2 向量资产；存量旧表兜底（clean break 后应为 0）。
        row = self._fetchone(
            """SELECT COUNT(*) AS n FROM asset_retrieval_embeddings_v2
               WHERE snapshot_id = %s""",
            (document_snapshot_id,),
        )
        if row and int(row["n"]) > 0:
            return int(row["n"])
        row = self._fetchone(
            """SELECT COUNT(*) AS n FROM asset_retrieval_embeddings e
               JOIN asset_retrieval_units u ON u.id = e.retrieval_unit_id
               WHERE u.document_snapshot_id = %s""",
            (document_snapshot_id,),
        )
        return int(row["n"]) if row else 0

    # -- retrieval embeddings --

    def insert_retrieval_embedding(
        self,
        embedding_id: str,
        retrieval_unit_id: str,
        embedding_model: str,
        embedding_provider: str,
        text_kind: str,
        embedding_dim: int,
        embedding_vector: str,
        content_hash: str = "",
        metadata_json: dict | None = None,
    ) -> str:
        now = _utcnow()
        self._execute(
            """INSERT INTO asset_retrieval_embeddings
                   (id, retrieval_unit_id, embedding_model, embedding_provider,
                    text_kind, embedding_dim, embedding_vector, content_hash,
                    created_at, metadata_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                embedding_id, retrieval_unit_id, embedding_model, embedding_provider,
                text_kind, embedding_dim, embedding_vector, content_hash,
                now, _json_dumps(metadata_json),
            ),
        )
        return embedding_id

    # -- builds --

    def insert_build(
        self,
        build_id: str,
        build_code: str,
        status: str = "building",
        build_mode: str = "full",
        domain: str | None = None,
        source_batch_id: str | None = None,
        parent_build_id: str | None = None,
        mining_run_id: str | None = None,
        summary_json: dict | None = None,
        validation_json: dict | None = None,
        kb_id: str | None = None,
    ) -> str:
        now = _utcnow()
        self._execute(
            """INSERT INTO asset_builds
                   (id, build_code, status, build_mode, domain, source_batch_id, parent_build_id,
                    mining_run_id, summary_json, validation_json, kb_id, created_at, finished_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)""",
            (
                build_id, build_code, status, build_mode, domain, source_batch_id, parent_build_id,
                mining_run_id, _json_dumps(summary_json), _json_dumps(validation_json),
                kb_id, now,
            ),
        )
        return build_id

    def update_build_status(
        self,
        build_id: str,
        status: str,
        finished_at: str | None = None,
        summary_json: dict | None = None,
        validation_json: dict | None = None,
    ) -> None:
        fa = finished_at or _utcnow()
        self._execute(
            """UPDATE asset_builds SET status = %s, finished_at = %s,
               summary_json = COALESCE(%s, summary_json),
               validation_json = COALESCE(%s, validation_json)
               WHERE id = %s""",
            (
                status,
                fa,
                _json_dumps(summary_json) if summary_json is not None else None,
                _json_dumps(validation_json) if validation_json is not None else None,
                build_id,
            ),
        )

    def get_build(self, build_id: str) -> dict[str, Any] | None:
        return self._fetchone("SELECT * FROM asset_builds WHERE id = %s", (build_id,))

    def get_active_build(self, *, domain: str, channel: str) -> dict[str, Any] | None:
        return self._fetchone(
            """SELECT builds.*
               FROM asset_publish_releases AS releases
               JOIN asset_builds AS builds
                 ON builds.id = releases.build_id
                AND builds.domain = releases.domain
               WHERE releases.domain = %s
                 AND builds.domain = %s
                 AND releases.channel = %s
                 AND releases.status = 'active'
               LIMIT 1""",
            (domain, domain, channel),
        )

    def get_active_document_ids_by_batch(
        self,
        *,
        domain: str,
        channel: str,
        source_batch_id: str,
    ) -> list[str]:
        """Return active documents whose current selection came from a batch."""
        rows = self._fetchall(
            """SELECT selections.document_id
               FROM asset_publish_releases AS releases
               JOIN asset_builds AS builds
                 ON builds.id = releases.build_id
                AND builds.domain = releases.domain
               JOIN asset_build_document_snapshots AS selections
                 ON selections.build_id = builds.id
                AND selections.selection_status = 'active'
               JOIN asset_documents AS documents
                 ON documents.id = selections.document_id
                AND documents.domain = builds.domain
               JOIN asset_document_snapshots AS snapshots
                 ON snapshots.id = selections.document_snapshot_id
                AND snapshots.domain = builds.domain
               JOIN asset_source_batches AS batches
                 ON batches.id = selections.source_batch_id
                AND batches.domain = builds.domain
               WHERE releases.domain = %s
                 AND builds.domain = %s
                 AND releases.channel = %s
                 AND releases.status = 'active'
                 AND batches.id = %s""",
            (domain, domain, channel, source_batch_id),
        )
        return [row["document_id"] for row in rows]

    # -- build document snapshots --

    def upsert_build_document_snapshot(
        self,
        *,
        build_id: str,
        document_id: str,
        document_snapshot_id: str,
        source_batch_id: str | None,
        selection_status: str = "active",
        reason: str = "add",
        metadata_json: dict | None = None,
    ) -> None:
        row = self._fetchone(
            """INSERT INTO asset_build_document_snapshots
                   (build_id, document_id, document_snapshot_id, source_batch_id,
                    selection_status, reason, metadata_json)
               SELECT builds.id, documents.id, snapshots.id, batches.id, %s, %s, %s
               FROM asset_builds AS builds
               JOIN asset_documents AS documents
                 ON documents.id = %s
                AND documents.domain = builds.domain
               JOIN asset_document_snapshots AS snapshots
                 ON snapshots.id = %s
                AND snapshots.domain = builds.domain
               LEFT JOIN asset_source_batches AS batches
                 ON batches.id = %s
                AND batches.domain = builds.domain
               WHERE builds.id = %s
                 AND (%s OR batches.id IS NOT NULL)
               ON CONFLICT(build_id, document_id) DO UPDATE SET
                   document_snapshot_id = excluded.document_snapshot_id,
                   source_batch_id = excluded.source_batch_id,
                   selection_status = excluded.selection_status,
                   reason = excluded.reason,
                   metadata_json = excluded.metadata_json
               RETURNING build_id""",
            (
                selection_status,
                reason,
                _json_dumps(metadata_json),
                document_id,
                document_snapshot_id,
                source_batch_id,
                build_id,
                source_batch_id is None,
            ),
        )
        if row is None:
            raise ValueError("domain_mismatch")

    def get_build_snapshots(self, build_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM asset_build_document_snapshots WHERE build_id = %s",
            (build_id,),
        )

    # -- publish releases --

    def insert_release(
        self,
        release_id: str,
        release_code: str,
        build_id: str,
        domain: str = "default",
        channel: str = "prod",
        status: str = "staging",
        previous_release_id: str | None = None,
        released_by: str | None = None,
        release_notes: str | None = None,
        metadata_json: dict | None = None,
    ) -> str:
        self._execute(
            """INSERT INTO asset_publish_releases
                   (id, release_code, build_id, domain, channel, status, previous_release_id,
                    released_by, release_notes, metadata_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                release_id, release_code, build_id, domain, channel, status, previous_release_id,
                released_by, release_notes, _json_dumps(metadata_json),
            ),
        )
        return release_id

    def activate_release(self, release_id: str) -> None:
        now = _utcnow()
        release = self._fetchone(
            "SELECT domain, channel FROM asset_publish_releases WHERE id = %s", (release_id,)
        )
        if release is None:
            raise ValueError(f"Release {release_id} not found")
        domain = release["domain"]
        channel = release["channel"]
        # Retire previous active release scoped to this domain+channel
        self._execute(
            "UPDATE asset_publish_releases SET status = 'retired', deactivated_at = %s "
            "WHERE domain = %s AND channel = %s AND status = 'active'",
            (now, domain, channel),
        )
        self._execute(
            "UPDATE asset_publish_releases SET status = 'active', activated_at = %s WHERE id = %s",
            (now, release_id),
        )

    def get_active_release(self, domain: str, channel: str = "prod") -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT * FROM asset_publish_releases WHERE domain = %s AND channel = %s AND status = 'active'",
            (domain, channel),
        )

    def get_release(self, release_id: str) -> dict[str, Any] | None:
        return self._fetchone("SELECT * FROM asset_publish_releases WHERE id = %s", (release_id,))


# ===================================================================
# MiningRuntimeDB
# ===================================================================

class MiningRuntimeDB(_DB):
    """Adapter for mining_runtime tables — Mining process-state truth source."""

    # -- mining runs --

    def claim_run(
        self,
        run_id: str,
        domain: str,
        worker_id: str,
        lease_seconds: int = 300,
        allowed_statuses: tuple[str, ...] = ("queued", "running"),
    ) -> bool:
        """Atomically claim an unowned or expired run.

        ``allowed_statuses`` 按执行动作扩展：resume 认领需覆盖
        interrupted/failed/awaiting_review（与 ``resume_running`` 的
        recover_workflow 状态集对齐），execute 保持 queued/running。
        """
        count = self._run(
            "WITH domain_claim AS (SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS locked) "
            "UPDATE mining_runs SET worker_id = %s, heartbeat_at = NOW(), "
            "lease_until = NOW() + (%s * INTERVAL '1 second'), attempt_no = attempt_no + 1 "
            "FROM domain_claim WHERE domain_claim.locked AND id = %s AND domain = %s AND status = ANY(%s) "
            "AND (worker_id IS NULL OR lease_until IS NULL OR lease_until < NOW()) "
            "AND NOT EXISTS (SELECT 1 FROM mining_runs active WHERE active.domain = %s "
            "AND active.id <> %s AND active.status IN ('queued', 'running') "
            "AND active.worker_id IS NOT NULL AND active.lease_until >= NOW())",
            # 参数序必须与 %s 在 SQL 文本中的出现序一致（$n 按出现序绑定）；
            # statuses 必须是 list——psycopg 把 tuple 适配为 record 字面量。
            (domain, worker_id, lease_seconds, run_id, domain, list(allowed_statuses), domain, run_id),
            fetch="rowcount",
        )
        return bool(count)

    def renew_run_lease(self, run_id: str, worker_id: str, lease_seconds: int = 300) -> bool:
        """续租仅以 worker_id 为栅栏：resume 认领后状态仍为 interrupted 的
        窗口期内也必须能续租（否则误判租约丢失）。"""
        count = self._run(
            "UPDATE mining_runs SET heartbeat_at = NOW(), lease_until = NOW() + (%s * INTERVAL '1 second') "
            "WHERE id = %s AND worker_id = %s",
            (lease_seconds, run_id, worker_id), fetch="rowcount",
        )
        return bool(count)

    def release_run_lease(self, run_id: str, worker_id: str) -> bool:
        count = self._run(
            "UPDATE mining_runs SET worker_id = NULL, lease_until = NULL "
            "WHERE id = %s AND worker_id = %s", (run_id, worker_id), fetch="rowcount",
        )
        return bool(count)

    def insert_run(self, data: MiningRunData) -> str:
        self._execute(
            """INSERT INTO mining_runs
                   (id, source_batch_id, input_path, domain, channel, status, current_stage, build_id,
                    total_documents, new_count, updated_count, skipped_count,
                    failed_count, committed_count, started_at, finished_at,
                    error_summary, metadata_json, execution_engine, workflow_id,
                    workflow_version, workflow_version_id, workflow_graph_hash,
                    workflow_manifest_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s)""",
            (
                data.id, data.source_batch_id, data.input_path, data.domain, data.channel,
                data.status, data.current_stage, data.build_id,
                data.total_documents, data.new_count, data.updated_count, data.skipped_count,
                data.failed_count, data.committed_count, data.started_at or _utcnow(),
                data.finished_at, data.error_summary, _json_dumps(data.metadata_json),
                data.execution_engine, data.workflow_id, data.workflow_version,
                data.workflow_version_id, data.workflow_graph_hash,
                _json_dumps(data.workflow_manifest_json)
                if data.workflow_manifest_json is not None else None,
            ),
        )
        return data.id

    def update_run_status(
        self,
        run_id: str,
        status: str,
        finished_at: str | None = None,
        error_summary: str | None = None,
        build_id: str | None = None,
        metadata_json: dict | None = None,
        subloop_stage: str | None = None,
        ontology_version_id: str | None = None,
        current_stage: str | None = None,
        domain: str | None = None,
        expected_statuses: tuple[str, ...] | None = None,
        clear_finished_at: bool = False,
        clear_error_summary: bool = False,
        **counters: int,
    ) -> bool:
        parts = ["status = %s"]
        params: list[Any] = [status]
        if finished_at is not None:
            parts.append("finished_at = %s")
            params.append(finished_at)
        if error_summary is not None:
            parts.append("error_summary = %s")
            params.append(error_summary)
        elif clear_error_summary:
            parts.append("error_summary = NULL")
        if clear_finished_at:
            parts.append("finished_at = NULL")
        if build_id is not None:
            parts.append("build_id = %s")
            params.append(build_id)
        if metadata_json is not None:
            parts.append("metadata_json = %s")
            params.append(_json_dumps(metadata_json))
        if subloop_stage is not None:
            parts.append("subloop_stage = %s")
            params.append(subloop_stage)
        if ontology_version_id is not None:
            parts.append("ontology_version_id = %s")
            params.append(ontology_version_id)
        if current_stage is not None:
            parts.append("current_stage = %s")
            params.append(current_stage)
        for col in ("total_documents", "new_count", "updated_count", "skipped_count", "failed_count", "committed_count"):
            if col in counters:
                parts.append(f"{col} = %s")
                params.append(counters[col])
        where = ["id = %s"]
        params.append(run_id)
        if domain is not None:
            where.append("domain = %s")
            params.append(domain)
        if expected_statuses:
            where.append("status = ANY(%s)")
            params.append(list(expected_statuses))
        count = self._run(
            f"UPDATE mining_runs SET {', '.join(parts)} WHERE {' AND '.join(where)}",
            tuple(params),
            fetch="rowcount",
        )
        return bool(count)

    def set_run_phase(
        self,
        run_id: str,
        domain: str,
        current_stage: str,
        *,
        status: str = "running",
    ) -> bool:
        """Advance an active run without overwriting a terminal/cancelled row."""
        return self.update_run_status(
            run_id,
            status,
            current_stage=current_stage,
            domain=domain,
            expected_statuses=("queued", "running"),
        )

    def finish_ingest(
        self,
        run_id: str,
        domain: str,
        total_documents: int,
        ingest_summary: dict[str, Any],
    ) -> bool:
        count = self._run(
            "UPDATE mining_runs SET status = 'running', current_stage = 'mining', "
            "total_documents = %s, metadata_json = COALESCE(metadata_json, '{}'::jsonb) "
            "|| %s::jsonb WHERE id = %s AND domain = %s "
            "AND status IN ('queued', 'running')",
            (
                total_documents,
                _json_dumps({"ingest_summary": ingest_summary}),
                run_id,
                domain,
            ),
            fetch="rowcount",
        )
        return bool(count)

    def fail_run(
        self,
        run_id: str,
        domain: str,
        error_summary: str,
        current_stage: str,
    ) -> bool:
        return self.update_run_status(
            run_id,
            "failed",
            finished_at=_utcnow(),
            error_summary=error_summary,
            current_stage=current_stage,
            domain=domain,
            expected_statuses=("queued", "running"),
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._fetchone("SELECT * FROM mining_runs WHERE id = %s", (run_id,))

    def operator_statuses_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """(operator_type, status) 行——finalize 期按算子状态留痕用（如 embedding_fallback）。"""
        return self._fetchall(
            "SELECT operator_type, status FROM mining_workflow_node_events "
            "WHERE run_id = %s",
            (run_id,),
        )

    def get_interrupted_runs(self) -> list[dict[str, Any]]:
        return self._fetchall("SELECT * FROM mining_runs WHERE status = 'interrupted' ORDER BY started_at")

    def get_awaiting_review_runs(self, domain: str | None = None) -> list[dict[str, Any]]:
        """列出停在人审 Gate 的 run（B6 暂停态）。domain 可选过滤。"""
        if domain:
            return self._fetchall(
                "SELECT * FROM mining_runs WHERE status = 'awaiting_review' AND domain = %s "
                "ORDER BY started_at",
                (domain,),
            )
        return self._fetchall(
            "SELECT * FROM mining_runs WHERE status = 'awaiting_review' ORDER BY started_at")

    # -- run documents --

    def insert_run_document(self, data: MiningRunDocumentData) -> str:
        self._execute(
            """INSERT INTO mining_run_documents
                   (id, run_id, document_key, raw_content_hash, normalized_content_hash,
                    action, status, document_id, document_snapshot_id, error_message,
                    started_at, finished_at, metadata_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                data.id, data.run_id, data.document_key, data.raw_content_hash,
                data.normalized_content_hash, data.action, data.status,
                data.document_id, data.document_snapshot_id, data.error_message,
                data.started_at, data.finished_at, _json_dumps(data.metadata_json),
            ),
        )
        return data.id

    def update_run_document(
        self,
        rd_id: str,
        status: str | None = None,
        document_id: str | None = None,
        document_snapshot_id: str | None = None,
        error_message: str | None = None,
        finished_at: str | None = None,
        metadata_json: dict | None = None,
        action: str | None = None,
        metadata_patch: dict | None = None,
    ) -> None:
        parts: list[str] = []
        params: list[Any] = []
        if status is not None:
            parts.append("status = %s")
            params.append(status)
        if action is not None:
            parts.append("action = %s")
            params.append(action)
        if document_id is not None:
            parts.append("document_id = %s")
            params.append(document_id)
        if document_snapshot_id is not None:
            parts.append("document_snapshot_id = %s")
            params.append(document_snapshot_id)
        if error_message is not None:
            parts.append("error_message = %s")
            params.append(error_message)
        if finished_at is not None:
            parts.append("finished_at = %s")
            params.append(finished_at)
        if metadata_json is not None:
            parts.append("metadata_json = %s")
            params.append(_json_dumps(metadata_json))
        if metadata_patch:
            # 浅合并：只覆盖 patch 里的键，保留 file_size 等摄取期写入的字段。
            parts.append("metadata_json = COALESCE(metadata_json, '{}'::jsonb) || %s::jsonb")
            params.append(_json_dumps(metadata_patch))
        if not parts:
            return
        params.append(rd_id)
        self._execute(f"UPDATE mining_run_documents SET {', '.join(parts)} WHERE id = %s", tuple(params))

    def get_run_documents(self, run_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM mining_run_documents WHERE run_id = %s ORDER BY id",
            (run_id,),
        )

    def get_run_document_by_key(self, run_id: str, document_key: str) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT * FROM mining_run_documents WHERE run_id = %s AND document_key = %s",
            (run_id, document_key),
        )

    # -- stage events --

    def insert_stage_event(self, data: StageEvent) -> str:
        self._execute(
            """INSERT INTO mining_run_stage_events
                   (id, run_id, run_document_id, stage, status, duration_ms,
                    output_summary, error_message, created_at, metadata_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                data.id, data.run_id, data.run_document_id, data.stage, data.status,
                data.duration_ms, data.output_summary, data.error_message,
                data.created_at or _utcnow(), _json_dumps(data.metadata_json),
            ),
        )
        return data.id

    def get_stage_events(self, run_id: str, run_document_id: str | None = None) -> list[dict[str, Any]]:
        if run_document_id:
            return self._fetchall(
                "SELECT * FROM mining_run_stage_events WHERE run_id = %s AND run_document_id = %s ORDER BY created_at",
                (run_id, run_document_id),
            )
        return self._fetchall(
            "SELECT * FROM mining_run_stage_events WHERE run_id = %s ORDER BY created_at",
            (run_id,),
        )

    def get_last_stage_status(self, run_id: str, run_document_id: str | None, stage: str) -> str | None:
        row = self._fetchone(
            """SELECT status FROM mining_run_stage_events
               WHERE run_id = %s AND stage = %s
               AND (run_document_id = %s OR (CAST(%s AS TEXT) IS NULL AND run_document_id IS NULL))
               ORDER BY created_at DESC LIMIT 1""",
            (run_id, stage, run_document_id, run_document_id),
        )
        return row["status"] if row else None

    def get_committed_document_keys(self, run_id: str) -> frozenset[str]:
        rows = self._fetchall(
            "SELECT document_key FROM mining_run_documents WHERE run_id = %s AND status = 'committed'",
            (run_id,),
        )
        return frozenset(r["document_key"] for r in rows)

    def get_failed_document_keys(self, run_id: str) -> frozenset[str]:
        rows = self._fetchall(
            "SELECT document_key FROM mining_run_documents WHERE run_id = %s AND status IN ('failed', 'processing')",
            (run_id,),
        )
        return frozenset(r["document_key"] for r in rows)
