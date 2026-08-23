"""PostgreSQL repository for the Segment Compiler layer (M5).

Implements ``SegmentStore`` over 真实 ``asset_raw_segments``（兼容投影
写入，+011 的 compiler_fingerprint 列）与 ``asset_segment_element_links``
（011 新表）。替换语义与 legacy ``delete_segments_by_snapshot`` 惯例一致：
重切 = 先删该快照全部切片与 links，再整体插入。

仅在真实 PG 测试库可用时执行（与 shadow_parse/snapshot_store 的 PG
仓储同风格：构造只收 pool，每方法一次连接 = 一个逻辑事务）。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from knowledge_mining.mining.contracts.segment_compiler import (
    CompiledSegment,
)
from knowledge_mining.mining.segment_compiler.projection import (
    to_raw_segment_data,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class PgSegmentStore:
    """PG ``SegmentStore``：asset_raw_segments（兼容投影）+ element links."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def replace_for_snapshot(
        self,
        snapshot_id: str,
        segments: tuple[CompiledSegment, ...],
        compiler_fingerprint: str,
        *,
        document_key: str,
    ) -> int:
        async with self._pool.connection() as conn:
            # 对抗评审 MEDIUM-5：显式事务包裹删除+插入——中途失败不得留下
            # 空快照（旧切片已删、新切片未写全）。
            async with conn.transaction():
                await self._replace_in_txn(
                    conn, snapshot_id, segments, compiler_fingerprint,
                    document_key=document_key,
                )
        return len(segments)

    async def _replace_in_txn(
        self, conn, snapshot_id, segments, compiler_fingerprint, *,
        document_key,
    ) -> None:
        # 替换语义：先删旧切片与 links（legacy db.py:741 惯例）。
        await conn.execute(
                "DELETE FROM asset_segment_element_links "
                "WHERE document_snapshot_id = %s",
                [snapshot_id],
        )
        await conn.execute(
                "DELETE FROM asset_raw_segments WHERE document_snapshot_id = %s",
                [snapshot_id],
        )
        for seg in segments:
                rsd = to_raw_segment_data(seg, document_key=document_key)
                await conn.execute(
                    """INSERT INTO asset_raw_segments (
                           id, document_snapshot_id, segment_key, segment_index,
                           block_type, semantic_role, section_path, section_title,
                           raw_text, normalized_text, content_hash,
                           normalized_hash, token_count, structure_json,
                           source_offsets_json, entity_refs_json, metadata_json,
                           compiler_fingerprint
                       ) VALUES (
                           %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                       )""",
                    [
                        _new_id("seg"), snapshot_id,
                        f"{document_key}#{seg.segment_index}",
                        seg.segment_index, rsd.block_type, rsd.semantic_role,
                        json.dumps(rsd.section_path, ensure_ascii=False),
                        rsd.section_title, rsd.raw_text, rsd.normalized_text,
                        rsd.content_hash, rsd.normalized_hash, rsd.token_count,
                        json.dumps(rsd.structure_json, ensure_ascii=False),
                        json.dumps(rsd.source_offsets_json, ensure_ascii=False),
                        json.dumps(rsd.entity_refs_json, ensure_ascii=False),
                        json.dumps(rsd.metadata_json, ensure_ascii=False),
                        compiler_fingerprint,
                    ],
                )
                for link in seg.links:
                    await conn.execute(
                        """INSERT INTO asset_segment_element_links (
                               id, document_snapshot_id, segment_index,
                               element_id, evidence_span_ids, char_start,
                               char_end, metadata_json
                           ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        [
                            _new_id("sel"), snapshot_id, seg.segment_index,
                            link.element_id,
                            json.dumps(list(link.evidence_span_ids)),
                            link.char_range[0] if link.char_range else None,
                            link.char_range[1] if link.char_range else None,
                            "{}",
                        ],
                    )

    async def compiler_fingerprint(self, snapshot_id: str) -> str | None:
        """该快照已落库切片的编译指纹（无切片/未编译 → None）.

        SegmentCompileService 幂等短路用：同内容多文档共享同一快照时，
        并发编译会在唯一键上相撞——指纹一致直接复用已有切片。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT compiler_fingerprint FROM asset_raw_segments
                   WHERE document_snapshot_id = %s AND compiler_fingerprint IS NOT NULL
                   LIMIT 1""",
                [snapshot_id],
            )
            row = await cur.fetchone()
        value = row.get("compiler_fingerprint") if row else None
        return value if isinstance(value, str) and value else None

    async def list_for_snapshot(
        self, snapshot_id: str
    ) -> tuple[CompiledSegment, ...]:
        """读回该快照的切片（兼容投影字段的逆映射，供复核/API 消费）."""
        from knowledge_mining.mining.contracts.segment_compiler import (
            SegmentElementLink,
        )

        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT segment_index, block_type, raw_text, section_path,
                          source_offsets_json, structure_json, token_count
                   FROM asset_raw_segments
                   WHERE document_snapshot_id = %s
                   ORDER BY segment_index""",
                [snapshot_id],
            )
            rows = await cur.fetchall()
        out: list[CompiledSegment] = []
        for r in rows:
            offsets = _as_dict(r.get("source_offsets_json"))
            chain = tuple(
                (int(n.get("level", 0)), str(n.get("title", "")))
                for n in _as_list(r.get("section_path"))
            )
            links = tuple(
                SegmentElementLink(
                    element_id=str(l.get("element_id", "")),
                    evidence_span_ids=tuple(
                        str(s) for s in l.get("evidence_span_ids", [])
                    ),
                    char_range=(
                        (int(l["char_range"][0]), int(l["char_range"][1]))
                        if l.get("char_range") else None
                    ),
                )
                for l in offsets.get("element_links", [])
            )
            out.append(CompiledSegment(
                segment_index=int(r["segment_index"]),
                block_type=str(r["block_type"]),
                raw_text=str(r["raw_text"]),
                heading_chain=chain,
                element_ids=tuple(l.element_id for l in links),
                links=links,
                metadata=_as_dict(r.get("structure_json")),
                token_count=r.get("token_count"),
            ))
        return tuple(out)


def _as_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return json.loads(value)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return json.loads(value)


__all__ = ["PgSegmentStore"]
