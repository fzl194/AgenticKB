from __future__ import annotations

from typing import Iterable

from ..core import (
    EditPolicy,
    ErrorPolicy,
    ExecutionZone,
    MiningOperatorDef,
    SlotDecl,
    SlotType,
)
from .options import OPTIONS_BY_OPERATOR


# 唯一生产骨架：解析与切片显式分离。
_FIXED_TYPES = {
    "input_ingest", "document_parse", "segment_compile",
    "asset_persist", "mining_finalize",
}
_PROTECTED_TYPES = {"entity_review_gate", "ontology_review_gate", "graph_write"}


_SPECS = (
    ("input_ingest", "input", {"input_spec"}, {"raw_files"}, "FAIL_FAST"),
    ("document_parse", "document", {"raw_files"}, {"parsed_documents"}, "SKIP_DOCUMENT"),
    ("segment_compile", "document", {"parsed_documents"}, {"parsed_segments"}, "SKIP_DOCUMENT"),
    ("enrich", "document", {"parsed_segments"}, {"semantic_enrichment"}, "FALLBACK"),
    ("discourse_line", "document", {"parsed_segments"}, {"discourse_relations"}, "SKIP_WITH_EMPTY"),
    ("contextual_retrieval_enrich", "document", {"parsed_segments"}, {"retrieval_context"}, "FALLBACK"),
    ("retrieval_unit_build", "document", {"parsed_segments"}, {"retrieval_units"}, "SKIP_DOCUMENT"),
    ("embedding", "document", {"retrieval_units"}, {"embeddings"}, "FALLBACK"),
    ("entity_extract", "document", {"parsed_segments"}, {"entity_mentions"}, "SKIP_WITH_EMPTY"),
    ("entity_resolve", "document", {"entity_mentions"}, {"resolved_entities"}, "SKIP_WITH_EMPTY"),
    ("entity_relation_extract", "document", {"entity_mentions"}, {"entity_relations"}, "SKIP_WITH_EMPTY"),
    ("asset_persist", "document", {"parsed_segments"}, {"assets_persisted"}, "SKIP_DOCUMENT"),
    ("entity_review_gate", "global", {"entity_mentions"}, {"entity_review_approved"}, "PAUSE_FOR_REVIEW"),
    ("ontology_induction", "global", {"entity_review_approved"}, {"ontology_candidates"}, "FALLBACK"),
    ("ontology_review_gate", "global", {"ontology_candidates"}, {"ontology_review_approved"}, "PAUSE_FOR_REVIEW"),
    ("graph_write", "global", {"entity_review_approved"}, {"graph_written"}, "FAIL_FAST"),
    ("mining_finalize", "global", {"assets_persisted"}, {"finalized"}, "FAIL_FAST"),
)


_LABELS = {
    "input_ingest": ("输入发现", "发现输入文件并初始化运行批次。", "input"),
    "document_parse": ("文档解析", "冻结输入并产出结构化解析结果（标题树/表格/证据定位），质量门控后形成知识快照。", "document"),
    "segment_compile": ("切片编译", "从知识快照按策略编译检索切片（表格行带表头、章节路径注入）。", "document"),
    "enrich": ("语义增强", "补充段落语义角色和内容判断。", "document"),
    "discourse_line": ("篇章关系抽取", "抽取段落之间的篇章关系。", "discourse"),
    "contextual_retrieval_enrich": ("上下文检索增强", "生成分段检索背景。", "discourse"),
    "retrieval_unit_build": ("检索单元生成", "生成原文、问题和表格行检索单元。", "discourse"),
    "embedding": ("向量化", "为选定检索单元生成向量。", "discourse"),
    "entity_extract": ("实体抽取", "抽取实体 Mention 和证据。", "ontology"),
    "entity_resolve": ("实体归一", "解析别名并关联规范实体。", "ontology"),
    "entity_relation_extract": ("实体关系抽取", "生成实体关系候选。", "ontology"),
    "asset_persist": ("资产持久化", "合并能力线并原子写入文档资产。", "storage"),
    "entity_review_gate": ("实体审核", "存在待审实体时暂停。", "review"),
    "ontology_induction": ("本体归纳", "从已确认实体归纳本体候选。", "ontology"),
    "ontology_review_gate": ("本体审核", "存在待审本体候选时暂停。", "review"),
    "graph_write": ("图谱写入", "审核后聚合并写入最终图谱。", "ontology"),
    "mining_finalize": ("挖掘收尾", "执行 Build、校验、发布和 Run 收尾。", "publish"),
}


def _document_slot(name: str = "documents", *, required: bool = True) -> SlotDecl:
    return SlotDecl(name, SlotType.DOCUMENT_BATCH, required=required)


def _slots(operator_type: str) -> tuple[tuple[SlotDecl, ...], tuple[SlotDecl, ...]]:
    if operator_type == "input_ingest":
        return (
            (SlotDecl("input", SlotType.INPUT_SPEC),),
            (SlotDecl("rawFiles", SlotType.RAW_FILE_BATCH),),
        )
    if operator_type == "document_parse":
        return (
            (SlotDecl("rawFiles", SlotType.RAW_FILE_BATCH),),
            (_document_slot(),),
        )
    if operator_type == "segment_compile":
        slot = _document_slot()
        return ((slot,), (slot,))
    if operator_type == "asset_persist":
        return (
            (
                _document_slot(),
                _document_slot("discourseAssets", required=False),
                _document_slot("ontologyAssets", required=False),
            ),
            (SlotDecl("finalizeInput", SlotType.FINALIZE_INPUT),),
        )
    if operator_type == "mining_finalize":
        return (
            (SlotDecl("finalizeInput", SlotType.FINALIZE_INPUT),),
            (SlotDecl("result", SlotType.FINALIZE_RESULT),),
        )
    if operator_type in {
        "entity_review_gate",
        "ontology_induction",
        "ontology_review_gate",
        "graph_write",
    }:
        slot = SlotDecl("finalizeInput", SlotType.FINALIZE_INPUT)
        return ((slot,), (slot,))
    slot = _document_slot()
    return ((slot,), (slot,))


def _definition(spec: tuple[str, str, set[str], set[str], str]) -> MiningOperatorDef:
    operator_type, zone, requires, provides, error_policy = spec
    display_name, description, category = _LABELS[operator_type]
    input_slots, output_slots = _slots(operator_type)
    if operator_type in _FIXED_TYPES:
        edit_policy = EditPolicy.FIXED
    elif operator_type in _PROTECTED_TYPES:
        edit_policy = EditPolicy.PROTECTED
    else:
        edit_policy = EditPolicy.EDITABLE
    return MiningOperatorDef(
        type=operator_type,
        version="1",
        display_name=display_name,
        description=description,
        category=category,
        zone=ExecutionZone(zone),
        edit_policy=edit_policy,
        input_slots=input_slots,
        output_slots=output_slots,
        requires=frozenset(requires),
        provides=frozenset(provides),
        param_schema_json=OPTIONS_BY_OPERATOR[operator_type].model_json_schema(by_alias=True),
        error_policy=ErrorPolicy(error_policy),
        unique=True,
    )


def _build_catalog(specs: Iterable[tuple[str, str, set[str], set[str], str]]) -> dict[str, MiningOperatorDef]:
    return {definition.type: definition for definition in map(_definition, specs)}


_BUILTIN_CATALOG = _build_catalog(_SPECS)


def builtin_catalog() -> dict[str, MiningOperatorDef]:
    return dict(_BUILTIN_CATALOG)
