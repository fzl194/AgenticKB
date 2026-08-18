"""Golden corpus 阈值守卫（整改轮）：把基准指标固化为回归门禁.

禁止只以"元素数量"作质量结论（用户指令）——本测试断言六类指标的
**下限**与期望的决策分布。基准回归时先跑 ``tools/golden_benchmark.py``
定位逐样本差异。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.parse_adapters.factory import resolve_pipeline
from knowledge_mining.mining.parse_quality import (
    QualityGate,
    compute_metrics,
)
from knowledge_mining.mining.parse_reconciler import StructuralReconciler
from tests.golden_corpus.corpus import PARSER_ID, build_corpus, corpus_stats


def _evaluate_all():
    docs = build_corpus()
    reconciler = StructuralReconciler()
    gate = QualityGate()
    outcomes: dict[str, dict] = {}
    for doc in docs:
        parser, normalizer = resolve_pipeline(PARSER_ID[doc.format_key])
        try:
            artifact = parser.parse(doc.data, mime=doc.mime)
            ir = normalizer.normalize(artifact, source_raw_hash="guard")
            result = reconciler.reconcile(ir)
            metrics = compute_metrics(
                result.document,
                source_text=doc.source_text,
                expectations=doc.expectations,
            )
            decision = gate.evaluate(metrics).decision
            outcomes[doc.name] = {
                "category": doc.category,
                "decision": decision,
                "metrics": metrics,
            }
        except Exception:  # noqa: BLE001 —— 负例合法失败
            outcomes[doc.name] = {
                "category": doc.category, "decision": "PARSE_FAILED",
                "metrics": None,
            }
    return outcomes


_OUTCOMES = _evaluate_all()


def test_corpus_shape() -> None:
    stats = corpus_stats()
    assert stats["total"] == 50
    assert stats["by_category"] == {
        "positive": 19, "complex": 21, "negative": 4, "degenerate": 6,
    }
    for fmt, count in {
        "md": 7, "txt": 5, "docx": 8, "xlsx": 7, "pptx": 7, "html": 8,
        "pdf": 8,
    }.items():
        assert stats["by_format"][fmt] == count


def test_positive_samples_pass_or_warn() -> None:
    positives = {
        name: o for name, o in _OUTCOMES.items() if o["category"] == "positive"
    }
    assert len(positives) == 19
    bad = {
        n: o["decision"] for n, o in positives.items()
        if o["decision"] not in ("PASS", "WARN")
    }
    assert not bad, bad


def test_degenerate_empties_fail() -> None:
    empties = [n for n, o in _OUTCOMES.items() if "empty" in n]
    assert len(empties) == 4  # md/txt/docx/html-empty（txt-blank-only 另计）
    for name in empties:
        assert _OUTCOMES[name]["decision"] == "FAIL", name


def test_negative_garbage_rejected() -> None:
    assert _OUTCOMES["md-garbage"]["decision"] == "PARSE_FAILED"


def _avg(field: str) -> float:
    values = [
        getattr(o["metrics"], field) for o in _OUTCOMES.values()
        if o["metrics"] is not None and getattr(o["metrics"], field) is not None
    ]
    return sum(values) / len(values)


def test_overall_metric_floors() -> None:
    """六类指标下限（回归门禁；低于阈值 = 解析质量退化）."""
    assert _avg("char_coverage") >= 0.98
    assert _avg("structure_accuracy") >= 0.95
    assert _avg("table_cell_evidence") >= 0.90
    assert _avg("table_grid_consistency") >= 0.99
    assert _avg("evidence_locatability") >= 0.85
    assert _avg("reading_order_monotonicity") >= 0.99


def test_pdf_two_page_furniture_recovers_body() -> None:
    """双页构造修复后：pdf-furniture 必须恢复正文（曾整文档为空）."""
    assert _OUTCOMES["pdf-furniture"]["decision"] in ("PASS", "WARN")


def test_warning_distribution_visible() -> None:
    """诊断分布：不支持结构（图片/图表）必须可见，不静默丢失."""
    counter: dict[str, int] = {}
    for o in _OUTCOMES.values():
        if o["metrics"] is None:
            continue
        for key, n in o["metrics"].warning_counts.items():
            counter[key] = counter.get(key, 0) + n
    assert counter.get("document", 0) >= 1  # DOCX/MD 图片等诊断
    assert counter.get("sheet", 0) >= 1 or counter.get("slide", 0) >= 1
