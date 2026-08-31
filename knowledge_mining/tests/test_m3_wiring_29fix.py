"""29号 M3 生产接线契约：组合根七件套 + 适配器行为."""
from __future__ import annotations

from types import SimpleNamespace


def test_build_new_chain_services_wires_m3_when_llm_configured():
    from knowledge_mining.mining.workflow.new_chain_services import (
        build_new_chain_services,
    )

    services = build_new_chain_services(
        bucket_prefix="test-",
        llm_generator=SimpleNamespace(execute=lambda *a, **k: "x"),
    )
    assert services.query_expansion_service is not None
    assert services.hierarchical_summary_service is not None
    # 未配置 llm：None → handler FALLBACK degraded（基础资产不受影响）
    bare = build_new_chain_services(bucket_prefix="test-")
    assert bare.query_expansion_service is None
    assert bare.hierarchical_summary_service is None


def test_question_generator_skip_and_parse_paths():
    from knowledge_mining.mining.retrieval_projection.llm_generation import (
        LLMQuestionGenerator,
    )

    class _Client:
        def __init__(self):
            self.calls = 0

        def execute(self, messages, *, expected_output_type, **kwargs):
            self.calls += 1
            if expected_output_type != "json_object":
                raise AssertionError("questions must request json_object")
            return {"question": "风扇坏了先查什么？", "answer_span": "先查电源"}

    client = _Client()
    gen = LLMQuestionGenerator(client)
    out = gen.generate_questions([{"text": "风扇停转时先查电源。"}])
    assert out == [{"question": "风扇坏了先查什么？", "answer_span": "先查电源"}]

    class _Boom:
        def execute(self, *a, **k):
            raise RuntimeError("llm down")

    out2 = LLMQuestionGenerator(_Boom()).generate_questions(
        [{"text": "正文"}],
    )
    assert out2 == ["SKIP"]  # 单项失败按 SKIP，不抛

    # 成本护栏：超 max_items 的项直接 SKIP 不调用
    out3 = LLMQuestionGenerator(_Client(), max_items=1).generate_questions(
        [{"text": "a"}, {"text": "b"}],
    )
    assert out3[1] == "SKIP"


def test_summarizer_requests_text_output():
    from knowledge_mining.mining.retrieval_projection.llm_generation import (
        LLMSummarizer,
    )

    class _Client:
        def execute(self, messages, *, expected_output_type, **kwargs):
            assert expected_output_type == "text"
            return "  两句摘要。 "

    out = LLMSummarizer(_Client()).summarize("章一", ["片段一", "片段二"])
    assert out == "两句摘要。"
