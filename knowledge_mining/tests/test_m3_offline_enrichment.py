"""M3 契约（批次8，24 号 §5.5/§5.6）：离线 LLM 增强两个可选算子.

- query_expansion_generate：资格门→LLM（SKIP 或 question+answer_span）→
  answer_span 归一化回源校验→query_alias（returnable=False，指回
  canonical）→三层去重；失败/降级不阻断基础资产（query_alias_ready=
  false + degraded 留痕）；
- hierarchical_summary_generate：标题树自底向上 section/document 摘要
  （summary_alias，derived，绑定 source refs）；LLM 失败只 degraded；
- 两算子注册正式目录（7→9），不进入零 LLM 官方基础线预置。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from knowledge_mining.mining.contracts.retrieval_projection import (
    RetrieRepresentation,
)
from knowledge_mining.mining.contracts.segment_compiler import (
    CompiledSegment,
    SegmentElementLink,
)


def _rep(rep_type="prose", *, text="一段足够长的正文内容用于通过资格门审查。" * 6,
         canonical="d#seg:0", ordinal=0):
    return RetrieRepresentation(
        representation_id=f"d:s1:{rep_type}:{ordinal}",
        representation_type=rep_type,
        content_type=rep_type,
        content_text=text,
        structural_context="章一",
        target_type=rep_type if rep_type != "prose" else "segment",
        target_ref=canonical,
        canonical_evidence_id=canonical,
    )


def _segments():
    return (
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


# ---------------------------------------------------------------------------
# 资格门
# ---------------------------------------------------------------------------


def test_eligibility_default_gates_by_type_and_length() -> None:
    from knowledge_mining.mining.retrieval_projection.query_expansion import (
        is_eligible,
    )

    assert is_eligible(_rep("prose", text="足够长的陈述句" * 20)) is True
    # 太短不过
    assert is_eligible(_rep("prose", text="短句")) is False
    # 默认关闭的类型
    assert is_eligible(_rep("section", text="长" * 200)) is False
    assert is_eligible(_rep("code_block", text="long code" * 50)) is False
    # table_row 至少一个 header-value
    row = _rep("table_row", text="告警码为A-101", ordinal=1)
    assert is_eligible(row) is True


# ---------------------------------------------------------------------------
# answer_span 校验 + 去重
# ---------------------------------------------------------------------------


def test_answer_span_must_match_source_normalized() -> None:
    from knowledge_mining.mining.retrieval_projection.query_expansion import (
        validate_answer_span,
    )

    source = "风扇停转时应当先检查 电源 模块。"
    assert validate_answer_span(
        "先检查 电源 模块。", source_text=source,
    ) is True
    # 归一化后不匹配 → 拒绝
    assert validate_answer_span(
        "先检查电源模块", source_text="完全不同的内容",
    ) is False


def test_alias_dedup_within_target_document_and_canonical() -> None:
    from knowledge_mining.mining.retrieval_projection.query_expansion import (
        dedup_aliases,
    )

    def alias(q, canonical="d#seg:0"):
        return SimpleNamespace(
            question=q, canonical_evidence_id=canonical,
            document="d", answer_span_valid=True,
        )

    kept = dedup_aliases([
        alias("问题一"), alias("问题一"),  # target 内去重
        alias("问题二", canonical="d#seg:1"),
        alias("问题一", canonical="d#seg:1"),  # 同问不同源：文档级去重后保留1条
    ])
    questions = [a.question for a in kept]
    assert len(kept) == 2
    assert len(set(questions)) == 2


# ---------------------------------------------------------------------------
# 门面：SKIP / 失败降级 / alias 契约
# ---------------------------------------------------------------------------


class _FakeGenerator:
    def __init__(self, outcomes) -> None:
        self._outcomes = outcomes  # 每 rep 一项：SKIP 或 dict

    def generate_questions(self, items):
        results = []
        for item in items:
            outcome = self._outcomes.pop(0) if self._outcomes else "SKIP"
            results.append(outcome if outcome == "SKIP" else outcome)
        return results


def test_query_expansion_facade_skip_and_alias_contract(tmp_path):
    import asyncio

    from knowledge_mining.mining.retrieval_projection.query_expansion import (
        QueryExpansionFacade,
    )
    from knowledge_mining.mining.retrieval_projection.repositories_memory import (
        MemoryAliasStore,
        MemoryRepresentationStore,
    )

    rep_store, alias_store = MemoryRepresentationStore(), MemoryAliasStore()
    reps = (_rep("prose", canonical="d#seg:0"),
            _rep(
                "prose",
                text=(
                    "风扇停转时应当先检查 电源 模块。检修流程详见维护手册相应章节，"
                    "并确认风扇指示灯状态与告警码的对应关系后再进行更换操作。"
                    "更换前务必断电并佩戴防静电手环，更换后重启设备观察十分钟，"
                    "确认告警消除且风扇转速恢复正常后方可结束本次维护作业流程。"
                ),
                canonical="d#seg:1",
                ordinal=1,
            ))
    asyncio.new_event_loop().run_until_complete(
        rep_store.replace_for_snapshot("s1", reps, "proj", document_key="d")
    )
    generator = _FakeGenerator([
        "SKIP",
        {"question": "风扇停转先查什么？", "answer_span": "先检查 电源 模块。",
         "source_text": "风扇停转时应当先检查 电源 模块。"},
    ])
    facade = QueryExpansionFacade(
        representation_store=rep_store, alias_store=alias_store,
        generator=generator,
    )
    outcome = facade.generate_for_snapshot(snapshot_id="s1", params={})

    # SKIP 计数；span 匹配的 alias 保留并指回源
    assert outcome.skipped == 1
    assert len(outcome.aliases) == 1
    alias = outcome.aliases[0]
    assert alias.representation_type == "query_alias"
    assert alias.returnable is False
    assert alias.canonical_evidence_id == "d#seg:1"
    assert alias.target_ref == "d#seg:1"
    assert outcome.degraded is False


def test_query_expansion_llm_failure_degrades_without_blocking(tmp_path):
    import asyncio

    from knowledge_mining.mining.retrieval_projection.query_expansion import (
        QueryExpansionFacade,
    )
    from knowledge_mining.mining.retrieval_projection.repositories_memory import (
        MemoryAliasStore,
        MemoryRepresentationStore,
    )

    rep_store, alias_store = MemoryRepresentationStore(), MemoryAliasStore()
    reps = (_rep("prose"),)
    asyncio.new_event_loop().run_until_complete(
        rep_store.replace_for_snapshot("s1", reps, "proj", document_key="d")
    )

    class _Boom:
        def generate_questions(self, items):
            raise RuntimeError("llm unavailable")

    facade = QueryExpansionFacade(
        representation_store=rep_store, alias_store=alias_store, generator=_Boom(),
    )
    outcome = facade.generate_for_snapshot(snapshot_id="s1", params={})
    assert outcome.aliases == ()
    assert outcome.degraded is True
    assert outcome.llm_failures == 1


# ---------------------------------------------------------------------------
# hierarchical summary
# ---------------------------------------------------------------------------


def test_hierarchical_summary_bottom_up_with_gates():
    from knowledge_mining.mining.retrieval_projection.summary import (
        HierarchicalSummaryFacade,
    )
    from knowledge_mining.mining.segment_compiler.repositories_memory import (
        MemorySegmentStore,
    )
    import asyncio

    seg_store = MemorySegmentStore()
    asyncio.new_event_loop().run_until_complete(
        seg_store.replace_for_snapshot("s1", _segments(), "segc", document_key="d")
    )

    class _Summarizer:
        def summarize(self, title, texts):
            return f"{title}的摘要"

    facade = HierarchicalSummaryFacade(
        segment_store=seg_store, alias_store=None, summarizer=_Summarizer(),
    )
    outcome = facade.generate_for_snapshot(
        snapshot_id="s1", params={"minSectionTokens": 10},
    )
    aliases = outcome.aliases
    assert aliases, "至少产出 section/document 摘要"
    summary = aliases[0]
    assert summary.representation_type == "summary_alias"
    assert summary.returnable is False
    assert summary.provenance.get("derived") is True
    assert summary.target_type in {"section", "document"}
    assert summary.source_refs  # 绑定 source target


def test_catalog_registers_ninth_operator_pair():
    from knowledge_mining.mining.workflow.operators.catalog import builtin_catalog

    catalog = builtin_catalog()
    assert {
        "query_expansion_generate", "hierarchical_summary_generate",
    } <= set(catalog)
    assert set(catalog) == {
        "input_ingest", "document_parse", "segment_compile",
        "retrieval_unit_project", "embedding", "asset_persist",
        "mining_finalize",
        "query_expansion_generate", "hierarchical_summary_generate",
    }
