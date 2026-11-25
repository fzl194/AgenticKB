"""S1 · default_plan_factory 的旧二进制 Office MIME 预映射闸门。

解析计划在 parse service 转换**之前**按原始 MIME 选主解析器——旧二进制
（OLE2）格式必须在这里预映射到 OOXML 对应格式，否则计划兜底
legacy_markdown，转换产物到达后 UnsupportedFormat（真实 .xls E2E 实测踩过）。
"""
from __future__ import annotations

from types import SimpleNamespace

from knowledge_mining.mining.workflow.new_chain_services import (
    DocumentParseFacade,
)


def _raw(mime: str) -> SimpleNamespace:
    return SimpleNamespace(mime=mime, document_id="doc-1")


def test_plan_factory_maps_legacy_xls_to_native_xlsx():
    plan = DocumentParseFacade.default_plan_factory(_raw("application/vnd.ms-excel"), {})
    assert plan.primary_parser_id == "native_xlsx"


def test_plan_factory_maps_legacy_ppt_to_native_pptx():
    plan = DocumentParseFacade.default_plan_factory(_raw("application/vnd.ms-powerpoint"), {})
    assert plan.primary_parser_id == "native_pptx"


def test_plan_factory_maps_legacy_doc_to_native_docx():
    plan = DocumentParseFacade.default_plan_factory(_raw("application/msword"), {})
    assert plan.primary_parser_id == "native_docx"


def test_plan_factory_native_mimes_unchanged():
    plan = DocumentParseFacade.default_plan_factory(
        _raw("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), {}
    )
    assert plan.primary_parser_id == "native_xlsx"
