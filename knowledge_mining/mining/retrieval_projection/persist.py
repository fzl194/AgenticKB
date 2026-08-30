"""三面资产持久化（批次8 M5，24 号 §5.8）.

AssetPersistService：读三个暂存 store（切片/表示/向量）→ 结构化面投影
→ readiness 事实 → 快照级替换写入 AssetWriter（memory 契约实现；PG 实现
执行 schema.py DDL 后参数化写入）。FTS 分词契约在此落地：lexical 文本经
tokenize_for_search（jieba）预分词，tokenizer 版本进 provenance。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from knowledge_mining.mining.infra.text_utils import tokenize_for_search
from knowledge_mining.mining.retrieval_projection.readiness import compute_readiness
from knowledge_mining.mining.retrieval_projection.schema import (
    ASSET_SCHEMA_VERSION,
    TOKENIZER_VERSION,
)
from knowledge_mining.mining.retrieval_projection.structure_projection import (
    project_structure,
)

ASSET_PERSIST_VERSION = "1"


def lexical_text(content_text: str, *, structural_context: str = "") -> str:
    """FTS 索引文本：面包屑 + 正文，经 jieba 预分词（两侧同源契约）."""
    raw = f"{structural_context}\n{content_text}" if structural_context else content_text
    return tokenize_for_search(raw)


@dataclass(frozen=True)
class PersistOutcome:
    document_id: str | None
    snapshot_id: str | None
    readiness: Mapping[str, Any]
    schema_version: str
    tokenizer_version: str
    counts: Mapping[str, int]


class MemoryAssetWriter:
    """契约实现：三面快照级替换（测试/开发；PG 走 PgAssetWriter）."""

    def __init__(self) -> None:
        self.snapshots: dict[str, dict[str, Any]] = {}

    def replace_for_snapshot(
        self, snapshot_id: str, faces: Mapping[str, Any]
    ) -> int:
        self.snapshots[snapshot_id] = dict(faces)
        return int(faces.get("representation_count", 0))


class AssetPersistService:
    """组合三个暂存 store 并完成三面写入（唯一正式入库入口）."""

    def __init__(
        self,
        *,
        segment_store: Any,
        representation_store: Any,
        embedding_store: Any,
        writer: Any,
    ) -> None:
        self._segments = segment_store
        self._representations = representation_store
        self._embeddings = embedding_store
        self._writer = writer

    def persist_for_snapshot(
        self, *, snapshot_id: str | None, document_ref: str
    ) -> PersistOutcome:
        from .async_bridge import run_sync

        if not snapshot_id:
            raise ValueError("persist_for_snapshot requires a snapshot_id")

        segments = run_sync(self._segments.list_for_snapshot(snapshot_id))
        representations = run_sync(
            self._representations.list_for_snapshot(snapshot_id)
        )
        embedding_records = run_sync(self._embeddings.list_for_snapshot(snapshot_id))
        structure = project_structure(segments, document_ref=document_ref)
        readiness = compute_readiness(
            representations=representations,
            structure=structure,
            embedding_records=embedding_records,
        )

        lexical_rows = [
            {
                "representation_id": rep.representation_id,
                "lexical_text": lexical_text(
                    rep.content_text, structural_context=rep.structural_context
                ),
                "tokenizer_version": TOKENIZER_VERSION,
            }
            for rep in representations
            if rep.lexical_eligible
        ]

        faces = {
            "schema_version": ASSET_SCHEMA_VERSION,
            "persist_version": ASSET_PERSIST_VERSION,
            "document_ref": document_ref,
            "raw_segments": tuple(
                {
                    "segment_index": seg.segment_index,
                    "block_type": seg.block_type,
                    "raw_text": seg.raw_text,
                    "heading_chain_json": json.dumps(
                        [[level, title] for level, title in seg.heading_chain],
                        ensure_ascii=False,
                    ),
                    "metadata_json": json.dumps(
                        dict(seg.metadata or {}), ensure_ascii=False
                    ),
                    "token_count": seg.token_count,
                }
                for seg in segments
            ),
            "structure_nodes": structure.nodes,
            "structure_edges": structure.edges,
            "table_assets": structure.table_assets,
            "table_cells": structure.table_cells,
            "representations": tuple(
                {
                    "representation_id": rep.representation_id,
                    "representation_type": rep.representation_type,
                    "content_type": rep.content_type,
                    "content_text": rep.content_text,
                    "structural_context": rep.structural_context,
                    "target_type": rep.target_type,
                    "target_ref": rep.target_ref,
                    "canonical_evidence_id": rep.canonical_evidence_id,
                    "container_ref": rep.container_ref,
                    "parent_ref": rep.parent_ref,
                    "context_group_id": rep.context_group_id,
                    "source_refs_json": json.dumps(
                        [dict(r) for r in rep.source_refs],
                        ensure_ascii=False,
                    ),
                    "ordinal": rep.ordinal,
                    "lexical_eligible": rep.lexical_eligible,
                    "dense_eligible": rep.dense_eligible,
                    "returnable": rep.returnable,
                    "facets_json": json.dumps(dict(rep.facets), ensure_ascii=False),
                    "provenance_json": json.dumps(
                        dict(rep.provenance), ensure_ascii=False
                    ),
                }
                for rep in representations
            ),
            "lexical_rows": tuple(lexical_rows),
            "embeddings": tuple(
                {
                    "embedding_id": record.embedding_id,
                    "representation_id": record.representation_id,
                    "strategy": record.strategy,
                    "policy_version": record.policy_version,
                    "provider": record.provider,
                    "model": record.model,
                    "model_version": record.model_version,
                    "dimension": record.dimension,
                    "input_hash": record.input_hash,
                    "fallback_from": record.fallback_from,
                }
                for record in embedding_records
            ),
            "raw_segment_count": len(segments),
            "representation_count": len(representations),
            "embedding_count": len(embedding_records),
            "structure_node_count": len(structure.nodes),
            "tokenizer_version": TOKENIZER_VERSION,
            # 27号审查修复 B：readiness 随三面原子落库（PgAssetWriter 写
            # asset_snapshot_readiness；finalize 发布门禁与 inspect 消费）。
            "readiness": dict(readiness),
        }
        self._writer.replace_for_snapshot(snapshot_id, faces)

        return PersistOutcome(
            document_id=None,
            snapshot_id=snapshot_id,
            readiness=readiness,
            schema_version=ASSET_SCHEMA_VERSION,
            tokenizer_version=TOKENIZER_VERSION,
            counts={
                "raw_segments": len(segments),
                "representations": len(representations),
                "embeddings": len(embedding_records),
            },
        )


__all__ = [
    "ASSET_PERSIST_VERSION",
    "AssetPersistService",
    "MemoryAssetWriter",
    "PersistOutcome",
    "lexical_text",
]
