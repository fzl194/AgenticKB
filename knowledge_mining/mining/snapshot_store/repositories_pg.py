"""PostgreSQL repository for the Snapshot Store layer (M4 WP9).

Implements ``SnapshotRepository``（``contracts/snapshot_store.py``）over a
psycopg ``AsyncConnectionPool``，与 ``shadow_parse/repositories_pg.py``
同风格（构造只收 pool；每方法一次连接 = 一个逻辑事务）。

写入目标（**真实发布表**，SRS 已固定 ``asset_document_snapshots`` 是唯一
知识版本根）：

- ``asset_document_snapshots``：008 目标列 + legacy 必填列；
- ``asset_document_snapshot_links``：来源对象 + content revision（008 列）。

**旧链路串线隔离（M4 审计结论，ADR-0003 D-033）**：新链快照的 workflow
绑定四元组填哨兵 ``new-parse-chain@1``——legacy ``find_reusable_snapshot``
的 ``workflow_graph_hash IS NULL`` 分支与真实 workflow 绑定分支都永远
匹配不到新行；004 的 ``ck_asset_snapshot_workflow_binding_complete`` CHECK
（四列同 NULL 或同 NOT NULL）由哨兵四元组满足。

幂等（§2.2/§8.3A）：``ON CONFLICT (domain, snapshot_fingerprint) WHERE
snapshot_fingerprint IS NOT NULL DO NOTHING``——008 的 partial unique
索引为冲突目标；命中后读回原行（created=False），link 存在则跳过。

仅在真实 PG 测试库可用时执行（``KB_RUN_POSTGRES_ACCEPTANCE=1``）。
"""
from __future__ import annotations

import json
from typing import Any

from knowledge_mining.mining.contracts.snapshot_store import (
    NEW_CHAIN_WORKFLOW_BINDING,
    SnapshotCommitResult,
    SnapshotRecord,
    SnapshotSourceLink,
)

_SNAPSHOT_COLUMNS = (
    "id, domain, normalized_content_hash, raw_content_hash, mime_type, "
    "title, snapshot_fingerprint, parse_ir_storage_object_id, "
    "parse_ir_schema_version, parser_fingerprint, compiler_fingerprint, "
    "quality_status, lifecycle_status, created_by_run_id, "
    "workflow_id, workflow_version, workflow_version_id, workflow_graph_hash, "
    "created_at, parser_profile_json, metadata_json"
)


def _snapshot_from_row(r: dict[str, Any]) -> SnapshotRecord:
    return SnapshotRecord(
        id=r["id"],
        domain=r["domain"],
        snapshot_fingerprint=r["snapshot_fingerprint"],
        raw_content_hash=r["raw_content_hash"],
        normalized_content_hash=r["normalized_content_hash"],
        mime_type=r["mime_type"],
        parse_ir_storage_object_id=r.get("parse_ir_storage_object_id"),
        parse_ir_schema_version=r.get("parse_ir_schema_version"),
        parser_fingerprint=r.get("parser_fingerprint"),
        compiler_fingerprint=r.get("compiler_fingerprint"),
        quality_status=r.get("quality_status") or "PASS",
        lifecycle_status=r.get("lifecycle_status") or "READY",
        created_by_run_id=r.get("created_by_run_id"),
        created_at=r["created_at"].isoformat()
        if hasattr(r["created_at"], "isoformat")
        else str(r["created_at"]),
        title=r.get("title"),
        parser_profile_json=_json_str(r.get("parser_profile_json")),
        metadata_json=_json_str(r.get("metadata_json")),
    )


def _json_str(value: Any) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


class PgSnapshotRepository:
    """PG ``SnapshotRepository`` over 真实 snapshots/links 表（008+010 列）."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def commit(
        self, snapshot: SnapshotRecord, link: SnapshotSourceLink
    ) -> SnapshotCommitResult:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""INSERT INTO asset_document_snapshots (
                        {_SNAPSHOT_COLUMNS}
                   )
                   VALUES (
                       %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       %s,%s,%s,%s,%s,%s,%s
                   )
                   ON CONFLICT (domain, snapshot_fingerprint)
                     WHERE snapshot_fingerprint IS NOT NULL
                   DO NOTHING
                   RETURNING *""",
                (
                    snapshot.id, snapshot.domain,
                    snapshot.normalized_content_hash, snapshot.raw_content_hash,
                    snapshot.mime_type, snapshot.title,
                    snapshot.snapshot_fingerprint,
                    snapshot.parse_ir_storage_object_id,
                    snapshot.parse_ir_schema_version,
                    snapshot.parser_fingerprint,
                    snapshot.compiler_fingerprint,
                    snapshot.quality_status, snapshot.lifecycle_status,
                    snapshot.created_by_run_id,
                    # 旧链路串线隔离：哨兵 workflow 绑定四元组（见模块
                    # docstring；004 CHECK 要求四列同态）。
                    "new-parse-chain", 1,
                    NEW_CHAIN_WORKFLOW_BINDING, NEW_CHAIN_WORKFLOW_BINDING,
                    snapshot.created_at,
                    snapshot.parser_profile_json, snapshot.metadata_json,
                ),
            )
            row = await cur.fetchone()
            if row is not None:
                stored = _snapshot_from_row(dict(row))
                await self._insert_link(conn, link)
                return SnapshotCommitResult(snapshot=stored, created=True)
            # 指纹命中复用：SELECT 必须在同一连接上执行（对抗评审：持
            # 连接再取第二连接，pool min_size=1 时自锁死）；且必须补写该
            # 文档的 link（同内容不同文档共享指纹场景，CRITICAL-1）。
            cur = await conn.execute(
                """SELECT * FROM asset_document_snapshots
                   WHERE domain = %s AND snapshot_fingerprint = %s""",
                [snapshot.domain, snapshot.snapshot_fingerprint],
            )
            row = await cur.fetchone()
            assert row is not None, (
                "conflict on fingerprint must imply an existing row"
            )
            existing_row = _snapshot_from_row(dict(row))
            # link 必须指向既有快照（service 层构造时用的是新 snapshot.id）。
            await self._insert_link(
                conn,
                SnapshotSourceLink(
                    id=link.id, document_id=link.document_id,
                    document_snapshot_id=existing_row.id,
                    source_storage_object_id=link.source_storage_object_id,
                    source_content_revision=link.source_content_revision,
                    title=link.title, linked_at=link.linked_at,
                    source_uri=link.source_uri,
                    relative_path=link.relative_path,
                ),
            )
            return SnapshotCommitResult(
                snapshot=existing_row, created=False,
                reused_reason="fingerprint_hit",
            )

    async def _insert_link(self, conn: Any, link: SnapshotSourceLink) -> None:
        """link 插入（同 snapshot+来源对象已存在则跳过——幂等复用路径）."""
        cur = await conn.execute(
            """SELECT 1 FROM asset_document_snapshot_links
               WHERE document_snapshot_id = %s
                 AND source_storage_object_id = %s
                 AND document_id = %s""",
            [link.document_snapshot_id, link.source_storage_object_id,
             link.document_id],
        )
        if await cur.fetchone() is not None:
            return
        await conn.execute(
            """INSERT INTO asset_document_snapshot_links (
                   id, document_id, document_snapshot_id, relative_path,
                   source_uri, title, linked_at, metadata_json,
                   source_storage_object_id, source_content_revision
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            [
                link.id, link.document_id, link.document_snapshot_id,
                link.relative_path or link.source_uri,  # legacy NOT NULL 哨兵
                link.source_uri, link.title, link.linked_at,
                "{}", link.source_storage_object_id,
                link.source_content_revision,
            ],
        )

    async def get(self, snapshot_id: str) -> SnapshotRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM asset_document_snapshots WHERE id = %s",
                [snapshot_id],
            )
            row = await cur.fetchone()
            return _snapshot_from_row(dict(row)) if row else None

    async def find_by_fingerprint(
        self, domain: str, fingerprint: str
    ) -> SnapshotRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM asset_document_snapshots
                   WHERE domain = %s AND snapshot_fingerprint = %s""",
                [domain, fingerprint],
            )
            row = await cur.fetchone()
            return _snapshot_from_row(dict(row)) if row else None

    async def latest_for_document(
        self, document_id: str, domain: str
    ) -> tuple[SnapshotRecord, SnapshotSourceLink] | None:
        from knowledge_mining.mining.contracts.snapshot_store import (
            SnapshotSourceLink,
        )

        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT snapshots.*, links.source_storage_object_id AS _src_obj,
                          links.source_content_revision AS _src_rev,
                          links.title AS _link_title
                   FROM asset_document_snapshots AS snapshots
                   JOIN asset_document_snapshot_links AS links
                     ON links.document_snapshot_id = snapshots.id
                   WHERE links.document_id = %s AND snapshots.domain = %s
                     AND snapshots.lifecycle_status = 'READY'
                     AND snapshots.snapshot_fingerprint IS NOT NULL
                   ORDER BY snapshots.created_at DESC,
                            links.linked_at DESC, snapshots.id DESC
                   LIMIT 1""",
                [document_id, domain],
            )
            row = await cur.fetchone()
        if row is None:
            return None
        data = dict(row)
        link = SnapshotSourceLink(
            id="", document_id=document_id,
            document_snapshot_id=data["id"],
            source_storage_object_id=data.get("_src_obj") or "",
            source_content_revision=int(data.get("_src_rev") or 0),
            title=data.get("_link_title"),
        )
        return _snapshot_from_row(data), link

    async def mark_lifecycle(
        self, snapshot_id: str, lifecycle_status: str
    ) -> SnapshotRecord:
        existing = await self.get(snapshot_id)
        if existing is None:
            raise KeyError(f"unknown snapshot id: {snapshot_id!r}")
        if existing.lifecycle_status != "READY":
            raise ValueError(
                f"lifecycle is one-way from READY; current="
                f"{existing.lifecycle_status!r}, requested={lifecycle_status!r}"
            )
        if lifecycle_status not in ("DEPRECATED", "REVOKED"):
            raise ValueError(
                f"lifecycle may only move to DEPRECATED/REVOKED, got "
                f"{lifecycle_status!r}"
            )
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE asset_document_snapshots
                   SET lifecycle_status = %s
                   WHERE id = %s AND lifecycle_status = 'READY'
                   RETURNING *""",
                [lifecycle_status, snapshot_id],
            )
            row = await cur.fetchone()
            assert row is not None, "guarded by pre-read; concurrent move rare"
            return _snapshot_from_row(dict(row))


__all__ = ["PgSnapshotRepository"]
