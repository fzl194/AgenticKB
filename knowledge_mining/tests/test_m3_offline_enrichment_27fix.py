"""27号审查修复 D 的回归测试：M3 两实验算子的实现缺陷修正.

- answer_span 只对源表示原文校验（LLM 自报 source_text 不能扩大证据范围）；
- maxAliasesPerTarget 由算子参数生效并按 canonical 计数；
- hierarchical summary 真自底向上（子摘要进父输入；文档摘要含顶层摘要）；
- alias 子集替换语义（replace_aliases_for_snapshot 不清基础表示）。
"""
from __future__ import annotations

import asyncio

from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrievalRepresentation,
)


def _rep(rep_type="prose", *, text="一段足够长的正文内容用于通过资格门审查。" * 6,
         canonical="d#seg:0", ordinal=0):
    return RetrievalRepresentation(
        representation_id=f"d:s1:{rep_type}:{ordinal}",
        representation_type=rep_type,
        content_type=rep_type,
        content_text=text,
        structural_context="章一",
        target_type=rep_type if rep_type != "prose" else "segment",
        target_ref=canonical,
        canonical_evidence_id=canonical,
    )


class _FakeGenerator:
    def __init__(self, outcomes) -> None:
        self._outcomes = list(outcomes)

    def generate_questions(self, items):
        results = []
        for _ in items:
            outcome = self._outcomes.pop(0) if self._outcomes else "SKIP"
            results.append(outcome)
        return results


def test_query_expansion_27fix_span_trusts_source_only(tmp_path):
    from knowledge_mining.mining.retrieval_projection.query_expansion import (
        QueryExpansionFacade,
    )
    from knowledge_mining.mining.retrieval_projection.repositories_memory import (
        MemoryAliasStore,
        MemoryRepresentationStore,
    )

    rep_store, alias_store = MemoryRepresentationStore(), MemoryAliasStore()
    reps = (
        _rep("prose", canonical="d#seg:0"),
        _rep(
            "prose",
            text="风扇停转时应当先检查 电源 模块。检修流程详见维护手册相应章节，"
                 "并确认风扇指示灯状态与告警码的对应关系后再进行更换操作流程。"
                 "更换前务必断电并佩戴防静电手环，更换后重启设备观察十分钟确认。",
            canonical="d#seg:1", ordinal=1,
        ),
    )
    asyncio.new_event_loop().run_until_complete(
        rep_store.replace_for_snapshot("s1", reps, "proj", document_key="d")
    )
    generator = _FakeGenerator([
        # 1) span 只出现在 LLM 自报 source_text、不在源表示原文 → invalid
        {"question": "Q1", "answer_span": "完全编造的答案",
         "source_text": "完全编造的答案"},
        # 2) span 在源表示原文 → 有效（source_text 留空也不影响）
        {"question": "Q2", "answer_span": "先检查 电源 模块。", "source_text": ""},
    ])
    facade = QueryExpansionFacade(
        representation_store=rep_store, alias_store=alias_store,
        generator=generator,
    )
    outcome = facade.generate_for_snapshot(snapshot_id="s1", params={})
    assert outcome.invalid == 1
    assert len(outcome.aliases) == 1
    assert outcome.aliases[0].canonical_evidence_id == "d#seg:1"


def test_query_expansion_27fix_per_target_cap(tmp_path):
    from knowledge_mining.mining.retrieval_projection.query_expansion import (
        QueryExpansionFacade,
    )
    from knowledge_mining.mining.retrieval_projection.repositories_memory import (
        MemoryAliasStore,
        MemoryRepresentationStore,
    )

    long_text = "风扇停转时应当先检查 电源 模块。检修流程详见维护手册相应章节。" * 3
    rep_store, alias_store = MemoryRepresentationStore(), MemoryAliasStore()
    reps = tuple(
        _rep("prose", text=long_text, canonical="d#seg:0", ordinal=i)
        for i in range(3)
    ) + (_rep("prose", text=long_text, canonical="d#seg:9", ordinal=9),)
    asyncio.new_event_loop().run_until_complete(
        rep_store.replace_for_snapshot("s2", reps, "proj", document_key="d")
    )
    generator = _FakeGenerator([
        {"question": f"Q{i}", "answer_span": "先检查 电源 模块。"}
        for i in range(4)
    ])
    facade = QueryExpansionFacade(
        representation_store=rep_store, alias_store=alias_store,
        generator=generator,
    )
    outcome = facade.generate_for_snapshot(
        snapshot_id="s2", params={"maxAliasesPerTarget": 2},
    )
    assert len(outcome.aliases) == 3  # d#seg:0 ×2 + d#seg:9 ×1
    per_target: dict[str, int] = {}
    for alias in outcome.aliases:
        per_target[alias.canonical_evidence_id] = (
            per_target.get(alias.canonical_evidence_id, 0) + 1
        )
    assert per_target == {"d#seg:0": 2, "d#seg:9": 1}


def test_summary_27fix_child_summaries_feed_parents():
    from knowledge_mining.mining.contracts.segment_compiler import (
        CompiledSegment,
        SegmentElementLink,
    )
    from knowledge_mining.mining.retrieval_projection.summary import (
        HierarchicalSummaryFacade,
    )
    from knowledge_mining.mining.segment_compiler.repositories_memory import (
        MemorySegmentStore,
    )

    segments = (
        CompiledSegment(
            segment_index=0, block_type="paragraph", raw_text="章一第一段正文。",
            heading_chain=((1, "章一"),),
            links=(SegmentElementLink(element_id="e0"),), token_count=60,
        ),
        CompiledSegment(
            segment_index=1, block_type="paragraph", raw_text="章一第二段正文。",
            heading_chain=((1, "章一"), (2, "小节"),),
            links=(SegmentElementLink(element_id="e1"),), token_count=60,
        ),
    )
    seg_store = MemorySegmentStore()
    asyncio.new_event_loop().run_until_complete(
        seg_store.replace_for_snapshot("s1", segments, "segc", document_key="d")
    )

    calls: list[tuple[str, list[str]]] = []

    class _Recording:
        def summarize(self, title, texts):
            calls.append((title, list(texts)))
            return f"{title}摘要"

    facade = HierarchicalSummaryFacade(
        segment_store=seg_store, alias_store=None, summarizer=_Recording(),
    )
    outcome = facade.generate_for_snapshot(
        snapshot_id="s1",
        params={"minSectionTokens": 10, "documentRef": "d.md"},
    )
    titles = [c[0] for c in calls]
    # 深层先于浅层，document 最后
    assert titles.index("小节") < titles.index("章一")
    assert titles[-1] == "d.md"
    # 父（章一）输入包含子（小节）的摘要文本
    parent_texts = calls[titles.index("章一")][1]
    assert any("小节摘要" in t for t in parent_texts)
    # 文档摘要输入包含顶层章节摘要
    doc_texts = calls[-1][1]
    assert any("章一摘要" in t for t in doc_texts)
    assert outcome.degraded is False


def test_memory_store_alias_subset_replace_preserves_base():
    from knowledge_mining.mining.retrieval_projection.repositories_memory import (
        MemoryRepresentationStore,
    )

    store = MemoryRepresentationStore()
    base = (_rep("prose", canonical="d#seg:0"),)
    asyncio.new_event_loop().run_until_complete(
        store.replace_for_snapshot("s1", base, "proj", document_key="d")
    )
    alias1 = _rep("query_alias", text="Q1", canonical="d#seg:0", ordinal=1)
    asyncio.new_event_loop().run_until_complete(
        store.replace_aliases_for_snapshot(
            "s1", (alias1,), "qe", document_key="d",
        )
    )
    listed = store._by_snapshot["s1"]
    types = [r.representation_type for r in listed]
    assert types.count("prose") == 1 and types.count("query_alias") == 1

    alias2 = _rep("query_alias", text="Q2", canonical="d#seg:0", ordinal=2)
    asyncio.new_event_loop().run_until_complete(
        store.replace_aliases_for_snapshot(
            "s1", (alias2,), "qe", document_key="d",
        )
    )
    final = [r.representation_type for r in store._by_snapshot["s1"]]
    # 基础表示保留；旧别名被新别名替换（不累积）
    assert final.count("prose") == 1
    assert final.count("query_alias") == 1
    assert final.count("summary_alias") == 0
