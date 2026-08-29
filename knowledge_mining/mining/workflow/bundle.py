"""版本化 MiningDocumentBundle（批次8 M1，24 号 §3.3）。

document_parse 直交、segment_compile 续写的**唯一文档态载体**——取代
legacy ``DocumentContext`` 在算子间的传递（旧兼容投影已删除）。

序列化边界（24 号 §3.3 审查追加）：
- bundle 只携带**引用与计数**（snapshot_ref/parse_ir_ref/compiled_segment_count），
  永不携带切片/表示/向量本体；
- 切片落在 SegmentStore（按 snapshot_ref 经 ``list_for_snapshot`` 读）；
- representations/embeddings 由 M2/M4 填充计数，本体同样落库不进 bundle。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment

BUNDLE_VERSION = "1"


@dataclass(frozen=True)
class MiningDocumentBundle:
    """一个 Run 文档的版本化状态包。

    字段分组：
    - 身份：document_ref（document_key）/run_document_id/bundle_version；
    - 解析指针：snapshot_ref/parse_ir_ref/parser_fingerprint/quality_status；
    - 生命周期（asset_persist/M5 消费）：raw_file/profile/action/existing_doc/document_id；
    - 编译事实：compiled_segment_count/compiler_fingerprint/document_facts；
    - 后续阶段（M2/M4/M5 填充）：representations_count/embeddings_count/
      capability_facts/diagnostics。
    """

    document_ref: str
    run_document_id: str
    bundle_version: str = BUNDLE_VERSION
    snapshot_ref: str | None = None
    parse_ir_ref: str | None = None
    parser_fingerprint: str | None = None
    quality_status: str | None = None
    raw_file: Any = None
    profile: Any = None
    action: str | None = None
    existing_doc: Any = None
    document_id: str | None = None
    compiled_segment_count: int = 0
    compiler_fingerprint: str | None = None
    document_facts: Mapping[str, Any] = field(default_factory=dict)
    representations_count: int = 0
    embeddings_count: int = 0
    capability_facts: frozenset[str] = frozenset()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def with_updates(self, **changes: Any) -> "MiningDocumentBundle":
        """不可变更新：返回携带变更的新 bundle（对齐 DocumentState 语义）。"""
        return replace(self, **changes)


def compute_document_facts(
    segments: Iterable[CompiledSegment],
) -> dict[str, Any]:
    """从编译切片做确定性聚合（24 号 §5.3 文档统计；C4：不设独立算子）。"""
    block_type_counts: dict[str, int] = {}
    token_total = 0
    section_paths: set[tuple[tuple[int, str], ...]] = set()
    max_depth = 0
    count = 0
    for segment in segments:
        count += 1
        block_type_counts[segment.block_type] = (
            block_type_counts.get(segment.block_type, 0) + 1
        )
        token_total += segment.token_count or 0
        chain = tuple(segment.heading_chain)
        if chain:
            section_paths.add(chain)
            max_depth = max(max_depth, len(chain))
    return {
        "segment_count": count,
        "token_total": token_total,
        "section_count": len(section_paths),
        "max_section_depth": max_depth,
        "block_type_counts": dict(sorted(block_type_counts.items())),
        "block_type_ratios": {
            block_type: round(amount / count, 4)
            for block_type, amount in sorted(block_type_counts.items())
        } if count else {},
    }


__all__ = [
    "BUNDLE_VERSION",
    "MiningDocumentBundle",
    "compute_document_facts",
]
