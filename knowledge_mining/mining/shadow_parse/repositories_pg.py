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
    ParseRunRecord,
    SHADOW_PARSE_STATUSES,
)

_COLUMNS = (
    "id, document_id, source_storage_object_id, source_raw_hash, "
    "source_content_revision, parser_id, parser_fingerprint, "
    "parse_ir_storage_object_id, parse_ir_schema_version, element_count, "
    "container_count, relation_count, status, error_message, started_at, "
    "finished_at, metadata_json"
)

# ON CONFLICT 覆盖列：除 id 与幂等键三列外的全部列。
_CONFLICT_UPDATE = ", ".join(
    f"{col} = EXCLUDED.{col}"
    for col in (
        "source_storage_object_id", "source_content_revision", "parser_id",
        "parse_ir_storage_object_id", "parse_ir_schema_version", "element_count",
        "container_count", "relation_count", "status", "error_message",
        "started_at", "finished_at", "metadata_json",
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
        if record.status not in SHADOW_PARSE_STATUSES:
            raise ValueError(f"unknown shadow parse status: {record.status!r}")
        async with self._pool.connection() as conn:
            # 幂等语义与 memory 实现对齐（契约 docstring）：只有 FAILED 行可被
            # 翻转/覆盖；已 SUCCEEDED 的行视为等价重复写入，返回原行不改写
            # （防止并发双跑时后写者覆盖已成功行的制品指针）。
            cur = await conn.execute(
                f"""INSERT INTO asset_parse_runs ({_COLUMNS})
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (document_id, source_raw_hash, parser_fingerprint)
                   DO UPDATE SET {_CONFLICT_UPDATE}
                   WHERE asset_parse_runs.status = 'FAILED'
                   RETURNING *""",
                (
                    record.id, record.document_id,
                    record.source_storage_object_id, record.source_raw_hash,
                    record.source_content_revision, record.parser_id,
                    record.parser_fingerprint, record.parse_ir_storage_object_id,
                    record.parse_ir_schema_version, record.element_count,
                    record.container_count, record.relation_count,
                    record.status, record.error_message,
                    record.started_at, record.finished_at,
                    json.dumps(json.loads(record.metadata_json or "{}"),
                               ensure_ascii=False),
                ),
            )
            row = await cur.fetchone()
            if row is not None:
                return _parse_run_from_row(dict(row))
            # WHERE guard 拦下了写入：幂等键上已有 SUCCEEDED 行，读回返回。
            existing = await self.find_by_document_hash(
                record.document_id,
                record.source_raw_hash,
                record.parser_fingerprint,
            )
            assert existing is not None, "conflict row must exist when RETURNING is empty"
            return existing

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
                     AND parser_fingerprint = %s""",
                [document_id, source_raw_hash, parser_fingerprint],
            )
            row = await cur.fetchone()
            return _parse_run_from_row(dict(row)) if row else None


__all__ = ["PgParseRunRepository"]
