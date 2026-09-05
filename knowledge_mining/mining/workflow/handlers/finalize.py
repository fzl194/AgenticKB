from __future__ import annotations

from typing import Any, Mapping

from ..core import OperatorResult, OperatorStatus, OperatorWarning
from ..operators.options import FinalizeOptions


def mining_finalize_handler(
    state: Any, params: Mapping[str, Any], runtime: Any
) -> OperatorResult:
    options = FinalizeOptions.model_validate(dict(params))
    required = set(
        (runtime.manifest.get("executionPlan") or {}).get(
            "requiredCompletion", []
        )
    )
    required.discard("finalized")
    binding = runtime.manifest.get("runtimeBinding") or {}
    if binding.get("ontologyApplicable") is False:
        required -= {
            "entity_review_approved",
            "ontology_review_approved",
            "graph_written",
        }
    missing = required - set(getattr(state, "capabilities", frozenset()))
    if missing:
        raise RuntimeError(
            "Cannot finalize before capabilities: " + ", ".join(sorted(missing))
        )
    run_id = runtime.manifest.get("runId") or runtime.manifest.get("run_id")
    run_id = run_id or getattr(runtime.services, "run_id", None)
    if not run_id:
        raise ValueError("Workflow Manifest has no Run identity")
    execution_mode = getattr(runtime.services, "execution_mode", "publish")
    summary = runtime.services.finalize_mining(
        str(run_id),
        execution_mode=execution_mode,
        publish_on_partial_failure=options.publish_on_partial_failure,
    )
    summary_status = str(summary.get("status") or "completed")
    # 36号 §十一：按 finalize 真实结果透传——Build 未创建（且存在失败
    # 文档）→ FAILED；部分失败但 Build 成功 → SUCCESS + partial warning。
    # 此前无条件 SUCCESS 会让 Run 实际失败而节点显示完成、resume 复用
    # 错误的 completed finalize 事件。
    warnings = []
    if summary.get("partial_success"):
        warnings.append(OperatorWarning(
            "finalize_partial",
            "run completed with failed documents: "
            f"committed={summary.get('committed_count')}, "
            f"failed={summary.get('failed_count')}, "
            f"skipped={summary.get('skipped_count')}",
        ))
    if summary.get("build_id") is None:
        failed_count = int(summary.get("failed_count") or 0)
        committed = int(summary.get("committed_count") or 0)
        skipped = int(summary.get("skipped_count") or 0)
        staged = (
            int(summary.get("staged_count") or 0)
            if execution_mode == "assets_only" else 0
        )
        if failed_count and not (committed or skipped or staged):
            return OperatorResult(
                state,
                frozenset(),
                OperatorStatus.FAILED,
                warnings=warnings,
                error_code="finalize_no_build",
                error_message=(
                    "no document entered a validated Build: "
                    + str(summary.get("rejection_summary") or [])[:400]
                ),
            )
    if summary_status != "completed":
        return OperatorResult(
            state,
            frozenset(),
            OperatorStatus.FAILED,
            error_code=f"finalize_{summary_status}",
            error_message=(
                str(summary.get("error") or summary.get("error_summary") or "")
                or f"mining finalize ended with status {summary_status}"
            ),
        )
    capabilities = {"finalized"}
    if summary.get("release_id"):
        capabilities.add("release_published")
    return OperatorResult(
        state,
        frozenset(capabilities),
        OperatorStatus.SUCCESS,
        warnings=warnings,
    )
