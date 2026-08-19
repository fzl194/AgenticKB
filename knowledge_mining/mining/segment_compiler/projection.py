"""CompiledSegment -> RawSegmentData 兼容投影（M5，SRS §2.3/§8.3）.

现有 enrich / discourse / retrieval_unit / embedding 消费 ``RawSegmentData``
（旧 ``parse_segment`` 算子的输出形状）。新链路切片经本投影零改动接入
这些下游环节——字段映射：

| CompiledSegment | RawSegmentData |
|---|---|
| ``segment_index`` / ``block_type`` / ``raw_text`` | 同名 |
| ``heading_chain`` | ``section_path``（``[{level, title}]``）+ ``section_title``（最内层） |
| ``links`` | ``source_offsets_json.element_links``（元素 + 证据 span + char_range） |
| ``metadata`` | ``structure_json``（表格行表头/行号、figure caption 等） |
| 内容哈希 | ``content_hash`` / ``normalized_hash``（sha256） |

设计（ADR-0003 D-001）：纯函数，无 IO。
"""
from __future__ import annotations

import hashlib
import re

from knowledge_mining.mining.contracts.models import RawSegmentData
from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment

#: 新链切片类型 -> legacy block_type 白名单映射（asset_raw_segments 有
#: CHECK 约束 + 下游 enrich 按类型分支——投影层收敛词表，不改 DB/下游）。
_BLOCK_TYPE_PROJECTION = {
    "table_row": "table",      # structure_json 保留 row 细节
    "figure": "image",         # structure_json 保留 caption
    "list_item": "list",
    "quote": "blockquote",
    "caption": "paragraph",
    "toc_entry": "paragraph",
    "reference": "paragraph",
    "footnote": "paragraph",
}

#: legacy asset_raw_segments.block_type CHECK 白名单（002 DDL）。
_LEGACY_BLOCK_TYPES = frozenset({
    "paragraph", "heading", "table", "list", "code", "blockquote",
    "html_table", "raw_html", "image", "unknown",
})


def to_raw_segment_data(
    segment: CompiledSegment, *, document_key: str
) -> RawSegmentData:
    """一条编译切片 -> 旧链路下游可消费的 RawSegmentData."""
    section_path = [
        {"level": level, "title": title} for level, title in segment.heading_chain
    ]
    element_links = [
        {
            "element_id": link.element_id,
            "evidence_span_ids": list(link.evidence_span_ids),
            **({"char_range": [link.char_range[0], link.char_range[1]]}
               if link.char_range is not None else {}),
        }
        for link in segment.links
    ]
    raw_text = segment.raw_text
    normalized = _normalize(raw_text)
    return RawSegmentData(
        document_key=document_key,
        segment_index=segment.segment_index,
        # 对抗评审 HIGH-3：未知类型回落 unknown——白名单闭合，杜绝
        # INSERT 击穿 DB CHECK 导致整快照编译失败。
        block_type=_BLOCK_TYPE_PROJECTION.get(
            segment.block_type,
            segment.block_type
            if segment.block_type in _LEGACY_BLOCK_TYPES else "unknown",
        ),
        section_path=section_path,
        section_title=segment.heading_chain[-1][1] if segment.heading_chain else None,
        raw_text=raw_text,
        normalized_text=normalized,
        content_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        normalized_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        token_count=segment.token_count,
        structure_json=dict(segment.metadata),
        source_offsets_json={"element_links": element_links},
        metadata_json={"compiler": "segment-compiler"},
    )


def _normalize(text: str) -> str:
    """轻量归一（空白折叠）——与旧链路语义对齐即可，不做语言处理."""
    return re.sub(r"\s+", " ", text).strip()


__all__ = ["to_raw_segment_data"]
