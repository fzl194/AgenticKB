from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.jobs import run as run_job
from knowledge_mining.mining.contracts.models import DocumentProfile
from knowledge_mining.mining.pipeline import DocumentContext
from knowledge_mining.mining.workflow.compiler import WorkflowCompiler
from knowledge_mining.mining.workflow.core import (
    DocumentState,
    OperatorResult,
    OperatorStatus,
)
from knowledge_mining.mining.workflow.handler_registry import (
    HandlerRegistry,
    builtin_handler_registry,
)
from knowledge_mining.mining.workflow.manifest import bind_run_manifest
from knowledge_mining.mining.workflow.operators.catalog import builtin_catalog
from knowledge_mining.mining.workflow.runtime import (
    InvalidWorkflowManifest,
    MiningWorkflowRuntime,
)
from knowledge_mining.tests.formal_chain_helper import formal_chain_workflow_graph


class RuntimeRepository:
    def __init__(self, manifest) -> None:
        self.manifest = manifest
        self.events = []
        self.attempts = {}
        self.markers = {}

    def load_manifest(self, run_id):
        return self.manifest

    def start_node(self, **kwargs):
        key = (kwargs["run_document_id"], kwargs["node_id"])
        self.attempts[key] = self.attempts.get(key, 0) + 1
        attempt = SimpleNamespace(id=f"{key}:{self.attempts[key]}")
        self.events.append({**kwargs, "id": attempt.id, "status": "started"})
        return attempt

    def finish_node(self, attempt, **kwargs):
        event = next(item for item in self.events if item["id"] == attempt.id)
        event.update(kwargs)

    def is_node_completed(self, run_id, node_id, run_document_id):
        return any(
            item["run_id"] == run_id
            and item["node_id"] == node_id
            and item["run_document_id"] == run_document_id
            and item["status"] == "completed"
            for item in self.events
        )

    def reusable_node_result(self, run_id, node_id, run_document_id):
        reusable = {"completed", "skipped", "fallback", "not_applicable"}
        matching = [
            item
            for item in self.events
            if item["run_id"] == run_id
            and item["node_id"] == node_id
            and item["run_document_id"] == run_document_id
            and item["status"] in reusable
        ]
        if not matching:
            return None
        event = matching[-1]
        return {
            "status": event["status"],
            "capabilities": event.get("output_summary", {}).get(
                "capabilities", ()
            ),
        }

    def document_persist_marker(self, run_document_id):
        return self.markers.get(run_document_id)

    def set_active_node(self, *args, **kwargs):
        pass


def minimal_manifest(*, max_tokens=800):
    graph = formal_chain_workflow_graph()
    graph = replace(
        graph,
        nodes=tuple(
            replace(node, params={"maxTokens": max_tokens})
            if node.operator_type == "segment_compile"
            else node
            for node in graph.nodes
        ),
    )
    plan = WorkflowCompiler(builtin_catalog()).compile(
        graph, mode="publish"
    ).require_plan()
    return bind_run_manifest(
        plan.to_manifest(workflow_id="workflow-a", workflow_version=1),
        domain="odn",
        channel="prod",
        ontology_version_id=None,
        upload_batch_id="batch-1",
        config_fingerprint="config-1",
    )


def runtime_context(manifest, registry, calls):
    repository = RuntimeRepository(manifest)
    state = DocumentState(
        "doc-1",
        "doc:/a",
        DocumentContext(
            profile=DocumentProfile(document_key="doc:/a"),
            run_document_id="doc-1",
        ),
    )

    def input_handler(input_spec, params, runtime):
        calls.append(("input_ingest", dict(params)))
        return OperatorResult((state,), frozenset({"raw_files"}), OperatorStatus.SUCCESS)

    def document_handler(operator_type):
        def handler(document, params, runtime):
            calls.append((operator_type, dict(params)))
            if operator_type == "asset_persist":
                repository.markers[document.run_document_id] = ("d1", "s1")
            return OperatorResult(
                document,
                frozenset({operator_type + "_done"}),
                OperatorStatus.SUCCESS,
            )

        return handler

    def finalize_handler(state, params, runtime):
        calls.append(("mining_finalize", dict(params)))
        capabilities = {"finalized"}
        if getattr(runtime.services, "execution_mode", "publish") == "publish":
            capabilities.add("release_published")
        return OperatorResult(
            state,
            frozenset(capabilities),
            OperatorStatus.SUCCESS,
        )

    services = SimpleNamespace(
        handler_registry=registry,
        input_spec={"uploadBatchId": "batch-1"},
        max_workers=1,
        run_id="run-1",
        active_operator_type=None,
        initial_global_capabilities=frozenset(),
        execution_mode="publish",
        claim_manual_publish=lambda: True,
    )
    return SimpleNamespace(
        domain="odn",
        channel="prod",
        ontology_version_id=None,
        runtime_repository=repository,
        services=services,
        cancellation_check=lambda: False,
        manifest=manifest,
    ), input_handler, document_handler, finalize_handler


def test_runtime_executes_only_the_frozen_manifest_and_parameters() -> None:
    manifest = minimal_manifest(max_tokens=800)
    calls = []
    registry = HandlerRegistry()
    context, input_handler, document_handler, finalize_handler = runtime_context(
        manifest, registry, calls
    )
    registry.register("input_ingest", "1", input_handler)
    registry.register("document_parse", "1", document_handler("document_parse"))
    registry.register("segment_compile", "1", document_handler("segment_compile"))
    registry.register("asset_persist", "1", document_handler("asset_persist"))
    registry.register("mining_finalize", "1", finalize_handler)
    context.services.global_workflow_repository = SimpleNamespace(
        read=lambda *args: (_ for _ in ()).throw(
            AssertionError("runtime must not read global workflow state")
        )
    )

    result = MiningWorkflowRuntime(context, run_id="run-1").execute()

    compile_call = next(item for item in calls if item[0] == "segment_compile")
    assert compile_call[1]["maxTokens"] == 800
    assert result.status == "completed"
    assert result.capabilities >= {"finalized", "release_published"}


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schemaVersion", "1.0", "schema"),
        ("catalogVersion", "999", "catalog"),
        ("graphHash", "tampered", "graph hash"),
    ],
)
def test_runtime_rejects_tampered_or_unsupported_manifest(field, value, message) -> None:
    manifest = minimal_manifest()
    manifest[field] = value
    registry = HandlerRegistry()
    context, input_handler, document_handler, finalize_handler = runtime_context(
        manifest, registry, []
    )
    registry.register("input_ingest", "1", input_handler)
    registry.register("document_parse", "1", document_handler("document_parse"))
    registry.register("segment_compile", "1", document_handler("segment_compile"))
    registry.register("asset_persist", "1", document_handler("asset_persist"))
    registry.register("mining_finalize", "1", finalize_handler)

    with pytest.raises(InvalidWorkflowManifest, match=message):
        MiningWorkflowRuntime(context, run_id="run-1").execute()


def test_runtime_rejects_parameter_hash_and_missing_exact_handler() -> None:
    manifest = minimal_manifest()
    parse = next(item for item in manifest["nodes"] if item["type"] == "segment_compile")
    parse["params"]["maxTokens"] = 801
    context, *_ = runtime_context(manifest, HandlerRegistry(), [])

    with pytest.raises(InvalidWorkflowManifest, match="parameter hash"):
        MiningWorkflowRuntime(context, run_id="run-1").execute()


def test_builtin_registry_has_all_16_exact_handlers() -> None:
    registry = builtin_handler_registry()

    assert {
        operator_type
        for operator_type in builtin_catalog()
        if registry.resolve(operator_type, "1")
    } == set(builtin_catalog())


def test_safe_run_override_changes_finalize_without_changing_node_hash() -> None:
    manifest = minimal_manifest()
    manifest["runOverrides"] = {"publishOnPartialFailure": True}
    calls = []
    registry = HandlerRegistry()
    context, input_handler, document_handler, finalize_handler = runtime_context(
        manifest, registry, calls
    )
    registry.register("input_ingest", "1", input_handler)
    registry.register("document_parse", "1", document_handler("document_parse"))
    registry.register("segment_compile", "1", document_handler("segment_compile"))
    registry.register("asset_persist", "1", document_handler("asset_persist"))
    registry.register("mining_finalize", "1", finalize_handler)

    MiningWorkflowRuntime(context, run_id="run-1").execute()

    finalize_call = next(item for item in calls if item[0] == "mining_finalize")
    assert finalize_call[1]["publishOnPartialFailure"] is True


def test_manual_publish_replays_finalize_after_assets_only_execution() -> None:
    manifest = minimal_manifest()
    calls = []
    registry = HandlerRegistry()
    context, input_handler, document_handler, finalize_handler = runtime_context(
        manifest, registry, calls
    )
    registry.register("input_ingest", "1", input_handler)
    registry.register("document_parse", "1", document_handler("document_parse"))
    registry.register("segment_compile", "1", document_handler("segment_compile"))
    registry.register("asset_persist", "1", document_handler("asset_persist"))
    registry.register("mining_finalize", "1", finalize_handler)
    runtime = MiningWorkflowRuntime(context, run_id="run-1")

    context.services.execution_mode = "assets_only"
    assets = runtime.execute()
    context.services.execution_mode = "publish"
    published = runtime.publish()
    published_again = runtime.publish()

    assert "release_published" not in assets.capabilities
    assert "release_published" in published.capabilities
    assert "release_published" in published_again.capabilities
    assert [name for name, _ in calls].count("mining_finalize") == 2
    assert [name for name, _ in calls].count("input_ingest") == 2


def test_manual_publish_claim_conflict_stops_before_reading_input() -> None:
    manifest = minimal_manifest()
    calls = []
    registry = HandlerRegistry()
    context, input_handler, document_handler, finalize_handler = runtime_context(
        manifest, registry, calls
    )
    registry.register("input_ingest", "1", input_handler)
    registry.register("document_parse", "1", document_handler("document_parse"))
    registry.register("segment_compile", "1", document_handler("segment_compile"))
    registry.register("asset_persist", "1", document_handler("asset_persist"))
    registry.register("mining_finalize", "1", finalize_handler)
    runtime = MiningWorkflowRuntime(context, run_id="run-1")
    context.services.execution_mode = "assets_only"
    runtime.execute()
    calls_before_publish = list(calls)
    context.services.execution_mode = "publish"
    context.services.claim_manual_publish = lambda: False

    with pytest.raises(ValueError, match="claimed for publishing"):
        runtime.publish()

    assert calls == calls_before_publish


@pytest.mark.parametrize(
    ("engine", "expected"),
    [("legacy", "legacy"), ("workflow", "workflow")],
)
def test_existing_run_dispatch_uses_persisted_engine_not_submission_flag(
    monkeypatch, engine, expected
) -> None:
    calls = []
    monkeypatch.setenv("MINING_RUN_SUBMISSION_ENGINE", "legacy")
    monkeypatch.setattr(
        run_job, "_persisted_execution_engine", lambda **kwargs: engine
    )
    monkeypatch.setattr(
        run_job,
        "_run_legacy",
        lambda *args, **kwargs: calls.append("legacy") or {"status": "completed"},
    )
    monkeypatch.setattr(
        run_job,
        "_run_workflow_job",
        lambda *args, **kwargs: calls.append("workflow") or {"status": "completed"},
    )

    run_job.run("C:/incoming", domain="odn", run_id="run-1")

    assert calls == [expected]
