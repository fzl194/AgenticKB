"""Tests for mining pipeline bug fixes (Phase 1), hot-pluggable architecture (Phase 2),
and new operators (Wave 1-7: Embedding, Discourse Relations, Contextual Retrieval, etc.)."""
from __future__ import annotations

import json
import pytest

from knowledge_mining.mining.contracts.models import (
    ContentBlock,
    DocumentProfile,
    RawSegmentData,
    RetrievalUnitData,
    SectionNode,
)


# ===================================================================
# Phase 1: Bug Fix Tests
# ===================================================================

class TestNestedListParsing:
    """Bug 1: Structure parser should preserve nested list items."""

    def test_nested_list_items_preserved(self):
        """Nested list sub-items should appear in items_nested."""
        from knowledge_mining.mining.infra.structure import parse_structure

        md = """## Steps

1. Configure Flowfilter
   - Sub-item A
   - Sub-item B
2. Set policy
   - Sub-item C
3. Apply rule
"""
        tree = parse_structure(md)
        blocks = _collect_blocks(tree)
        list_blocks = [b for b in blocks if b.block_type == "list"]
        assert len(list_blocks) >= 1

        main_list = list_blocks[0]
        struct = main_list.structure
        assert struct is not None

        # Flat items should only contain depth-1 items
        assert "Configure Flowfilter" in struct["items"]
        assert "Set policy" in struct["items"]
        assert "Apply rule" in struct["items"]

        # Nested items should include all depths
        items_nested = struct.get("items_nested", [])
        nested_texts = [it["text"] for it in items_nested]
        assert "Sub-item A" in nested_texts, f"Sub-item A missing from {nested_texts}"
        assert "Sub-item B" in nested_texts
        assert "Sub-item C" in nested_texts

    def test_nested_list_hierarchical_text(self):
        """ContentBlock.text should contain indented hierarchical text."""
        from knowledge_mining.mining.infra.structure import parse_structure

        md = """## Steps

1. Step one
   - Detail A
   - Detail B
2. Step two
"""
        tree = parse_structure(md)
        blocks = _collect_blocks(tree)
        list_blocks = [b for b in blocks if b.block_type == "list"]
        assert len(list_blocks) >= 1

        text = list_blocks[0].text
        # Top-level items should have "1." prefix
        assert "1. Step one" in text
        assert "2. Step two" in text
        # Sub-items should be indented with "  -"
        assert "  - Detail A" in text or "- Detail A" in text

    def test_backward_compat_flat_items(self):
        """items field should still work with only depth-1 items."""
        from knowledge_mining.mining.infra.structure import parse_structure

        md = """- Apple
- Banana
- Cherry
"""
        tree = parse_structure(md)
        blocks = _collect_blocks(tree)
        list_blocks = [b for b in blocks if b.block_type == "list"]
        assert len(list_blocks) >= 1

        struct = list_blocks[0].structure
        assert struct["items"] == ["Apple", "Banana", "Cherry"]
        # items_nested should have same items with depth=1
        assert all(it["depth"] == 1 for it in struct["items_nested"])


class TestGeneratedQuestionDifferentiation:
    """Bug 2: generated_question title/text/search_text should differ."""

    def test_question_unit_fields_differ(self):
        from knowledge_mining.mining.stages.retrieval_units import _make_generated_question_unit

        seg = RawSegmentData(
            document_key="doc:/test.md",
            segment_index=5,
            block_type="paragraph",
            section_title="参数说明",
            raw_text="这是一个关于网络配置参数的详细说明段落，包含多个关键参数。",
            section_path=[{"title": "参数说明", "level": 2}],
        )

        unit = _make_generated_question_unit(seg, "如何配置网络参数？", 0)

        # v1.5: title should be pure question text (no Qn prefix)
        assert unit.title.startswith("如何配置网络参数")
        assert not unit.title.startswith("Q")

        # text should include source context
        assert "如何配置网络参数？" in unit.text
        assert "来源" in unit.text
        assert "参数说明" in unit.text

        # search_text should include section title (tokenized)
        # jieba may split "参数说明" into tokens, so check for presence of key chars
        assert "参数" in unit.search_text or "参数说明" in unit.search_text

        # All three should be different
        assert unit.title != unit.text
        assert unit.text != unit.search_text
        assert unit.title != unit.search_text

    def test_question_unit_second_index(self):
        from knowledge_mining.mining.stages.retrieval_units import _make_generated_question_unit

        seg = RawSegmentData(
            document_key="doc:/test.md",
            segment_index=0,
            block_type="paragraph",
            raw_text="test content",
        )
        unit = _make_generated_question_unit(seg, "What is this?", 1)
        # v1.5: title should be pure question text (no Qn prefix)
        assert unit.title.startswith("What is this?")
        assert not unit.title.startswith("Q")


class TestQuestionGenerationFilter:
    """Bug 3: heading-only and very short segments should not generate questions."""

    def test_heading_segments_filtered(self):
        from knowledge_mining.mining.stages.retrieval_units import _is_questionworthy

        heading_seg = RawSegmentData(
            document_key="doc:/test.md",
            segment_index=0,
            block_type="heading",
            raw_text="参数说明",
        )
        assert _is_questionworthy(heading_seg) is False

    def test_short_segments_filtered(self):
        from knowledge_mining.mining.stages.retrieval_units import _is_questionworthy

        short_seg = RawSegmentData(
            document_key="doc:/test.md",
            segment_index=0,
            block_type="paragraph",
            raw_text="too short",  # < 15 chars
        )
        assert _is_questionworthy(short_seg) is False

    def test_low_token_segments_filtered(self):
        from knowledge_mining.mining.stages.retrieval_units import _is_questionworthy

        low_token_seg = RawSegmentData(
            document_key="doc:/test.md",
            segment_index=0,
            block_type="paragraph",
            raw_text="some content that is long enough",
            token_count=5,  # < 10
        )
        assert _is_questionworthy(low_token_seg) is False

    def test_normal_segments_pass(self):
        from knowledge_mining.mining.stages.retrieval_units import _is_questionworthy
        from knowledge_mining.mining.infra.domain_pack import RetrievalPolicy

        # Use a low-token policy so token_count=15 passes the gate
        low_token_policy = RetrievalPolicy(min_questionworthy_tokens=10)

        good_seg = RawSegmentData(
            document_key="doc:/test.md",
            segment_index=0,
            block_type="paragraph",
            raw_text="This is a normal paragraph with enough content to be considered question-worthy.",
            token_count=15,
            semantic_role="concept",
        )
        assert _is_questionworthy(good_seg, low_token_policy) is True

        # unknown role should pass through (not in not_questionworthy_roles)
        unknown_seg = RawSegmentData(
            document_key="doc:/test.md",
            segment_index=0,
            block_type="paragraph",
            raw_text="This is a normal paragraph with enough content to be considered question-worthy.",
            token_count=15,
        )
        assert _is_questionworthy(unknown_seg, low_token_policy) is True

    def test_filter_in_build_retrieval_units(self):
        """Verify heading segments are not sent to question generator."""
        from knowledge_mining.mining.stages.retrieval_units import build_retrieval_units
        from knowledge_mining.mining.infra.domain_pack import DomainProfile, RetrievalPolicy

        # Use a low-token policy so token_count=15 passes the gate
        policy = RetrievalPolicy(min_questionworthy_tokens=10)
        profile = DomainProfile(
            domain_id="test",
            display_name="Test",
            entity_types=frozenset(),
            strong_entity_types=frozenset(),
            role_keyword_rules=(),
            heading_role_keywords=(),
            extractor_rules=(),
            llm_templates=(),
            semantic_roles=frozenset(),
            retrieval_policy=policy,
            document_types=frozenset(),
            eval_questions=(),
        )

        segments = [
            RawSegmentData(
                document_key="doc:/test.md",
                segment_index=0,
                block_type="heading",
                raw_text="Title Only",
                section_title="Title Only",
            ),
            RawSegmentData(
                document_key="doc:/test.md",
                segment_index=1,
                block_type="paragraph",
                raw_text="This is a substantial paragraph with enough content to generate questions from.",
                token_count=15,
                section_title="Title Only",
                semantic_role="concept",
            ),
        ]

        # Mock question generator to track which segments it receives
        received_keys: list[str] = []

        class MockQGen:
            def generate(self, segment):
                return ["Q?"]

            def generate_batch(self, segments):
                for s in segments:
                    received_keys.append(f"{s.document_key}#{s.segment_index}")
                return {f"{s.document_key}#{s.segment_index}": ["Generated Q?"] for s in segments}

        units = build_retrieval_units(
            segments,
            document_key="doc:/test.md",
            question_generator=MockQGen(),
            profile=profile,
        )

        # Only the paragraph should have received questions
        assert "doc:/test.md#0" not in received_keys, "Heading segment should not be sent to QGen"
        assert "doc:/test.md#1" in received_keys


class TestContextualTextImprovements:
    """v1.3: Section context is folded into raw_text.search_text (not separate unit)."""

    def test_section_context_in_search_text(self):
        """Section titles not in raw_text should appear in search_text."""
        from knowledge_mining.mining.stages.retrieval_units import _make_raw_text_unit

        seg = RawSegmentData(
            document_key="doc:/test.md",
            segment_index=0,
            block_type="paragraph",
            raw_text="This is content about configuration.",
            section_path=[{"title": "参数说明", "level": 2}, {"title": "配置步骤", "level": 3}],
            section_title="配置步骤",
        )

        unit = _make_raw_text_unit(seg)
        # search_text should contain the section title context (tokenized by jieba)
        # "参数说明" is tokenized as "参数" + "说明", "配置步骤" as "配置" + "步骤"
        assert "参数" in unit.search_text and "说明" in unit.search_text
        assert "配置" in unit.search_text and "步骤" in unit.search_text
        # text should be unchanged raw_text
        assert unit.text == "This is content about configuration."

    def test_heading_section_context_in_search_text(self):
        """Headings still get raw_text units with section context in search_text."""
        from knowledge_mining.mining.stages.retrieval_units import _make_raw_text_unit

        seg = RawSegmentData(
            document_key="doc:/test.md",
            segment_index=0,
            block_type="heading",
            raw_text="Title",
            section_title="Title",
        )

        unit = _make_raw_text_unit(seg)
        assert unit.unit_type == "raw_text"
        assert unit.text == "Title"


class TestTableRowUnits:
    """Bug 1 supplement: table segments should produce per-row retrieval units."""

    def test_table_row_units_generated(self):
        from knowledge_mining.mining.stages.retrieval_units import _make_table_row_units

        seg = RawSegmentData(
            document_key="doc:/test.md",
            segment_index=0,
            block_type="table",
            raw_text="table text",
            structure_json={
                "columns": ["用户类型", "业务类型", "业务需求"],
                "rows": [
                    {"用户类型": "普通用户", "业务类型": "视频业务", "业务需求": "高带宽"},
                    {"用户类型": "企业用户", "业务类型": "数据业务", "业务需求": "低延迟"},
                ],
            },
        )

        units = _make_table_row_units(seg)
        assert len(units) == 2

        # First row
        u0 = units[0]
        assert u0.unit_type == "table_row"
        assert "用户类型为普通用户" in u0.text
        assert "业务类型为视频业务" in u0.text
        assert u0.weight == 0.8

        # Second row
        u1 = units[1]
        assert "企业用户" in u1.text
        assert "低延迟" in u1.text

    def test_table_row_units_in_build(self):
        from knowledge_mining.mining.stages.retrieval_units import build_retrieval_units
        from knowledge_mining.mining.infra.domain_pack import RetrievalPolicy, DomainProfile

        # Use a profile with table_row enabled
        policy = RetrievalPolicy(table_row="structured_tables")
        profile = DomainProfile(
            domain_id="test", display_name="Test",
            entity_types=frozenset(), strong_entity_types=frozenset(),
            role_keyword_rules=(), heading_role_keywords=(),
            extractor_rules=(), llm_templates=(),
            semantic_roles=frozenset(),
            retrieval_policy=policy, document_types=frozenset(),
            eval_questions=(),
        )

        seg = RawSegmentData(
            document_key="doc:/test.md",
            segment_index=0,
            block_type="table",
            raw_text="table text",
            structure_json={
                "columns": ["A", "B"],
                "rows": [{"A": "a1", "B": "b1"}],
            },
        )

        units = build_retrieval_units([seg], document_key="doc:/test.md", profile=profile)
        table_rows = [u for u in units if u.unit_type == "table_row"]
        assert len(table_rows) == 1

    def test_non_table_produces_no_row_units(self):
        from knowledge_mining.mining.stages.retrieval_units import _make_table_row_units

        seg = RawSegmentData(
            document_key="doc:/test.md",
            segment_index=0,
            block_type="paragraph",
            raw_text="not a table",
        )
        assert _make_table_row_units(seg) == []


# ===================================================================
# Phase 2: Pipeline Architecture Tests
# ===================================================================

class TestDocumentContext:
    """DocumentContext should be immutable and support with_updates."""

    def test_immutable(self):
        from knowledge_mining.mining.pipeline import DocumentContext

        ctx = DocumentContext()
        with pytest.raises(AttributeError):
            ctx.segments = ()  # type: ignore

    def test_with_updates(self):
        from knowledge_mining.mining.pipeline import DocumentContext

        ctx = DocumentContext()
        seg = RawSegmentData(document_key="doc:/test.md", segment_index=0)
        ctx2 = ctx.with_updates(segments=(seg,))
        assert len(ctx2.segments) == 1
        assert len(ctx.segments) == 0  # original unchanged


class TestPipelineConfig:
    """PipelineConfig should accept pluggable operators."""

    def test_default_config(self):
        from knowledge_mining.mining.pipeline import PipelineConfig

        config = PipelineConfig(domain="test-domain")
        assert config.domain == "test-domain"
        assert config.segmenter is None
        assert config.enricher is None

    def test_domain_is_required(self):
        from knowledge_mining.mining.pipeline import PipelineConfig

        with pytest.raises(TypeError):
            PipelineConfig()

    def test_custom_segmenter(self):
        from knowledge_mining.mining.pipeline import PipelineConfig

        class CustomSegmenter:
            def segment(self, tree, profile, **kwargs):
                return []

        config = PipelineConfig(domain="test-domain", segmenter=CustomSegmenter())
        assert isinstance(config.segmenter, CustomSegmenter)


class TestDefaultSegmenter:
    """DefaultSegmenter should wrap segment_document."""

    def test_delegates_to_segment_document(self):
        from knowledge_mining.mining.stages.segment import DefaultSegmenter
        from knowledge_mining.mining.infra.structure import parse_structure

        md = "# Title\n\nParagraph content here.\n"
        tree = parse_structure(md)
        profile = DocumentProfile(document_key="doc:/test.md")

        segmenter = DefaultSegmenter()
        segments = segmenter.segment(tree, profile)
        assert len(segments) > 0
        assert all(isinstance(s, RawSegmentData) for s in segments)


# REMOVED: TestDefaultRelationBuilder - rule-based components deleted
# REMOVED: TestMiningPipeline - all tests depended on removed rule-based components


class TestLlmTemplates:
    """Verify new template is registered."""

    def test_segment_understanding_template_exists(self):
        from knowledge_mining.mining.infra.llm_templates import load_templates

        templates = load_templates("civil_engineering")
        keys = [t["template_key"] for t in templates]
        assert "mining-segment-understanding" in keys

    def test_segment_understanding_template_structure(self):
        from knowledge_mining.mining.infra.llm_templates import load_templates

        templates = load_templates("civil_engineering")
        tpl = next(t for t in templates if t["template_key"] == "mining-segment-understanding")
        assert tpl["expected_output_type"] == "json_object"
        # 实体抽取已拆到 mining-entity-extraction（L4 §15），此处只剩篇章字段
        assert "semantic_role" in tpl["user_prompt_template"]
        assert "content_assessment" in tpl["user_prompt_template"]

    def test_entity_extraction_template_exists(self):
        from knowledge_mining.mining.infra.llm_templates import load_templates

        templates = load_templates("civil_engineering")
        tpl = next(t for t in templates if t["template_key"] == "mining-entity-extraction")
        assert tpl["expected_output_type"] == "json_object"
        assert "$allowed_types" in tpl["user_prompt_template"]
        assert "out_of_schema" in tpl["user_prompt_template"]


# ===================================================================
# Helpers
# ===================================================================

def _collect_blocks(node: SectionNode) -> list[ContentBlock]:
    blocks = list(node.blocks)
    for child in node.children:
        blocks.extend(_collect_blocks(child))
    return blocks


# ===================================================================
# Wave 1: EmbeddingGenerator Tests
# ===================================================================

class TestLLMServiceEmbeddingGenerator:
    """LLMServiceEmbeddingGenerator should call llm_service and return embeddings."""

    def test_embed_single_text(self):
        from knowledge_mining.mining.infra.embedding import LLMServiceEmbeddingGenerator
        from unittest.mock import patch, MagicMock

        gen = LLMServiceEmbeddingGenerator(base_url="http://localhost:8900")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = gen.embed(["test text"])
            assert len(result) == 1
            assert result[0] == [0.1, 0.2, 0.3]

    def test_embed_empty_input(self):
        from knowledge_mining.mining.infra.embedding import LLMServiceEmbeddingGenerator

        gen = LLMServiceEmbeddingGenerator(base_url="http://localhost:8900")
        assert gen.embed([]) == []

    def test_embed_api_failure_returns_empty(self):
        from knowledge_mining.mining.infra.embedding import LLMServiceEmbeddingGenerator
        from unittest.mock import patch, MagicMock

        gen = LLMServiceEmbeddingGenerator(base_url="http://localhost:8900")

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = Exception("API error")
            mock_client_cls.return_value = mock_client

            result = gen.embed(["test"])
            assert result == []

    def test_embed_batch(self):
        from knowledge_mining.mining.infra.embedding import LLMServiceEmbeddingGenerator
        from unittest.mock import patch, MagicMock

        gen = LLMServiceEmbeddingGenerator(base_url="http://localhost:8900")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1, 0.2], "index": 0},
                {"embedding": [0.3, 0.4], "index": 1},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = gen.embed_batch(["text1", "text2"], batch_size=10)
            assert len(result) == 2

    def test_noop_embedding_generator(self):
        from knowledge_mining.mining.infra.embedding import NoOpEmbeddingGenerator

        gen = NoOpEmbeddingGenerator()
        assert gen.embed(["test"]) == []
        assert gen.embed_batch(["test"]) == []


# ===================================================================
# Wave 2: DiscourseRelationBuilder Tests
# ===================================================================

class TestDiscourseRelationBuilder:
    """DiscourseRelationBuilder should analyze segment discourse relations via LLM."""

    def test_parse_llm_results(self):
        from knowledge_mining.mining.stages.relations import DiscourseRelationBuilder

        builder = DiscourseRelationBuilder.__new__(DiscourseRelationBuilder)
        builder._client = None
        builder._timeout = 60
        builder._window_size = 15

        segments = [
            RawSegmentData(document_key="doc:/test.md", segment_index=0, raw_text="First segment about config."),
            RawSegmentData(document_key="doc:/test.md", segment_index=1, raw_text="Second segment about results."),
            RawSegmentData(document_key="doc:/test.md", segment_index=2, raw_text="Third segment about testing."),
        ]

        llm_output = [
            {"source": 0, "target": 1, "relation": "ELABORATES", "confidence": 0.9},
            {"source": 1, "target": 2, "relation": "CAUSES", "confidence": 0.7},
        ]

        relations = builder._parse_llm_results(llm_output, segments)
        assert len(relations) == 2

        assert relations[0].relation_type == "elaborates"
        assert relations[0].weight == 0.9
        assert relations[0].metadata_json["source"] == "discourse_llm"

        assert relations[1].relation_type == "causes"

    def test_unrelated_filtered_out(self):
        from knowledge_mining.mining.stages.relations import DiscourseRelationBuilder

        builder = DiscourseRelationBuilder.__new__(DiscourseRelationBuilder)
        builder._client = None

        segments = [
            RawSegmentData(document_key="doc:/test.md", segment_index=0, raw_text="A"),
            RawSegmentData(document_key="doc:/test.md", segment_index=1, raw_text="B"),
        ]

        llm_output = [
            {"source": 0, "target": 1, "relation": "UNRELATED", "confidence": 0.3},
            {"source": 0, "target": 1, "relation": "ELABORATES", "confidence": 0.8},
        ]

        relations = builder._parse_llm_results(llm_output, segments)
        assert len(relations) == 1
        assert relations[0].relation_type == "elaborates"

    def test_out_of_range_index_skipped(self):
        from knowledge_mining.mining.stages.relations import DiscourseRelationBuilder

        builder = DiscourseRelationBuilder.__new__(DiscourseRelationBuilder)
        builder._client = None

        segments = [RawSegmentData(document_key="doc:/test.md", segment_index=0, raw_text="A")]

        llm_output = [
            {"source": 0, "target": 5, "relation": "ELABORATES", "confidence": 0.9},
        ]

        relations = builder._parse_llm_results(llm_output, segments)
        assert len(relations) == 0

    def test_build_with_too_few_segments(self):
        from knowledge_mining.mining.stages.relations import DiscourseRelationBuilder

        builder = DiscourseRelationBuilder.__new__(DiscourseRelationBuilder)
        builder._client = None
        builder._timeout = 60
        builder._window_size = 15

        segments = [RawSegmentData(document_key="doc:/test.md", segment_index=0, raw_text="Only one")]
        result = builder.build(segments)
        assert result == []


# ===================================================================
# Wave 3: ContextualRetriever Tests
# ===================================================================

class TestContextualizer:
    """Contextualizer should generate context descriptions for segments."""

    # REMOVED: test_noop_contextualizer - NoOpContextualizer deleted

    def test_raw_text_unit_with_llm_context(self):
        """v1.3: LLM context is folded into raw_text.search_text and metadata."""
        from knowledge_mining.mining.stages.retrieval_units import _make_raw_text_unit

        seg = RawSegmentData(
            document_key="doc:/test.md",
            segment_index=1,
            block_type="paragraph",
            raw_text="APN配置需要设置正确的参数。",
            section_title="APN配置",
            section_path=[{"title": "APN配置", "level": 2}],
        )

        unit = _make_raw_text_unit(seg, llm_context="本段介绍APN的基本配置步骤", llm_task_id="task-ctx-123")
        assert unit.unit_type == "raw_text"
        assert unit.weight == 1.0
        # text is the original raw_text (for generation context)
        assert "APN配置需要设置正确的参数" in unit.text
        # search_text contains the LLM context (tokenized by jieba)
        assert "APN" in unit.search_text
        assert "配置" in unit.search_text
        assert unit.metadata_json["context_description"] == "本段介绍APN的基本配置步骤"
        assert unit.llm_result_refs_json["source"] == "contextual_retrieval"
        assert unit.llm_result_refs_json["task_id"] == "task-ctx-123"

    def test_contextualizer_in_build_retrieval_units(self):
        """v1.3: contextualizer enriches raw_text.search_text, no separate unit."""
        from knowledge_mining.mining.stages.retrieval_units import build_retrieval_units
        from knowledge_mining.mining.infra.domain_pack import RetrievalPolicy, DomainProfile

        # Use a profile with contextual_retrieval enabled
        policy = RetrievalPolicy(contextual_retrieval="on")
        profile = DomainProfile(
            domain_id="test", display_name="Test",
            entity_types=frozenset(), strong_entity_types=frozenset(),
            role_keyword_rules=(), heading_role_keywords=(),
            extractor_rules=(), llm_templates=(),
            semantic_roles=frozenset(),
            retrieval_policy=policy, document_types=frozenset(),
            eval_questions=(),
        )

        segments = [
            RawSegmentData(
                document_key="doc:/test.md",
                segment_index=0,
                block_type="paragraph",
                raw_text="This is a test paragraph.",
            ),
        ]

        class MockContextualizer:
            def contextualize(self, segments, document_text):
                return {f"{s.document_key}#{s.segment_index}": "Test context" for s in segments}

        units = build_retrieval_units(
            segments,
            document_key="doc:/test.md",
            contextualizer=MockContextualizer(),
            profile=profile,
        )

        # v1.3: no contextual_enhanced units, LLM context goes into raw_text
        raw_units = [u for u in units if u.unit_type == "raw_text"]
        assert len(raw_units) == 1
        assert "Test" in raw_units[0].search_text
        assert "context" in raw_units[0].search_text
        assert raw_units[0].metadata_json.get("context_description") == "Test context"
        # No separate contextual_text units
        assert not any(u.unit_key.endswith(":contextual_enhanced") for u in units)


# ===================================================================
# Wave 4-5: validate_build + REMOVE semantics Tests
# ===================================================================

class TestValidateBuild:
    """validate_build should check active snapshots, segments, and parent build."""

    def test_validate_build_no_active_snapshots(self):
        from knowledge_mining.mining.stages.publishing import validate_build
        from knowledge_mining.mining.infra.db import AssetCoreDB
        from unittest.mock import MagicMock

        db = MagicMock(spec=AssetCoreDB)
        db.get_build.return_value = {"build_mode": "full", "parent_build_id": None}
        db.get_build_snapshots.return_value = []

        with pytest.raises(ValueError, match="no active snapshots"):
            validate_build(db, "build-1")

    def test_validate_build_empty_snapshot(self):
        from knowledge_mining.mining.stages.publishing import validate_build
        from knowledge_mining.mining.infra.db import AssetCoreDB
        from unittest.mock import MagicMock

        db = MagicMock(spec=AssetCoreDB)
        db.get_build.return_value = {"build_mode": "full", "parent_build_id": None}
        db.get_build_snapshots.return_value = [
            {"selection_status": "active", "document_snapshot_id": "snap-1"},
        ]
        db.count_segments_by_snapshot.return_value = 0

        with pytest.raises(ValueError, match="no segments"):
            validate_build(db, "build-1")

    def test_validate_build_incremental_missing_parent(self):
        from knowledge_mining.mining.stages.publishing import validate_build
        from knowledge_mining.mining.infra.db import AssetCoreDB
        from unittest.mock import MagicMock

        db = MagicMock(spec=AssetCoreDB)
        db.get_build.return_value = {
            "build_mode": "incremental",
            "parent_build_id": "parent-missing",
        }
        db.get_build.return_value = {
            "build_mode": "incremental",
            "parent_build_id": "parent-missing",
        }

        # First call for the build itself
        build_data = {
            "build_mode": "incremental",
            "parent_build_id": "parent-missing",
        }
        db.get_build.side_effect = [build_data, None]  # build found, parent not

        with pytest.raises(ValueError, match="missing parent"):
            validate_build(db, "build-1")

    def test_validate_build_passes(self):
        from knowledge_mining.mining.stages.publishing import validate_build
        from knowledge_mining.mining.infra.db import AssetCoreDB
        from unittest.mock import MagicMock

        db = MagicMock(spec=AssetCoreDB)
        db.get_build.return_value = {"build_mode": "full", "parent_build_id": None}
        db.get_build_snapshots.return_value = [
            {"selection_status": "active", "document_snapshot_id": "snap-1"},
        ]
        db.count_segments_by_snapshot.return_value = 5

        # Should not raise
        validate_build(db, "build-1")


class TestRemoveSemantics:
    """classify_documents should detect REMOVE for deleted files."""

    def test_removed_document_detected(self):
        from knowledge_mining.mining.stages.publishing import classify_documents
        from knowledge_mining.mining.infra.db import AssetCoreDB
        from unittest.mock import MagicMock

        db = MagicMock(spec=AssetCoreDB)
        db.get_active_build.return_value = {"id": "prev-build-1"}
        db.get_build_snapshots.return_value = [
            {"document_id": "doc-1", "document_snapshot_id": "snap-1", "selection_status": "active"},
            {"document_id": "doc-2", "document_snapshot_id": "snap-2", "selection_status": "active"},
        ]

        # Current run only has doc-1, doc-2 was deleted
        decisions = [
            {"document_id": "doc-1", "document_snapshot_id": "snap-1-new"},
        ]

        result = classify_documents(
            db, decisions, domain="test-domain", channel="prod"
        )
        remove_decisions = [d for d in result if d.get("action") == "REMOVE"]
        assert len(remove_decisions) == 1
        assert remove_decisions[0]["document_id"] == "doc-2"
        assert remove_decisions[0]["selection_status"] == "removed"


# ===================================================================
# Wave 6: Counting Tests
# ===================================================================

class TestRunCounting:
    """run() should track new_count and updated_count separately."""

    def test_pipeline_config_accepts_new_operators(self):
        from knowledge_mining.mining.pipeline import PipelineConfig

        config = PipelineConfig(
            domain="test-domain",
            embedding_generator=None,
            discourse_relation_builder=None,
            contextualizer=None,
        )
        assert config.embedding_generator is None
        assert config.discourse_relation_builder is None
        assert config.contextualizer is None


# ===================================================================
# Wave 7: html_table Structure Extraction Tests
# ===================================================================

class TestHtmlTableExtraction:
    """html_table blocks should have columns/rows structure extracted."""

    def test_html_table_structure(self):
        from knowledge_mining.mining.infra.structure import _parse_html_table

        html = """<table>
        <thead><tr><th>参数</th><th>值</th><th>说明</th></tr></thead>
        <tbody>
            <tr><td>APN</td><td>cmnet</td><td>接入点名称</td></tr>
            <tr><td>Auth</td><td>PAP</td><td>认证方式</td></tr>
        </tbody>
        </table>"""

        structure = _parse_html_table(html)
        assert structure["kind"] == "html_table"
        assert structure["columns"] == ["参数", "值", "说明"]
        assert len(structure["rows"]) == 2
        assert structure["rows"][0]["参数"] == "APN"
        assert structure["rows"][1]["说明"] == "认证方式"
        assert structure["row_count"] == 2
        assert structure["col_count"] == 3

    def test_html_table_no_header(self):
        from knowledge_mining.mining.infra.structure import _parse_html_table

        html = """<table>
        <tr><td>A</td><td>B</td></tr>
        <tr><td>C</td><td>D</td></tr>
        </table>"""

        structure = _parse_html_table(html)
        assert structure["col_count"] == 0  # no thead
        assert structure["row_count"] == 2

    def test_html_table_in_structure_parser(self):
        from knowledge_mining.mining.infra.structure import parse_structure

        md = """# Test

<table>
<thead><tr><th>Col1</th><th>Col2</th></tr></thead>
<tbody><tr><td>Val1</td><td>Val2</td></tr></tbody>
</table>
"""

        tree = parse_structure(md)
        blocks = _collect_blocks(tree)
        html_tables = [b for b in blocks if b.block_type == "html_table"]
        assert len(html_tables) == 1

        struct = html_tables[0].structure
        assert struct is not None
        assert struct["kind"] == "html_table"
        assert struct["columns"] == ["Col1", "Col2"]


# ===================================================================
# Wave 2: RST Relation Types in Models
# ===================================================================

class TestRstRelationTypes:
    """VALID_RELATION_TYPES should include RST discourse labels."""

    def test_rst_labels_present(self):
        from knowledge_mining.mining.contracts.models import VALID_RELATION_TYPES
        from knowledge_mining.mining.contracts.rst_relations import RST_DB_VALUES

        for label in RST_DB_VALUES:
            assert label in VALID_RELATION_TYPES, f"{label} missing from VALID_RELATION_TYPES"
        assert "unrelated" in VALID_RELATION_TYPES

    def test_structural_labels_still_present(self):
        from knowledge_mining.mining.contracts.models import VALID_RELATION_TYPES

        structural = {"previous", "next", "same_section", "same_parent_section", "section_header_of"}
        for label in structural:
            assert label in VALID_RELATION_TYPES

    def test_discourse_relations_stage_name(self):
        from knowledge_mining.mining.contracts.models import VALID_STAGE_NAMES

        assert "discourse" in VALID_STAGE_NAMES
        assert "discourse_relations" in VALID_STAGE_NAMES

    def test_new_stage_names_present(self):
        from knowledge_mining.mining.contracts.models import VALID_STAGE_NAMES

        for name in ("embedding", "commit_segments", "db_write", "retrieval_units"):
            assert name in VALID_STAGE_NAMES, f"{name} missing from VALID_STAGE_NAMES"


# ===================================================================
# Wave 1-3: embedding_stage + db_write_stage tests
# ===================================================================

class TestEmbeddingStage:
    """embedding_stage should generate embeddings from retrieval units."""

    def test_no_embedding_generator(self):
        from knowledge_mining.mining.pipeline import embedding_stage, DocumentContext, PipelineConfig

        cfg = PipelineConfig(domain="test-domain", embedding_generator=None)
        ctx = DocumentContext()
        result = embedding_stage(ctx, cfg)
        assert result.embeddings == ()

    def test_no_retrieval_units(self):
        from knowledge_mining.mining.pipeline import embedding_stage, DocumentContext, PipelineConfig

        class MockGen:
            model_name = "test"

        cfg = PipelineConfig(domain="test-domain", embedding_generator=MockGen())
        ctx = DocumentContext()
        result = embedding_stage(ctx, cfg)
        assert result.embeddings == ()

    def test_embedding_success(self):
        from unittest.mock import MagicMock
        from knowledge_mining.mining.pipeline import embedding_stage, DocumentContext, PipelineConfig
        from knowledge_mining.mining.contracts.models import RetrievalUnitData

        gen = MagicMock()
        gen.embed_batch.return_value = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        cfg = PipelineConfig(domain="test-domain", embedding_generator=gen)

        units = (
            RetrievalUnitData(
                segment_key="doc#0", unit_key="ru:doc#0:raw_text", unit_type="raw_text",
                target_type="raw_segment", target_ref_json={}, title=None,
                text="hello", search_text="hello", block_type="paragraph",
                semantic_role="concept", facets_json={}, entity_refs_json=[],
                source_refs_json={}, llm_result_refs_json={}, source_segment_id="s1",
                weight=1.0, metadata_json={},
            ),
            RetrievalUnitData(
                segment_key="doc#1", unit_key="ru:doc#1:raw_text", unit_type="raw_text",
                target_type="raw_segment", target_ref_json={}, title=None,
                text="world", search_text="world", block_type="paragraph",
                semantic_role="concept", facets_json={}, entity_refs_json=[],
                source_refs_json={}, llm_result_refs_json={}, source_segment_id="s2",
                weight=1.0, metadata_json={},
            ),
        )
        ctx = DocumentContext(retrieval_units=units)
        result = embedding_stage(ctx, cfg)
        assert len(result.embeddings) == 2
        assert result.embeddings[0]["unit_key"] == "ru:doc#0:raw_text"
        assert result.embeddings[1]["unit_key"] == "ru:doc#1:raw_text"

    def test_embedding_failure_graceful(self):
        from unittest.mock import MagicMock
        from knowledge_mining.mining.pipeline import embedding_stage, DocumentContext, PipelineConfig
        from knowledge_mining.mining.contracts.models import RetrievalUnitData

        gen = MagicMock()
        gen.embed_batch.side_effect = RuntimeError("API down")
        cfg = PipelineConfig(domain="test-domain", embedding_generator=gen)

        units = (
            RetrievalUnitData(
                segment_key="doc#0", unit_key="ru:doc#0:raw_text", unit_type="raw_text",
                target_type="raw_segment", target_ref_json={}, title=None,
                text="hello", search_text="hello", block_type="paragraph",
                semantic_role="concept", facets_json={}, entity_refs_json=[],
                source_refs_json={}, llm_result_refs_json={}, source_segment_id="s1",
                weight=1.0, metadata_json={},
            ),
        )
        ctx = DocumentContext(retrieval_units=units)
        result = embedding_stage(ctx, cfg)
        assert result.embeddings == ()
        assert result.error is None  # embedding failure is non-fatal


class TestDbWriteStageSkip:
    """db_write_stage should skip errored and treeless contexts."""

    def test_skip_error_context(self):
        from unittest.mock import MagicMock
        from knowledge_mining.mining.pipeline import db_write_stage, DocumentContext, PipelineConfig

        tracker = MagicMock()
        cfg = PipelineConfig(domain="test-domain", tracker=tracker, runtime_db=MagicMock())
        ctx = DocumentContext(error="upstream failed", run_document_id="rd1")
        result = db_write_stage(ctx, cfg)
        assert result.error == "upstream failed"
        tracker.fail_document.assert_called_once()

    def test_skip_treeless_context(self):
        from unittest.mock import MagicMock
        from knowledge_mining.mining.pipeline import db_write_stage, DocumentContext, PipelineConfig

        tracker = MagicMock()
        cfg = PipelineConfig(domain="test-domain", tracker=tracker, runtime_db=MagicMock())
        ctx = DocumentContext(run_document_id="rd1")
        result = db_write_stage(ctx, cfg)
        assert result.error is None
        tracker.skip_document.assert_called_once()

    def test_preprocess_failure_is_failed_not_skipped(self):
        from unittest.mock import MagicMock
        from knowledge_mining.mining.contracts.models import RawFileData
        from knowledge_mining.mining.pipeline import (
            db_write_stage,
            DocumentContext,
            PipelineConfig,
        )

        tracker = MagicMock()
        cfg = PipelineConfig(
            domain="test-domain", tracker=tracker, runtime_db=MagicMock()
        )
        raw = RawFileData(
            file_path="bad.xlsx",
            relative_path="bad.xlsx",
            file_name="bad.xlsx",
            file_type="markdown",
            content="",
            raw_content_hash="raw",
            normalized_content_hash="raw",
            metadata_json={
                "preprocess_status": "failed",
                "preprocess_error_code": "excel_corrupt_file",
                "preprocess_error": "Unable to read Excel workbook: bad.xlsx",
            },
        )

        result = db_write_stage(
            DocumentContext(raw_file=raw, run_document_id="rd1"), cfg
        )

        tracker.fail_document.assert_called_once_with(
            "rd1", "Unable to read Excel workbook: bad.xlsx"
        )
        tracker.skip_document.assert_not_called()
        assert result.error == "Unable to read Excel workbook: bad.xlsx"


# ===================================================================
# Wave 1-3: DB embedding write test
# ===================================================================

class TestDBEmbeddingWrite:
    """AssetCoreDB should support embedding insertion."""

    def test_insert_retrieval_embedding(self):
        from knowledge_mining.mining.infra.db import AssetCoreDB
        from knowledge_mining.mining.infra.pg_config import MiningDbConfig
        from knowledge_mining.mining.infra.pg_schema import ensure_schema
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        cfg = MiningDbConfig()
        ensure_schema(cfg)
        pool = ConnectionPool(
            cfg.conninfo, min_size=1, max_size=2, open=True,
            kwargs={"row_factory": dict_row},
        )
        db = AssetCoreDB(pool)
        try:
            # Create prerequisite data: batch -> document -> snapshot -> link -> segment -> retrieval unit
            db.upsert_source_batch(
                domain="test-domain", batch_id="batch-1", batch_code="B-TEST",
                source_type="folder_scan",
            )
            doc_id = db.upsert_document(
                domain="test-domain", document_id="doc-1", document_key="doc:/test.md",
                document_name="test.md",
            )
            db.upsert_snapshot(
                domain="test-domain", snapshot_id="snap-1", normalized_content_hash="nh1",
                raw_content_hash="rh1", mime_type="text/markdown", title="Test",
            )
            db.insert_snapshot_link(
                domain="test-domain", link_id="link-1", document_id=doc_id,
                document_snapshot_id="snap-1", source_batch_id="batch-1",
                relative_path="test.md", source_uri="file:///test.md",
            )
            db.insert_raw_segment("seg-1", "snap-1", "doc:/test.md#0", 0, raw_text="test segment")
            db.insert_retrieval_unit("ru-1", "snap-1", "ru:test:raw_text", "raw_text", "raw_segment", text="test unit")

            # Insert embedding
            vec = json.dumps([0.1, 0.2, 0.3])
            db.insert_retrieval_embedding(
                embedding_id="emb-1",
                retrieval_unit_id="ru-1",
                embedding_model="embedding-3",
                embedding_provider="llm_service",
                text_kind="full",
                embedding_dim=3,
                embedding_vector=vec,
            )
            db.commit()

            # Verify
            row = db._fetchone("SELECT * FROM asset_retrieval_embeddings WHERE id = %s", ("emb-1",))
            assert row is not None
            assert row["embedding_model"] == "embedding-3"
            assert row["embedding_dim"] == 3
            assert row["embedding_provider"] == "llm_service"
        finally:
            db.close()


# ===================================================================
# StreamingPipeline Tests
# ===================================================================

class TestStreamingPipeline:
    """Tests for the queue-based parallel pipeline."""

    # REMOVED: test_single_doc_through_all_stages - rule-based components deleted

    # REMOVED: test_multi_doc_concurrent - rule-based components deleted

    # REMOVED: test_single_failure_does_not_block_others - rule-based components deleted

    def test_stage_exception_caught_as_error(self):
        """Exception in a stage is caught and stored in ctx.error."""
        from knowledge_mining.mining.pipeline import DocumentContext, StreamingPipeline

        def boom(ctx):
            raise RuntimeError("intentional test error")

        stages = [("explode", boom, 1)]
        ctx = DocumentContext()
        pipeline = StreamingPipeline(stages)
        results = pipeline.process_all([ctx])

        assert len(results) == 1
        assert results[0].error is not None
        assert "intentional test error" in results[0].error

    def test_error_field_in_with_updates(self):
        """error field is preserved through with_updates."""
        from knowledge_mining.mining.pipeline import DocumentContext

        ctx = DocumentContext(error="something broke")
        assert ctx.error == "something broke"

        ctx2 = ctx.with_updates(tree=None)
        assert ctx2.error == "something broke"

        ctx3 = ctx.with_updates(error=None)
        assert ctx3.error is None
