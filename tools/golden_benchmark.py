"""Golden corpus benchmark（整改轮，用户指令：六类指标 + warning 分布）.

对 ``tests/golden_corpus`` 全量样本执行：
    parse -> normalize -> reconcile -> compute_metrics(期望对照)
并输出按格式/全局聚合的报告（JSON + Markdown）。**不以元素数量作为
质量结论**——报告口径为：

- 字符覆盖率（有 source_text 的样本）
- 结构准确率（期望标题/锚文本/表格数命中综合）
- 表格完整率（cell 独立证据 + 网格一致性）
- 证据可定位率（元素级 locator 覆盖）
- 阅读序正确率（容器内 bbox top 单调性）
- warning 分布（diagnostics 归一化计数）
- 质量门禁决策分布（PASS/WARN/FAIL）

用法：
    cd knowledge_mining && python ../tools/golden_benchmark.py \
        [--out var/golden/benchmark]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_KM_ROOT = _REPO_ROOT / "knowledge_mining"
sys.path.insert(0, str(_KM_ROOT))   # tests.* 包
sys.path.insert(0, str(_REPO_ROOT))  # knowledge_mining 包（防环境同名包遮蔽）

from knowledge_mining.mining.parse_adapters.factory import resolve_pipeline
from knowledge_mining.mining.parse_quality import (
    QualityGate,
    compute_metrics,
)
from knowledge_mining.mining.parse_reconciler import StructuralReconciler
from tests.golden_corpus.corpus import PARSER_ID, build_corpus, corpus_stats


def run_benchmark() -> dict[str, Any]:
    docs = build_corpus()
    reconciler = StructuralReconciler()
    gate = QualityGate()

    per_doc: list[dict[str, Any]] = []
    warning_counts: Counter[str] = Counter()
    decisions: Counter[str] = Counter()

    for doc in docs:
        parser, normalizer = resolve_pipeline(PARSER_ID[doc.format_key])
        assert parser is not None, doc.name
        try:
            artifact = parser.parse(doc.data, mime=doc.mime)
            ir = normalizer.normalize(artifact, source_raw_hash="bench")
        except Exception as exc:  # noqa: BLE001 —— 负例合法失败：记录不断言
            decisions["PARSE_FAILED"] += 1
            per_doc.append({
                "name": doc.name, "format": doc.format_key,
                "category": doc.category,
                "char_coverage": None, "structure_accuracy": None,
                "heading_match_ratio": None, "anchor_hit_ratio": None,
                "table_count_match": None, "table_cell_evidence": None,
                "table_grid_consistency": None, "evidence_locatability": None,
                "reading_order_monotonicity": None,
                "decision": "PARSE_FAILED",
                "issues": [type(exc).__name__], "warnings": {},
            })
            continue
        result = reconciler.reconcile(ir)
        metrics = compute_metrics(
            result.document,
            source_text=doc.source_text,
            expectations=doc.expectations,
        )
        decision = gate.evaluate(metrics)
        decisions[decision.decision] += 1
        warning_counts.update(metrics.warning_counts)
        per_doc.append({
            "name": doc.name,
            "format": doc.format_key,
            "category": doc.category,
            "char_coverage": metrics.char_coverage,
            "structure_accuracy": metrics.structure_accuracy,
            "heading_match_ratio": metrics.heading_match_ratio,
            "anchor_hit_ratio": metrics.anchor_hit_ratio,
            "table_count_match": metrics.table_count_match,
            "table_cell_evidence": metrics.table_cell_evidence,
            "table_grid_consistency": metrics.table_grid_consistency,
            "evidence_locatability": metrics.evidence_locatability,
            "reading_order_monotonicity": metrics.reading_order_monotonicity,
            "decision": decision.decision,
            "issues": [i.code for i in decision.issues],
            "warnings": metrics.warning_counts,
        })

    def _avg(values: list[float | None]) -> float | None:
        real = [v for v in values if v is not None]
        return sum(real) / len(real) if real else None

    def _agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(rows),
            "char_coverage": _avg([r["char_coverage"] for r in rows]),
            "structure_accuracy": _avg([r["structure_accuracy"] for r in rows]),
            "table_cell_evidence": _avg(
                [r["table_cell_evidence"] for r in rows]
            ),
            "table_grid_consistency": _avg(
                [r["table_grid_consistency"] for r in rows]
            ),
            "evidence_locatability": _avg(
                [r["evidence_locatability"] for r in rows]
            ),
            "reading_order_monotonicity": _avg(
                [r["reading_order_monotonicity"] for r in rows]
            ),
        }

    by_format = {
        fmt: _agg([r for r in per_doc if r["format"] == fmt])
        for fmt in sorted({r["format"] for r in per_doc})
    }
    return {
        "corpus": corpus_stats(),
        "overall": _agg(per_doc),
        "by_format": by_format,
        "decision_distribution": dict(decisions),
        "warning_distribution": dict(warning_counts),
        "documents": per_doc,
    }


def _fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Golden Corpus Benchmark（整改轮）\n")
    stats = report["corpus"]
    lines.append(
        f"样本总量 **{stats['total']}**；按格式 "
        + "，".join(f"{k}={v}" for k, v in stats["by_format"].items())
        + "；按类别 "
        + "，".join(f"{k}={v}" for k, v in stats["by_category"].items())
        + "。\n"
    )
    lines.append("## 总体指标\n")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    for key in (
        "char_coverage", "structure_accuracy", "table_cell_evidence",
        "table_grid_consistency", "evidence_locatability",
        "reading_order_monotonicity",
    ):
        lines.append(f"| {key} | {_fmt(report['overall'][key])} |")
    lines.append("")
    lines.append("## 按格式\n")
    header = [
        "格式", "样本", "字符覆盖", "结构准确", "表格证据", "网格一致",
        "证据定位", "阅读序",
    ]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for fmt, agg in report["by_format"].items():
        lines.append("| " + " | ".join([
            fmt, str(agg["count"]),
            _fmt(agg["char_coverage"]),
            _fmt(agg["structure_accuracy"]),
            _fmt(agg["table_cell_evidence"]),
            _fmt(agg["table_grid_consistency"]),
            _fmt(agg["evidence_locatability"]),
            _fmt(agg["reading_order_monotonicity"]),
        ]) + " |")
    lines.append("")
    lines.append("## 质量决策分布\n")
    lines.append("```json")
    lines.append(json.dumps(report["decision_distribution"], ensure_ascii=False))
    lines.append("```")
    lines.append("")
    lines.append("## warning 分布\n")
    lines.append("```json")
    lines.append(json.dumps(report["warning_distribution"], ensure_ascii=False))
    lines.append("```")
    lines.append("")
    lines.append("## 逐样本明细\n")
    lines.append("| 样本 | 格式 | 类别 | 决策 | issues |")
    lines.append("|---|---|---|---|---|")
    for r in report["documents"]:
        lines.append(
            f"| {r['name']} | {r['format']} | {r['category']} | "
            f"{r['decision']} | {','.join(r['issues']) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default="var/golden/benchmark",
        help="报告输出目录（相对 knowledge_mining）",
    )
    args = parser.parse_args()

    report = run_benchmark()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "benchmark.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "benchmark.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    print(f"corpus={report['corpus']['total']} docs")
    print(json.dumps(report["overall"], ensure_ascii=False, indent=2))
    print(f"report -> {out_dir}/benchmark.md")


if __name__ == "__main__":
    main()
