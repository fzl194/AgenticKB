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

# 批次8 M0（24 号 §11）：正式目录只保留零 LLM 默认线骨架。
# 旧检索资产算子（enrich/discourse_line/contextual_retrieval_enrich/
# retrieval_unit_build）已删除；实体/本体七算子进入研究隔离（research.py），
# 不注册、不进画布/预置/新建范式候选（M2/M3/M6 再分别补
# retrieval_unit_project/query_expansion_generate/hierarchical_summary_generate）。

# 唯一生产骨架：解析与切片显式分离。
_FIXED_TYPES = {
    "input_ingest", "document_parse", "segment_compile",
    "asset_persist", "mining_finalize",
}


_SPECS = (
    ("input_ingest", "input", {"input_spec"}, {"raw_files"}, "FAIL_FAST"),
    ("document_parse", "document", {"raw_files"}, {"parsed_documents"}, "SKIP_DOCUMENT"),
    ("segment_compile", "document", {"parsed_documents"}, {"parsed_segments"}, "SKIP_DOCUMENT"),
    ("embedding", "document", {"retrieval_units"}, {"embeddings"}, "FALLBACK"),
    ("asset_persist", "document", {"parsed_segments"}, {"assets_persisted"}, "SKIP_DOCUMENT"),
    ("mining_finalize", "global", {"assets_persisted"}, {"finalized"}, "FAIL_FAST"),
)


_LABELS = {
    "input_ingest": ("输入发现", "发现输入文件并初始化运行批次。", "input"),
    "document_parse": ("文档解析", "冻结输入并产出结构化解析结果（标题树/表格/证据定位），质量门控后形成知识快照。", "document"),
    "segment_compile": ("切片编译", "从知识快照按策略编译检索切片（表格行带表头、章节路径注入）。", "document"),
    "embedding": ("向量化", "按 representation 策略矩阵为检索单元生成向量。", "discourse"),
    "asset_persist": ("资产持久化", "合并能力线并原子写入文档资产。", "storage"),
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
    slot = _document_slot()
    return ((slot,), (slot,))


def _definition(spec: tuple[str, str, set[str], set[str], str]) -> MiningOperatorDef:
    operator_type, zone, requires, provides, error_policy = spec
    display_name, description, category = _LABELS[operator_type]
    input_slots, output_slots = _slots(operator_type)
    if operator_type in _FIXED_TYPES:
        edit_policy = EditPolicy.FIXED
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
