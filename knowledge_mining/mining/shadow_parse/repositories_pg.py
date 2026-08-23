"""PostgreSQL repository for the Shadow Parse layer (M2).

Implements ``ParseRunRepository``（``shadow_parse/contracts.py``）over a psycopg
``AsyncConnectionPool``，与 ``file_management/repositories_pg.py`` 同风格：
- 构造只接收 pool，不在 import 时建池（组合根负责 ``AsyncConnectionPool``）。
- 每个方法独占一次连接（一个逻辑事务）。
- 幂等 upsert 走 ``ON CONFLICT (document_id, source_raw_hash, parser_fingerprint)
  DO UPDATE ... RETURNING *``（SRS §2.2），冲突时保留原行 ``id``（稳定身份）。
- 时间戳列 TIMESTAMPTZ 绑定 ISO 字符串、``metadata_json`` JSONB 绑定
  ``json.dumps`` 字符串，沿用 file_management PG 仓储的既有惯例。

仅在真实 PG 测试库可用时被加载执行（``KB_RUN_POSTGRES_ACCEPTANCE=1``）；
服务测试套件使用 memory 实现，不触碰本模块。

Column mapping（009 DDL）：
- ``asset_parse_runs``: id/document_id/source_storage_object_id/source_raw_hash/
  source_content_revision/parser_id/parser_fingerprint/
  parse_ir_storage_object_id/parse_ir_schema_version/element_count/
  container_count/relation_count/status/error_message/started_at/finished_at/
  metadata_json

References:
- SRS §2.2（幂等复用）、§C08（Shadow Parse）、§8.5（DDL 边界）。
- ADR-0003 D-006（guarded PG）、D-022（Repository Protocol）。
"""
from __future__ import annotations

import json
from typing import Any

from knowledge_mining.mining.shadow_parse.contracts import (
    ParseAttemptRecord,
    ParseRunRecord,
)
from knowledge_mining.mining.contracts.state_machines import (
    IllegalTransition,
    assert_transition,
)

_COLUMNS = (
    "id, document_id, source_storage_object_id, source_raw_hash, "
    "source_content_revision, parser_id, parser_fingerprint, "
    "parse_ir_storage_object_id, parse_ir_schema_version, element_count, "
    "container_count, relation_count, snapshot_id, status, error_message, "
    "started_at, finished_at, metadata_json"
)

# FAILED 行翻转覆盖列（upsert 的 UPDATE 分支）：14 个 SET 占位符，
# 与调用方参数元组顺序一致（SET 列后跟幂等键三列）。
_CONFLICT_UPDATE = ", ".join(
    f"{col} = %s"
    for col in (
        "source_storage_object_id", "source_content_revision", "parser_id",
        "parse_ir_storage_object_id", "parse_ir_schema_version", "element_count",
        "container_count", "relation_count", "snapshot_id", "status",
        "error_message", "started_at", "finished_at", "metadata_json",
    )
)


def _metadata_json_str(value: Any) -> str:
    """JSONB 读出的 dict / str 统一为 JSON 字符串。"""
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _parse_run_from_row(r: dict[str, Any]) -> ParseRunRecord:
    return ParseRunRecord(
        id=r["id"],
        document_id=r["document_id"],
        source_storage_object_id=r["source_storage_object_id"],
        source_raw_hash=r["source_raw_hash"],
        source_content_revision=r["source_content_revision"],
        parser_id=r["parser_id"],
        parser_fingerprint=r["parser_fingerprint"],
        status=r["status"],
        parse_ir_storage_object_id=r.get("parse_ir_storage_object_id"),
        parse_ir_schema_version=r.get("parse_ir_schema_version"),
        element_count=r.get("element_count"),
        container_count=r.get("container_count"),
        relation_count=r.get("relation_count"),
        snapshot_id=r.get("snapshot_id"),
        error_message=r.get("error_message"),
        started_at=r["started_at"].isoformat()
        if hasattr(r["started_at"], "isoformat")
        else str(r["started_at"]),
        finished_at=r["finished_at"].isoformat()
        if hasattr(r["finished_at"], "isoformat")
        else (str(r["finished_at"]) if r.get("finished_at") is not None else None),
        metadata_json=_metadata_json_str(r.get("metadata_json")),
    )


class PgParseRunRepository:
    """PG ``ParseRunRepository`` over ``asset_parse_runs``（009 DDL）。"""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def upsert(self, record: ParseRunRecord) -> ParseRunRecord:
        async with self._pool.connection() as conn:
            # 幂等语义与 memory 实现对齐（契约 docstring）：只有 FAILED 行可被
            # 翻转/覆盖；已 SUCCEEDED 的行视为等价重复写入，返回原行不改写
            # （防止并发双跑时后写者覆盖已成功行的制品指针）。
            # 010 起幂等键无唯一索引（Run 允许多行），改为读-判-写。
            cur = await conn.execute(
                f"""UPDATE asset_parse_runs SET {_CONFLICT_UPDATE}
                   WHERE document_id = %s AND source_raw_hash = %s
                     AND parser_fingerprint = %s
                     AND status = 'FAILED'
                   RETURNING *""",
                (
                    record.source_storage_object_id,
                    record.source_content_revision, record.parser_id,
                    record.parse_ir_storage_object_id,
                    record.parse_ir_schema_version, record.element_count,
                    record.container_count, record.relation_count,
                    record.snapshot_id, record.status, record.error_message,
                    record.started_at, record.finished_at,
                    json.dumps(json.loads(record.metadata_json or "{}"),
                               ensure_ascii=False),
                    record.document_id, record.source_raw_hash,
                    record.parser_fingerprint,
                ),
            )
            row = await cur.fetchone()
            if row is not None:
                return _parse_run_from_row(dict(row))
            existing = await self.find_by_document_hash(
                record.document_id, record.source_raw_hash,
                record.parser_fingerprint,
            )
            if existing is not None:
                return existing
            return await self.insert(record)

    async def insert(self, record: ParseRunRecord) -> ParseRunRecord:
        """追加新执行行（M4 Operator）；record.status 由 __post_init__ 校验."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""INSERT INTO asset_parse_runs ({_COLUMNS})
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *""",
                (
                    record.id, record.document_id,
                    record.source_storage_object_id, record.source_raw_hash,
                    record.source_content_revision, record.parser_id,
                    record.parser_fingerprint, record.parse_ir_storage_object_id,
                    record.parse_ir_schema_version, record.element_count,
                    record.container_count, record.relation_count,
                    record.snapshot_id,
                    record.status, record.error_message,
                    record.started_at, record.finished_at,
                    json.dumps(json.loads(record.metadata_json or "{}"),
                               ensure_ascii=False),
                ),
            )
            row = await cur.fetchone()
            assert row is not None, "INSERT ... RETURNING must yield a row"
            return _parse_run_from_row(dict(row))

    async def get(self, parse_run_id: str) -> ParseRunRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM asset_parse_runs WHERE id = %s",
                [parse_run_id],
            )
            row = await cur.fetchone()
            return _parse_run_from_row(dict(row)) if row else None

    async def find_by_document_hash(
        self,
        document_id: str,
        source_raw_hash: str,
        parser_fingerprint: str,
    ) -> ParseRunRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM asset_parse_runs
                   WHERE document_id = %s AND source_raw_hash = %s
                     AND parser_fingerprint = %s
                   ORDER BY (status = 'SUCCEEDED' AND snapshot_id IS NOT NULL)
                            DESC, started_at DESC
                   LIMIT 1""",
                [document_id, source_raw_hash, parser_fingerprint],
            )
            row = await cur.fetchone()
            return _parse_run_from_row(dict(row)) if row else None

    async def set_status(
        self,
        parse_run_id: str,
        new_status: str,
        *,
        error_message: str | None = None,
        snapshot_id: str | None = None,
        finished_at: str | None = None,
        parse_ir_storage_object_id: str | None = None,
        parse_ir_schema_version: str | None = None,
        element_count: int | None = None,
        container_count: int | None = None,
        relation_count: int | None = None,
    ) -> ParseRunRecord:
        """执行内状态推进：读当前行 -> assert_transition -> 条件 UPDATE.

        服务端 WHERE 兜底并发（两次并发推进只允许其一改行）；受影响行数
        为 0 时重读——id 不存在抛 KeyError，存在则说明并发竞争让 UPDATE
        条件失效，重读后校验并返回最新行。
        """
        existing = await self.get(parse_run_id)
        if existing is None:
            raise KeyError(f"unknown parse run id: {parse_run_id!r}")
        assert_transition("parse_run", existing.status, new_status)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE asset_parse_runs
                   SET status = %s,
                       error_message = COALESCE(%s, error_message),
                       snapshot_id = COALESCE(%s, snapshot_id),
                       finished_at = COALESCE(%s, finished_at),
                       parse_ir_storage_object_id = COALESCE(
                           %s, parse_ir_storage_object_id),
                       parse_ir_schema_version = COALESCE(
                           %s, parse_ir_schema_version),
                       element_count = COALESCE(%s, element_count),
                       container_count = COALESCE(%s, container_count),
                       relation_count = COALESCE(%s, relation_count)
                   WHERE id = %s AND status = %s
                   RETURNING *""",
                [
                    new_status, error_message, snapshot_id, finished_at,
                    parse_ir_storage_object_id, parse_ir_schema_version,
                    element_count, container_count, relation_count,
                    parse_run_id, existing.status,
                ],
            )
            row = await cur.fetchone()
            if row is None:
                latest = await self.get(parse_run_id)
                if latest is None:
                    raise KeyError(f"unknown parse run id: {parse_run_id!r}")
                if latest.status != new_status:
                    raise IllegalTransition(
                        "parse_run", existing.status, new_status,
                        f"concurrent update moved status to {latest.status}",
                    )
                return latest
            return _parse_run_from_row(dict(row))


def _attempt_from_row(r: dict[str, Any]) -> ParseAttemptRecord:
    return ParseAttemptRecord(
        id=r["id"],
        parse_run_id=r["parse_run_id"],
        attempt_index=r["attempt_index"],
        parser_id=r["parser_id"],
        parser_fingerprint=r["parser_fingerprint"],
        attempt_kind=r["attempt_kind"],
        outcome=r["outcome"],
        started_at=r["started_at"].isoformat()
        if hasattr(r["started_at"], "isoformat")
        else str(r["started_at"]),
        finished_at=r["finished_at"].isoformat()
        if hasattr(r["finished_at"], "isoformat")
        else (str(r["finished_at"]) if r.get("finished_at") is not None else None),
        error_message=r.get("error_message"),
        metadata_json=_metadata_json_str(r.get("metadata_json")),
    )


class PgParseAttemptRepository:
    """PG ``ParseAttemptRepository`` over ``asset_parse_run_attempts``（010）."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def append(self, record: ParseAttemptRecord) -> ParseAttemptRecord:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO asset_parse_run_attempts (
                       id, parse_run_id, attempt_index, parser_id,
                       parser_fingerprint, attempt_kind, outcome, started_at,
                       finished_at, error_message, metadata_json
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (parse_run_id, attempt_index) DO NOTHING
                   RETURNING *""",
                (
                    record.id, record.parse_run_id, record.attempt_index,
                    record.parser_id, record.parser_fingerprint,
                    record.attempt_kind, record.outcome, record.started_at,
                    record.finished_at, record.error_message,
                    json.dumps(json.loads(record.metadata_json or "{}"),
                               ensure_ascii=False),
                ),
            )
            row = await cur.fetchone()
            if row is None:
                raise ValueError(
                    f"attempt_index {record.attempt_index} already exists for "
                    f"run {record.parse_run_id!r}"
                )
            return _attempt_from_row(dict(row))

    async def list_by_run(
        self, parse_run_id: str
    ) -> tuple[ParseAttemptRecord, ...]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT * FROM asset_parse_run_attempts
                   WHERE parse_run_id = %s ORDER BY attempt_index""",
                [parse_run_id],
            )
            rows = await cur.fetchall()
            return tuple(_attempt_from_row(dict(r)) for r in rows)


__all__ = ["PgParseAttemptRepository", "PgParseRunRepository"]
