"""Parse Result read service（M5.4，SRS §1.2/§C14/C15 子集）.

文件绑定的结构化数据视图：给定 document，取其最新 READY 新链快照，
返回大纲 / 元素 / 表格摘要 / 切片预览 / 出生证明（快照指纹、来源对象
与 revision、解析管线、质量结论）——供前端「结构化数据」页与运行
复核消费。**只读**，不触碰快照生命周期。

设计（ADR-0003 D-022）：只依赖注入 Protocol；IR 从对象存储读回。
"""
from __future__ import annotations

import json
import heapq
import itertools
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from knowledge_mining.mining.contracts.file_management import (
    StorageObjectRepository,
)
from knowledge_mining.mining.contracts.parse_ir.types import (
    Element,
    ParsedDocument,
    TableAsset,
)
from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.contracts.storage.types import ObjectLocation
from knowledge_mining.mining.segment_compiler.service import SegmentStore

#: 元素文本预览截断（前端列表展示用；全文在原文/快照 IR）。
_PREVIEW_CHARS = 400
_OUTLINE_LIMIT = 500
_TABLE_LIMIT = 100
_TABLE_REF_CHARS = 256
_TABLE_CAPTION_CHARS = 240
_ELEMENT_CONTEXT_LIMIT = 5000
_TABLE_CONTEXT_SEGMENT_LIMIT = 2000


def _bounded_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[:limit]


class _LinkLike:
    """kb 层 serving 上下文 dict → link 属性形状（snapshot 卡片消费）."""

    def __init__(self, mapping: Any) -> None:
        self.source_storage_object_id = mapping.get("source_storage_object_id")
        self.source_content_revision = mapping.get("source_content_revision")


def _serving_summary(serving: Any) -> dict[str, Any] | None:
    if not serving:
        return None
    return {
        "document_snapshot_id": serving.get("document_snapshot_id"),
        "build_id": serving.get("build_id"),
        "source_content_revision": serving.get("source_content_revision"),
        "snapshot_created_at": serving.get("snapshot_created_at"),
    }


def _latest_summary(latest: Any) -> dict[str, Any] | None:
    if latest is None:
        return None
    snapshot, link = latest
    return {
        "document_snapshot_id": snapshot.id,
        "source_content_revision": link.source_content_revision,
        "created_at": snapshot.created_at,
    }


@dataclass(frozen=True)
class _ElementContext:
    """Snapshot-local source order and active heading for one unique element."""

    element: Element
    order_index: int
    section_element_id: str | None
    parent_section_element_id: str | None


def _element_contexts(doc: ParsedDocument) -> dict[str, _ElementContext]:
    """Resolve snapshot-local sections only through explicit Parse IR parent links."""
    selected = heapq.nsmallest(
        _ELEMENT_CONTEXT_LIMIT,
        enumerate(doc.elements),
        key=lambda item: (item[1].order_index, item[0]),
    )
    selected_ids = {element.element_id for _position, element in selected}
    counts: dict[str, int] = {element_id: 0 for element_id in selected_ids}
    for element in doc.elements:
        if element.element_id in counts:
            counts[element.element_id] += 1

    unique_elements = {
        element.element_id: element
        for element in doc.elements
        if counts.get(element.element_id) == 1
    }

    def explicit_parent_section(element: Element) -> str | None:
        parent_id = element.parent_id
        seen: set[str] = set()
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = unique_elements.get(parent_id)
            if parent is None:
                return None
            if parent.element_type in ("heading", "title"):
                return parent.element_id
            parent_id = parent.parent_id
        return None

    contexts: dict[str, _ElementContext] = {}
    for _position, element in selected:
        parent_section_id = explicit_parent_section(element)
        section_id: str | None
        if element.element_type in ("heading", "title"):
            section_id = element.element_id
        elif element.parent_id is not None:
            section_id = parent_section_id
        else:
            section_id = None
        if counts[element.element_id] == 1:
            contexts[element.element_id] = _ElementContext(
                element,
                element.order_index,
                section_id,
                parent_section_id if element.element_type in ("heading", "title") else None,
            )
    return contexts


def _segment_source_context(
    segment: CompiledSegment,
    contexts: dict[str, _ElementContext],
) -> tuple[str | None, int | None, int | None]:
    """Return section/start/end only when every source element is unambiguous."""
    element_ids = tuple(dict.fromkeys(segment.element_ids))
    if not element_ids or any(element_id not in contexts for element_id in element_ids):
        return None, None, None
    matched = [contexts[element_id] for element_id in element_ids]
    section_ids = {item.section_element_id for item in matched}
    section_element_id = next(iter(section_ids)) if len(section_ids) == 1 else None
    orders = [item.order_index for item in matched]
    return section_element_id, min(orders), max(orders)


def _table_contexts(
    segments: tuple[CompiledSegment, ...] | list[CompiledSegment],
    contexts: dict[str, _ElementContext],
    allowed_table_refs: set[str],
) -> dict[str, dict[str, str | None]]:
    """Resolve table identity/section/caption only through explicit table_ref facts."""
    collected: dict[str, dict[str, set[str]]] = {}
    for segment in itertools.islice(segments, _TABLE_CONTEXT_SEGMENT_LIMIT):
        if segment.block_type not in {"table", "table_row"}:
            continue
        table_ref = _bounded_text(segment.metadata.get("table_ref"), _TABLE_REF_CHARS)
        if table_ref is None or table_ref not in allowed_table_refs:
            continue
        bucket = collected.setdefault(
            table_ref, {"sources": set(), "sections": set(), "captions": set()}
        )
        source_ids = {
            element_id
            for element_id in segment.element_ids
            if element_id in contexts
            and contexts[element_id].element.element_type == "table"
        }
        if len(source_ids) == 1:
            bucket["sources"].update(source_ids)
        section_element_id, _start, _end = _segment_source_context(segment, contexts)
        if section_element_id is not None:
            bucket["sections"].add(section_element_id)
        caption = _bounded_text(
            segment.metadata.get("table_caption"), _TABLE_CAPTION_CHARS,
        )
        if caption is not None:
            bucket["captions"].add(caption)

    def only(values: set[str]) -> str | None:
        return next(iter(values)) if len(values) == 1 else None

    return {
        table_ref: {
            "source_element_id": only(values["sources"]),
            "parent_section_element_id": only(values["sections"]),
            "caption": only(values["captions"]),
        }
        for table_ref, values in collected.items()
    }


def _segment_summary(
    segment: CompiledSegment,
    contexts: dict[str, _ElementContext],
) -> dict[str, Any]:
    section_element_id, source_order_start, source_order_end = (
        _segment_source_context(segment, contexts)
    )
    return {
        "segment_index": segment.segment_index,
        "block_type": segment.block_type,
        "semantic_role": segment.semantic_role,
        "token_count": segment.token_count,
        "table_kind": segment.metadata.get("table_kind"),
        "view": segment.metadata.get("view"),
        "heading_chain": [
            {"level": level, "title": title}
            for level, title in segment.heading_chain
        ],
        "text": segment.raw_text[:_PREVIEW_CHARS],
        "element_ids": list(segment.element_ids),
        "section_element_id": section_element_id,
        "source_order_start": source_order_start,
        "source_order_end": source_order_end,
        "table_ref": _bounded_text(segment.metadata.get("table_ref"), _TABLE_REF_CHARS),
        "table_caption": _bounded_text(
            segment.metadata.get("table_caption"), _TABLE_CAPTION_CHARS,
        ),
    }


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
        self, *, domain: str, document_id: str,
        view: str = "latest_revision",
        serving: Any | None = None,
    ) -> dict[str, Any] | None:
        """结构化数据视图（A0-1 双视图）.

        - ``view="current_serving"``：该文档当前被搜索/Agent 使用的版本——
          ``serving`` 为 kb 层按 Java serving 同规则查出的快照上下文
          （含 document_snapshot_id/build_id/source_content_revision）。无
          current_serving（未挖掘/失败/移除）时回落展示 latest，并在
          versioning 里明确标记，不冒充当前知识；
        - ``view="latest_revision"``：当前上传文件的最新解析结果（可能尚未
          进入搜索）。

        既有调用方（不传 view/serving）行为不变。
        """
        current = await self._documents.get(document_id) if self._documents else None
        if self._documents is not None and current is None:
            return None
        if current is None:
            latest = await self._snapshots.latest_for_document(document_id, domain)
        else:
            latest = await self._snapshots.latest_for_document(
                document_id,
                domain,
                source_storage_object_id=current.storage_object_id,
                source_content_revision=current.content_revision,
            )

        # 视图选择：current_serving 优先 serving 快照；缺失回落 latest。
        # serving 存在但快照行不可得（域池与 kb 池不一致的异常态）→ 显式 409
        #（路由映射 StorageObjectMissing），不静默用 latest 冒充当前可搜索内容。
        chosen = None
        if view == "current_serving" and serving is not None:
            serving_snapshot = await self._snapshots.get(
                str(serving.get("document_snapshot_id") or "")
            )
            if serving_snapshot is None:
                from knowledge_mining.mining.contracts.storage.errors import (
                    StorageObjectMissing,
                )
                raise StorageObjectMissing(
                    "current serving snapshot row unavailable"
                )
            chosen = (serving_snapshot, _LinkLike(serving))
        if chosen is None:
            chosen = latest
        if chosen is None:
            return None
        snapshot, link = chosen
        doc = await self._load_ir(snapshot.parse_ir_storage_object_id)
        segments = await self._segments.list_for_snapshot(snapshot.id)
        table_total = sum(
            1 for asset in doc.structured_assets.values()
            if isinstance(asset, TableAsset)
        )
        table_assets = list(itertools.islice(
            (
                asset for asset in doc.structured_assets.values()
                if isinstance(asset, TableAsset)
            ),
            _TABLE_LIMIT,
        ))
        element_contexts = _element_contexts(doc)
        table_contexts = _table_contexts(
            segments, element_contexts, {asset.table_id for asset in table_assets},
        )

        serving_id = str(serving.get("document_snapshot_id") or "") if serving else None
        latest_id = latest[0].id if latest is not None else None
        versioning = {
            "view": view,
            "serving": _serving_summary(serving),
            "latest": _latest_summary(latest),
            "in_sync": bool(serving_id and serving_id == latest_id),
            # latest 解析是否已进入当前搜索：not_in_search 时页面必须明示
            "latest_state": (
                "no_results" if latest_id is None
                else "in_search" if serving_id == latest_id
                else "not_in_search"
            ),
        }
        outline_total = sum(
            1 for element in doc.elements
            if element.element_type in ("heading", "title")
        )
        outline_elements = heapq.nsmallest(
            _OUTLINE_LIMIT,
            (e for e in doc.elements if e.element_type in ("heading", "title")),
            key=lambda item: item.order_index,
        )
        outline_items = [
            {
                "element_id": e.element_id,
                "level": _level(e),
                "title": e.text.strip(),
                "order_index": e.order_index,
                "parent_section_element_id": (
                    element_contexts[e.element_id].parent_section_element_id
                    if e.element_id in element_contexts else None
                ),
            }
            for e in outline_elements
        ]
        table_items = [
            _table_summary(a, table_contexts.get(a.table_id))
            for a in table_assets[:_TABLE_LIMIT]
        ]
        return {
            "view": view,
            "versioning": versioning,
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
            "outline": outline_items[:_OUTLINE_LIMIT],
            # 对抗评审 HIGH-2：elements 与 segments 同样限界（count/items），
            # 防大文档无界响应。
            "elements": {
                "count": sum(
                    1 for e in doc.elements
                    if e.element_type not in (
                        "page_header", "page_footer", "page_number",
                    )
                ),
                "items": list(itertools.islice((
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
                ), 500)),
            },
            "tables": table_items,
            "segments": {
                "count": len(segments),
                "items": [
                    _segment_summary(s, element_contexts)
                    for s in segments[:200]
                ],
            },
            "diagnostics": {
                "warnings": list(doc.diagnostics.warnings)[:50],
                "containers": len(doc.containers),
                "relations": len(doc.relations),
                "outline_total": outline_total,
                "tables_total": table_total,
                "outline_truncated": outline_total > _OUTLINE_LIMIT,
                "tables_truncated": table_total > _TABLE_LIMIT,
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


#: 表格预览行上限（完整数据走 Agent 的 get_knowledge 表格查询能力，预览只做诊断）。
_PREVIEW_ROW_LIMIT = 50


def _table_summary(
    asset: TableAsset,
    context: dict[str, str | None] | None = None,
) -> dict[str, Any]:
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
    preview_rows = data_row_indexes[:_PREVIEW_ROW_LIMIT]
    resolved = context or {}
    return {
        "table_id": asset.table_id,
        "rows": len(data_row_indexes),
        "columns": asset.columns,
        "header": header,
        "source_element_id": resolved.get("source_element_id"),
        "parent_section_element_id": resolved.get("parent_section_element_id"),
        "caption": resolved.get("caption"),
        "preview_truncated": len(preview_rows) < len(data_row_indexes),
        "preview": [
            [c.text for c in sorted(
                (c for c in asset.cells if c.row_index == r),
                key=lambda c: c.column_index,
            )]
            for r in preview_rows
        ],
    }


__all__ = ["ParseResultReadService"]
