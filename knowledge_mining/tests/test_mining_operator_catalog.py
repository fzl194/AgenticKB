import pytest
from pydantic import ValidationError

from knowledge_mining.mining.workflow.core import EditPolicy, ErrorPolicy
from knowledge_mining.mining.workflow.operators.catalog import builtin_catalog
from knowledge_mining.mining.workflow.operators.options import (
    EmbeddingOptions,
    OPTIONS_BY_OPERATOR,
)
from knowledge_mining.mining.workflow.templates import builtin_templates


# 批次8 M0（24 号 §11）：正式目录收敛到零 LLM 默认线骨架。
# enrich/discourse_line/contextual_retrieval_enrich/retrieval_unit_build 删除；
# 实体/本体七算子研究隔离（operators/research.py）。
APPROVED_OPERATOR_TYPES = {
    "input_ingest",
    "document_parse",
    "segment_compile",
    "retrieval_unit_project",
    "query_expansion_generate",
    "hierarchical_summary_generate",
    "embedding",
    "asset_persist",
    "mining_finalize",
}


def test_catalog_exposes_exactly_the_approved_operators() -> None:
    catalog = builtin_catalog()
    assert set(catalog) == APPROVED_OPERATOR_TYPES
    assert {
        key for key, value in catalog.items() if value.edit_policy is EditPolicy.FIXED
    } == {
            "input_ingest", "document_parse", "segment_compile",
            "asset_persist", "mining_finalize",
        }
    assert {
        key
        for key, value in catalog.items()
        if value.edit_policy is EditPolicy.PROTECTED
    } == set()
    assert catalog["mining_finalize"].error_policy is ErrorPolicy.FAIL_FAST


def test_option_aliases_validate_wire_parameters() -> None:
    value = EmbeddingOptions.model_validate(
        {"strategyOverrides": {"code_block": "structural"}}
    )
    dumped = value.model_dump(by_alias=True)
    assert dumped["strategyOverrides"] == {"code_block": "structural"}
    with pytest.raises(Exception):
        EmbeddingOptions.model_validate({"unitTypes": ["raw_text"]})  # M4 已退役


def test_option_models_reject_unknown_and_inconsistent_parameters() -> None:
    with pytest.raises(ValidationError):
        EmbeddingOptions.model_validate({"unknownOption": True})


def test_every_operator_schema_comes_from_its_typed_option_model() -> None:
    catalog = builtin_catalog()
    assert set(OPTIONS_BY_OPERATOR) == APPROVED_OPERATOR_TYPES
    for operator_type, definition in catalog.items():
        model = OPTIONS_BY_OPERATOR[operator_type]
        assert definition.to_dict()["paramSchemaJson"] == model.model_json_schema(
            by_alias=True
        )
        model.model_validate(model().model_dump(by_alias=True))


def test_legacy_templates_retired_replaced_by_four_presets() -> None:
    """旧 7 类模板已退役；M6 起内置模板=4 套官方预置（M6_presets 展开断言）。"""
    assert set(builtin_templates()) == {
        "lexical_assets", "hybrid_assets",
        "query_alias_assets", "longdoc_assets",
    }


def test_catalog_results_cannot_mutate_the_singletons() -> None:
    catalog = builtin_catalog()
    catalog.pop("input_ingest")
    assert len(builtin_catalog()) == len(APPROVED_OPERATOR_TYPES)
