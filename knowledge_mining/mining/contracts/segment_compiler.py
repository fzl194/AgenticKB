"""Segment Compiler 契约（M5，SRS §4.12 / §C11 / §10.2）.

切片是**知识快照的编译视图**（SRS §3.10）：回答"为了某个场景怎么切"，
不回答"原文件里有什么"。策略参数（范式构建器头部节点可暴露的档位）：

- 粒度：``min_tokens`` / ``max_tokens``（结构边界优先，token 只是上限）；
- 合并：同标题下相邻段是否合并；
- 上下文：是否注入祖先标题链（检索命中时显示"第3章 > 3.2节 > …"）；
- 表格视图：整表 / 逐行（行自动带表头与表名）/ 两者；
- 图文：figure 是否编译为 caption + 正文引用段。

指纹（A08，SRS §8.3A）：策略或编译器版本变化 → 新 compiler_fingerprint
→ **新快照**（复用旧 Parse IR 对象，不重新解析/OCR/云调用）。

兼容投影（SRS §2.3）：``CompiledSegment`` 的字段语义与
``RawSegmentData`` 对齐——``heading_chain`` → ``section_path``、
``links`` → ``source_offsets_json``、``metadata`` → ``structure_json``，
现有 enrich/retrieval_unit/embedding 消费方零改动。

设计（ADR-0003 D-001）：frozen dataclass，纯 stdlib。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

#: 编译器版本（切片逻辑变化必须递增——指纹敏感性的组成部分）。
# v2（2026-08）：表格单视图默认（whole）+ min_tokens 合并生效 + 行边界
# 切分 + 超限表按行分组降级 + semantic_role/table_kind 标注；档位默认
# max=2048/min=512（大上下文窗口尺度，工业界 800–2048 主流区间）。
COMPILER_VERSION = "segment-compiler@2"

#: 表格视图词表（范式构建器下拉档位）。
TABLE_VIEWS = frozenset({"whole", "rows", "both"})


@dataclass(frozen=True)
class SegmentPolicy:
    """切片策略（快照内编译视图的参数；SRS §10.2 segment_compile 参数）.

    token 只是**上限**不是切分依据——结构边界（标题/表格/图）优先，
    超上限才在段落间隙二分（SRS §3.7：Element 不按 token 定义）。
    """

    max_tokens: int = 2048
    min_tokens: int = 512
    merge_adjacent_paragraphs: bool = True
    inject_heading_context: bool = True
    table_view: str = "whole"
    include_figure_captions: bool = True

    def __post_init__(self) -> None:
        if self.min_tokens <= 0:
            raise ValueError(f"min_tokens must be > 0, got {self.min_tokens}")
        if self.max_tokens < self.min_tokens:
            raise ValueError(
                f"max_tokens ({self.max_tokens}) must be >= min_tokens "
                f"({self.min_tokens})"
            )
        if self.table_view not in TABLE_VIEWS:
            raise ValueError(
                f"table_view must be one of {sorted(TABLE_VIEWS)}, got "
                f"{self.table_view!r}"
            )

    def policy_fingerprint(self) -> str:
        """策略指纹（确定性；任一参数变化必变，A08）."""
        payload = json.dumps(
            dataclasses.asdict(self), sort_keys=True, ensure_ascii=False
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compiler_fingerprint(
    policy: SegmentPolicy, *, compiler_version: str = COMPILER_VERSION
) -> str:
    """编译指纹 = 编译器版本 + 策略指纹（进 snapshot_fingerprint 的
    compiler 成分，SRS §8.3A）."""
    digest = hashlib.sha256(
        f"{compiler_version}|{policy.policy_fingerprint()}".encode("utf-8")
    ).hexdigest()[:16]
    return f"segc-{digest}"


@dataclass(frozen=True)
class SegmentElementLink:
    """切片 ↔ 原文元素的多对多映射（SRS §8.2 Segment Element Link）.

    - ``element_id``：快照 Parse IR 内的稳定元素 id；
    - ``evidence_span_ids``：涉及的证据 span（页码/bbox/单元格等定位）；
    - ``char_range``：element.text 内的字符范围（整元素采纳时为 None）。
    """

    element_id: str
    evidence_span_ids: tuple[str, ...] = ()
    char_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class CompiledSegment:
    """一条编译出的切片（字段语义对齐 RawSegmentData 兼容投影）.

    - ``heading_chain``：祖先标题链 ``((level, title), ...)`` →
      ``section_path``（检索命中时显示层级路径）；
    - ``links``：到原文元素/证据的映射 → ``source_offsets_json``；
    - ``metadata``：类型化信息（表格行携带表头/行号，figure 携带
      caption/引用元素）→ ``structure_json``；
    - ``semantic_role``：章节模式推导的语义角色（v2：定义/枚举/例子/
      结论/约束/导航），给下游挖掘 pipeline 提供可过滤轴。
    """

    segment_index: int
    block_type: str
    raw_text: str
    heading_chain: tuple[tuple[int, str], ...] = ()
    element_ids: tuple[str, ...] = ()
    links: tuple[SegmentElementLink, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    token_count: int | None = None
    semantic_role: str = "unknown"


__all__ = [
    "COMPILER_VERSION",
    "CompiledSegment",
    "SegmentElementLink",
    "SegmentPolicy",
    "TABLE_VIEWS",
    "compiler_fingerprint",
]
