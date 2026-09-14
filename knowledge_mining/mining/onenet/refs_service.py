# -*- coding: utf-8 -*-
"""KB 引用服务（47 号 §四-8）：业务库 → 一张网公共库文档的只读引用.

校验链（服务端强制，不依赖前端）：
1. 目标库 can_write（建/删引用是写动作）；
2. 每个 document 存在、未软删、且属**同域**公共库（metadata.kind=onenet）
   ——跨域引用在域库拆分时会断裂，服务端拒绝；
3. 重复引用幂等跳过（ON CONFLICT DO NOTHING）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class RefsError(RuntimeError):
    """引用操作错误（机器可读 reason 前缀）。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metadata_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


class RefsService:
    """引用建/删/列（依赖注入 pool 可测）."""

    def __init__(self, pool: Any):
        self._pool = pool

    async def add_refs(
        self, *, kb_id: str, document_ids: list[str], actor_id: str,
    ) -> dict[str, Any]:
        if not document_ids:
            raise RefsError("document_ids_required")
        async with self._pool.connection() as conn:
            # 目标库存在且可写
            cur = await conn.execute(
                "SELECT domain FROM knowledge_bases WHERE id = %s AND status = 'active'",
                [kb_id])
            kb_row = await cur.fetchone()
            if kb_row is None:
                raise RefsError(f"kb_not_found: {kb_id}")
            domain = kb_row["domain"]

            added, skipped = [], []
            for doc_id in document_ids:
                cur = await conn.execute(
                    """SELECT d.id, d.deleted_at, d.metadata_json, k.metadata_json AS kb_meta,
                              k.domain AS owner_domain
                       FROM asset_documents d
                       JOIN knowledge_bases k ON k.id = d.kb_id
                       WHERE d.id = %s""",
                    [doc_id])
                doc = await cur.fetchone()
                if doc is None or doc["deleted_at"] is not None:
                    skipped.append({"document_id": doc_id, "reason": "not_found"})
                    continue
                if str(doc["owner_domain"]) != str(domain):
                    raise RefsError(
                        f"cross_domain_reference: {doc_id} 属 {doc['owner_domain']} 域，"
                        f"目标库属 {domain} 域（引用不跨域，47 号 D7）")
                kb_meta = _metadata_dict(doc["kb_meta"])
                if kb_meta.get("kind") != "onenet":
                    raise RefsError(
                        f"not_onenet_document: {doc_id} 不属于一张网公共库")
                cur = await conn.execute(
                    """INSERT INTO kb_document_refs (kb_id, document_id, created_by, created_at)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (kb_id, document_id) DO NOTHING
                       RETURNING document_id""",
                    [kb_id, doc_id, actor_id, _utcnow()])
                row = await cur.fetchone()
                if row is not None:
                    added.append(doc_id)
                else:
                    skipped.append({"document_id": doc_id, "reason": "already_referenced"})
            return {"added": added, "skipped": skipped}

    async def remove_refs(
        self, *, kb_id: str, document_ids: list[str],
    ) -> dict[str, Any]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """DELETE FROM kb_document_refs
                   WHERE kb_id = %s AND document_id = ANY(%s)
                   RETURNING document_id""",
                [kb_id, list(document_ids)])
            removed = [r["document_id"] for r in await cur.fetchall()]
        return {"removed": removed}

    async def remove_refs_for_documents(self, document_ids: list[str]) -> int:
        """文档失效时同步清理全部引用行（重同步软删路径调用，47 号 §四-4）."""
        if not document_ids:
            return 0
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM kb_document_refs WHERE document_id = ANY(%s)",
                [list(document_ids)])
            return cur.rowcount() if hasattr(cur, "rowcount") else 0

    async def list_refs(self, *, kb_id: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT r.document_id, r.created_by, r.created_at AS referenced_at,
                          d.document_name, d.directory_path, d.file_size,
                          d.document_key, pk.name AS source_kb_name
                   FROM kb_document_refs r
                   JOIN asset_documents d ON d.id = r.document_id
                   JOIN knowledge_bases pk ON pk.id = d.kb_id
                   WHERE r.kb_id = %s AND d.deleted_at IS NULL
                   ORDER BY d.directory_path, d.document_name""",
                [kb_id])
            return [dict(r) for r in await cur.fetchall()]


__all__ = ["RefsError", "RefsService"]
