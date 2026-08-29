from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


class UnsupportedOperatorVersion(LookupError):
    pass


class UnsafeRunOverride(ValueError):
    pass


Handler = Callable[[Any, Mapping[str, Any], Any], Any]
SAFE_RUN_OVERRIDES = frozenset({
    "maxWorkers",
    "executionMode",
    "publishOnPartialFailure",
})


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], Handler] = {}

    def register(
        self, operator_type: str, operator_version: str, handler: Handler
    ) -> None:
        key = (operator_type, operator_version)
        if key in self._handlers:
            raise ValueError(
                f"Handler already registered for {operator_type}@{operator_version}"
            )
        self._handlers[key] = handler

    def resolve(self, operator_type: str, operator_version: str) -> Handler:
        try:
            return self._handlers[(operator_type, operator_version)]
        except KeyError as exc:
            raise UnsupportedOperatorVersion(
                f"Unsupported operator version: {operator_type}@{operator_version}"
            ) from exc


def resolve_effective_parameters(
    *,
    algorithm_defaults: Mapping[str, Any],
    domain_defaults: Mapping[str, Any],
    workflow_params: Mapping[str, Any],
    run_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    overrides = dict(run_overrides or {})
    unsafe = sorted(set(overrides) - SAFE_RUN_OVERRIDES)
    if unsafe:
        raise UnsafeRunOverride(
            f"Unsafe Run override(s): {', '.join(unsafe)}"
        )
    result = dict(algorithm_defaults)
    result.update(domain_defaults)
    result.update(workflow_params)
    result.update(overrides)
    return result


def builtin_handler_registry() -> HandlerRegistry:
    from .handlers.document import DOCUMENT_HANDLERS
    from .handlers.finalize import mining_finalize_handler
    from .handlers.input import input_ingest_handler
    from .handlers.persist import asset_persist_handler

    # 批次8 M0：研究算子（entity/ontology/graph_write）的 handler
    # 隔离在 handlers/research.py，不在此注册。
    registry = HandlerRegistry()
    registry.register("input_ingest", "1", input_ingest_handler)
    for operator_type, handler in DOCUMENT_HANDLERS.items():
        registry.register(operator_type, "1", handler)
    registry.register("asset_persist", "1", asset_persist_handler)
    registry.register("mining_finalize", "1", mining_finalize_handler)
    return registry
