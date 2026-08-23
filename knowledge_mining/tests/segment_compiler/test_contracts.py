"""M5.1 切片编译契约（RED 先行）：SegmentPolicy / 编译指纹 / 元素链接.

- SegmentPolicy：范式构建器可暴露的参数档位（粒度/合并/标题链注入/
  表格视图/图注），frozen + 指纹（策略变化 → 新快照，A08）。
- compiler_fingerprint：编译器版本 + 策略指纹的合成。
- SegmentElementLink：切片 ↔ 原文元素/证据的多对多映射（SRS §8.2）。
- CompiledSegment：编译产物（向 RawSegmentData 兼容投影对齐）。
"""
from __future__ import annotations

import dataclasses

import pytest


def test_segment_policy_defaults_and_frozen() -> None:
    from knowledge_mining.mining.contracts.segment_compiler import (
        SegmentPolicy,
    )

    policy = SegmentPolicy()
    assert policy.max_tokens >= policy.min_tokens > 0
    assert policy.table_view in ("whole", "rows", "both")
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.max_tokens = 1  # type: ignore[misc]


def test_segment_policy_validates_bounds() -> None:
    from knowledge_mining.mining.contracts.segment_compiler import (
        SegmentPolicy,
    )

    with pytest.raises(ValueError, match="max_tokens"):
        SegmentPolicy(max_tokens=32, min_tokens=64)  # 上限低于下限
    with pytest.raises(ValueError, match="table_view"):
        SegmentPolicy(table_view="magic")


def test_policy_fingerprint_changes_with_params() -> None:
    from knowledge_mining.mining.contracts.segment_compiler import (
        SegmentPolicy,
    )

    base = SegmentPolicy().policy_fingerprint()
    assert base
    assert base != SegmentPolicy(max_tokens=1024).policy_fingerprint()
    assert base != SegmentPolicy(table_view="both").policy_fingerprint()
    assert SegmentPolicy().policy_fingerprint() == base  # 确定性


def test_compiler_fingerprint_combines_version_and_policy() -> None:
    from knowledge_mining.mining.contracts.segment_compiler import (
        COMPILER_VERSION,
        SegmentPolicy,
        compiler_fingerprint,
    )

    fp = compiler_fingerprint(SegmentPolicy())
    assert fp and fp != SegmentPolicy().policy_fingerprint()
    assert COMPILER_VERSION in fp or fp.startswith("segc-")
    assert compiler_fingerprint(SegmentPolicy(max_tokens=1024)) != fp


def test_segment_element_link_shape() -> None:
    from knowledge_mining.mining.contracts.segment_compiler import (
        SegmentElementLink,
    )

    link = SegmentElementLink(
        element_id="e-12",
        evidence_span_ids=("s-1", "s-2"),
        char_range=(0, 342),
    )
    assert link.char_range == (0, 342)


def test_compiled_segment_carries_chain_links_and_projection_fields() -> None:
    from knowledge_mining.mining.contracts.segment_compiler import (
        CompiledSegment,
        SegmentElementLink,
    )

    seg = CompiledSegment(
        segment_index=3,
        block_type="table_row",
        raw_text="告警码 | A-101 | 原因 | 风扇停转",
        heading_chain=((1, "告警处理"), (2, "硬件告警")),
        element_ids=("table-12",),
        links=(SegmentElementLink(element_id="table-12"),),
        metadata={"table_header": ["告警码", "原因"], "row_index": 5},
    )
    # 面向 RawSegmentData 兼容投影的字段语义：
    assert [t for _, t in seg.heading_chain] == ["告警处理", "硬件告警"]
    assert seg.metadata["row_index"] == 5
