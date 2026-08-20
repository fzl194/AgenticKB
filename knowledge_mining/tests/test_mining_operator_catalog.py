import json

import pytest
from pydantic import ValidationError

from knowledge_mining.mining.workflow.core import EditPolicy, ErrorPolicy
from knowledge_mining.mining.workflow.operators.catalog import builtin_catalog
from knowledge_mining.mining.workflow.operators.options import (
    OPTIONS_BY_OPERATOR,
    ParseSegmentOptions,
    RetrievalUnitOptions,
)
from knowledge_mining.mining.workflow.templates import builtin_templates


APPROVED_OPERATOR_TYPES = {
    "input_ingest",
    "parse_segment",
    "document_parse",
    "segment_compile",
    "enrich",
    "discourse_line",
    "contextual_retrieval_enrich",
    "retrieval_unit_build",
    "embedding",
    "entity_extract",
    "entity_resolve",
    "entity_relation_extract",
    "asset_persist",
    "entity_review_gate",
    "ontology_induction",
    "ontology_review_gate",
    "graph_write",
    "mining_finalize",
}


def test_catalog_exposes_exactly_the_approved_18_operators() -> None:
    catalog = builtin_catalog()
    assert set(catalog) == APPROVED_OPERATOR_TYPES
    assert {
        key for key, value in catalog.items() if value.edit_policy is EditPolicy.FIXED
    } == {
            "input_ingest", "parse_segment", "document_parse",
            "segment_compile", "asset_persist", "mining_finalize",
        }
    assert {
        key
        for key, value in catalog.items()
        if value.edit_policy is EditPolicy.PROTECTED
    } == {"entity_review_gate", "ontology_review_gate", "graph_write"}
    assert catalog["graph_write"].error_policy is ErrorPolicy.FAIL_FAST
    assert catalog["mining_finalize"].error_policy is ErrorPolicy.FAIL_FAST


def test_option_aliases_validate_wire_parameters() -> None:
    value = RetrievalUnitOptions.model_validate(
        {
            "rawTextUnit": True,
            "generatedQuestionUnit": False,
            "tableRowUnit": True,
            "maxQuestionsPerSegment": 0,
            "minQuestionworthyTokens": 80,
        }
    )
    assert value.generated_question_unit is False
    assert value.model_dump(by_alias=True)["minQuestionworthyTokens"] == 80


def test_option_models_reject_unknown_and_inconsistent_parameters() -> None:
    with pytest.raises(ValidationError):
        RetrievalUnitOptions.model_validate({"unknownOption": True})
    with pytest.raises(ValidationError):
        ParseSegmentOptions.model_validate(
            {"minSegmentTokens": 800, "maxSegmentTokens": 200}
        )


def test_every_operator_schema_comes_from_its_typed_option_model() -> None:
    catalog = builtin_catalog()
    assert set(OPTIONS_BY_OPERATOR) == APPROVED_OPERATOR_TYPES
    for operator_type, definition in catalog.items():
        model = OPTIONS_BY_OPERATOR[operator_type]
        assert definition.to_dict()["paramSchemaJson"] == model.model_json_schema(
            by_alias=True
        )
        model.model_validate(model().model_dump(by_alias=True))


def test_all_seven_templates_are_global_and_full_contains_all_operators() -> None:
    templates = builtin_templates()
    assert set(templates) == {
        "minimal",
        "fast_retrieval",
        "discourse_only",
        "entity_graph",
        "hybrid_knowledge",
        "ontology_only",
        "full",
    }
    # v1 模板仍用一体算子（新算子属 v2 模板，见 test_m6_workflow_operators）
    assert {
        node.operator_type for node in templates["full"].nodes
    } == APPROVED_OPERATOR_TYPES - {"document_parse", "segment_compile"}
    assert all(
        '"domain"' not in json.dumps(graph.to_dict()).lower()
        for graph in templates.values()
    )


def test_template_editable_nodes_match_the_approved_capability_sets() -> None:
    catalog = builtin_catalog()
    templates = builtin_templates()

    def editable(template_name: str) -> set[str]:
        return {
            node.operator_type
            for node in templates[template_name].nodes
            if catalog[node.operator_type].edit_policy is EditPolicy.EDITABLE
        }

    assert editable("full") == {
        "enrich",
        "discourse_line",
        "contextual_retrieval_enrich",
        "retrieval_unit_build",
        "embedding",
        "entity_extract",
        "entity_resolve",
        "entity_relation_extract",
        "ontology_induction",
    }
    assert editable("discourse_only") == {
        "enrich",
        "discourse_line",
        "contextual_retrieval_enrich",
        "retrieval_unit_build",
        "embedding",
    }
    assert editable("fast_retrieval") == {
        "retrieval_unit_build",
        "embedding",
    }
    assert editable("entity_graph") == {
        "entity_extract",
        "entity_resolve",
        "entity_relation_extract",
    }
    assert editable("hybrid_knowledge") == {
        "enrich",
        "discourse_line",
        "contextual_retrieval_enrich",
        "retrieval_unit_build",
        "embedding",
        "entity_extract",
        "entity_resolve",
        "entity_relation_extract",
    }
    assert editable("ontology_only") == {
        "entity_extract",
        "entity_resolve",
        "entity_relation_extract",
        "ontology_induction",
    }
    assert editable("minimal") == set()


def test_catalog_and_template_results_cannot_mutate_the_singletons() -> None:
    catalog = builtin_catalog()
    templates = builtin_templates()
    catalog.pop("input_ingest")
    templates.pop("full")
    assert len(builtin_catalog()) == 18
    assert "full" in builtin_templates()
