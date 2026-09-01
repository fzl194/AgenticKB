"""Parse Result read service（M5.4，SRS §1.2/§C14/C15 子集）.

文件绑定的结构化数据视图：给定 document，取其最新 READY 新链快照，
返回大纲 / 元素 / 表格摘要 / 切片预览 / 出生证明（快照指纹、来源对象
与 revision、解析管线、质量结论）——供前端「结构化数据」页与运行
复核消费。**只读**，不触碰快照生命周期。

设计（ADR-0003 D-022）：只依赖注入 Protocol；IR 从对象存储读回。
"""
from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any

from knowledge_mining.mining.contracts.file_management import (
    StorageObjectRepository,
)
from knowledge_mining.mining.contracts.parse_ir.types import (
    ParsedDocument,
    TableAsset,
)
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.contracts.storage.types import ObjectLocation
from knowledge_mining.mining.segment_compiler.service import SegmentStore

#: 元素文本预览截断（前端列表展示用；全文在原文/快照 IR）。
_PREVIEW_CHARS = 400


class ParseResultReadService:
    """document -> 最新新链快照的结构化数据视图（只读）."""

    def __init__(
        self,
        *,
        snapshots: Any,
        storage_objects: StorageObjectRepository,
        object_store: ObjectStorePort,
        segment_store: SegmentStore,
        documents: Any | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._storage_objects = storage_objects
        self._store = object_store
        self._segments = segment_store
        self._documents = documents

    async def get_parse_result(
        self, *, domain: str, document_id: str
    ) -> dict[str, Any] | None:
        current = await self._documents.get(document_id) if self._documents else None
        if self._documents is not None and current is None:
            return None
        if current is None:
            found = await self._snapshots.latest_for_document(document_id, domain)
        else:
            found = await self._snapshots.latest_for_document(
                document_id,
                domain,
                source_storage_object_id=current.storage_object_id,
                source_content_revision=current.content_revision,
            )
        if found is None:
            return None
        snapshot, link = found
        doc = await self._load_ir(snapshot.parse_ir_storage_object_id)
        segments = await self._segments.list_for_snapshot(snapshot.id)
        return {
            "snapshot": {
                "id": snapshot.id,
                "title": snapshot.title,
                "mime_type": snapshot.mime_type,
                "quality_status": snapshot.quality_status,
                "lifecycle_status": snapshot.lifecycle_status,
                "parser_fingerprint": snapshot.parser_fingerprint,
                "compiler_fingerprint": snapshot.compiler_fingerprint,
                "snapshot_fingerprint": snapshot.snapshot_fingerprint,
                "created_by_run_id": snapshot.created_by_run_id,
                "created_at": snapshot.created_at,
                # 出生证明：来源对象 + 内容版本（links 行语义）
                "source_storage_object_id": link.source_storage_object_id,
                "source_content_revision": link.source_content_revision,
            },
            "outline": [
                {
                    "element_id": e.element_id,
                    "level": _level(e),
                    "title": e.text.strip(),
                }
                for e in doc.elements
                if e.element_type in ("heading", "title")
            ],
            # 对抗评审 HIGH-2：elements 与 segments 同样限界（count/items），
            # 防大文档无界响应。
            "elements": {
                "count": sum(
                    1 for e in doc.elements
                    if e.element_type not in (
                        "page_header", "page_footer", "page_number",
                    )
                ),
                "items": [
                    {
                        "element_id": e.element_id,
                        "element_type": e.element_type,
                        "text": e.text[:_PREVIEW_CHARS],
                        "order_index": e.order_index,
                        "containers": list(e.page_span_ids),
                        "has_evidence": bool(e.source_spans),
                    }
                    for e in doc.elements
                    if e.element_type not in (
                        "page_header", "page_footer", "page_number",
                    )
                ][:500],
            },
            "tables": [
                _table_summary(a)
                for a in doc.structured_assets.values()
                if isinstance(a, TableAsset)
            ],
            "segments": {
                "count": len(segments),
                "items": [
                    {
                        "segment_index": s.segment_index,
                        "block_type": s.block_type,
                        # v2 语义轴：章节角色（定义/枚举/例子/结论/约束/导航）
                        # 与表格视图/语义类型——前端展示与下游挖掘的过滤轴。
                        "semantic_role": s.semantic_role,
                        "token_count": s.token_count,
                        "table_kind": s.metadata.get("table_kind"),
                        "view": s.metadata.get("view"),
                        "heading_chain": [
                            {"level": lv, "title": t} for lv, t in s.heading_chain
                        ],
                        "text": s.raw_text[:_PREVIEW_CHARS],
                        "element_ids": list(s.element_ids),
                    }
                    for s in segments[:200]
                ],
            },
            "diagnostics": {
                "warnings": list(doc.diagnostics.warnings)[:50],
                "containers": len(doc.containers),
                "relations": len(doc.relations),
            },
        }

    #: 快照 IR LRU（批次3-问题2）：快照不可变，按 parse_ir 对象键缓存；
    #: 容量按条数（单 IR MB 级，16 条 ≈ 数十 MB 上限）。类级共享。
    _IR_CACHE_MAX = 16
    _ir_cache: "OrderedDict[str, ParsedDocument]" = OrderedDict()

    async def _load_ir(self, storage_object_id: str | None) -> ParsedDocument:
        from knowledge_mining.mining.contracts.storage.errors import (
            StorageObjectMissing,
        )

        if not storage_object_id:
            raise StorageObjectMissing("snapshot has no parse IR object")
        cached = ParseResultReadService._ir_cache.get(storage_object_id)
        if cached is not None:
            ParseResultReadService._ir_cache.move_to_end(storage_object_id)
            return cached
        record = await self._storage_objects.get(storage_object_id)
        if record is None:
            raise StorageObjectMissing(
                f"parse IR storage object {storage_object_id!r} not registered"
            )
        location = ObjectLocation(
            bucket=record.bucket, object_key=record.object_key,
            version_id=record.object_version_id,
        )
        # 批次3-问题2：磁盘直解——分块落盘后 json.load 直读文件，
        # 消灭内存里的 chunks 列表 + b"".join 两份全量副本（原 4 份 → 2 份）。
        import tempfile
        import os
        fd, tmp_path = tempfile.mkstemp(prefix=".ir-load-")
        try:
            with os.fdopen(fd, "wb") as fh:
                async for chunk in self._store.get_stream(location):
                    fh.write(chunk)
            with open(tmp_path, "rb") as fh:
                doc = ParsedDocument.from_dict(json.load(fh))
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        ParseResultReadService._ir_cache[storage_object_id] = doc
        ParseResultReadService._ir_cache.move_to_end(storage_object_id)
        while len(ParseResultReadService._ir_cache) > ParseResultReadService._IR_CACHE_MAX:
            ParseResultReadService._ir_cache.popitem(last=False)
        return doc


def _level(element: Any) -> int:
    level = element.style.get("level")
    return int(level) if isinstance(level, int) and level > 0 else 1


#: 表格预览行上限（完整数据走结构化查询/query_structured_asset，预览只做诊断）。
_PREVIEW_ROW_LIMIT = 50


def _table_summary(asset: TableAsset) -> dict[str, Any]:
    # 2026-09-01 用户反馈修复：①preview 曾取 range(min(rows, 5)) 且不过滤
    # is_header——首行表头与前端列 label 重复渲染、23 行表只见 5 行；
    # ②"rows" 曾是含表头的总行数，与切片行片数/结构化查询行数不一致。
    # 现在：rows = 数据行数；preview = 前 _PREVIEW_ROW_LIMIT 个数据行。
    header_rows = {c.row_index for c in asset.cells if c.is_header}
    header = [
        c.text for c in sorted(
            (c for c in asset.cells
             if c.is_header and c.row_index == min(header_rows, default=-1)),
            key=lambda c: c.column_index,
        )
    ]
    data_row_indexes = [
        r for r in range(asset.rows) if r not in header_rows
    ]
    return {
        "table_id": asset.table_id,
        "rows": len(data_row_indexes),
        "columns": asset.columns,
        "header": header,
        "preview": [
            [c.text for c in sorted(
                (c for c in asset.cells if c.row_index == r),
                key=lambda c: c.column_index,
            )]
            for r in data_row_indexes[:_PREVIEW_ROW_LIMIT]
        ],
    }


__all__ = ["ParseResultReadService"]
