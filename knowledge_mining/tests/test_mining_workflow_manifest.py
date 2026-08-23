from copy import deepcopy

from knowledge_mining.mining.workflow.compiler import WorkflowCompiler
from knowledge_mining.mining.workflow.manifest import (
    bind_run_manifest,
    canonical_json,
    graph_hash,
    value_hash,
)
from knowledge_mining.mining.workflow.operators.catalog import builtin_catalog
from knowledge_mining.mining.workflow.templates import builtin_templates


def _full_plan():
    return WorkflowCompiler(builtin_catalog()).compile(
        builtin_templates()["full"], mode="publish"
    ).require_plan()


def test_publish_hash_is_deterministic_and_run_binding_does_not_change_it() -> None:
    graph = builtin_templates()["full"]
    plan = WorkflowCompiler(builtin_catalog()).compile(
        graph, mode="publish"
    ).require_plan()
    published = plan.to_manifest(workflow_id="wf", workflow_version=3)
    original = deepcopy(published)

    bound = bind_run_manifest(
        published,
        domain="odn",
        channel="prod",
        ontology_version_id="ont-1",
        upload_batch_id="batch-1",
        config_fingerprint="cfg-hash",
    )

    assert published == original
    assert published["graphHash"] == graph_hash(graph)
    assert bound["graphHash"] == published["graphHash"]
    assert "runtimeBinding" not in published
    assert bound["runtimeBinding"]["domain"] == "odn"
    assert bound["runtimeBinding"]["ontologyVersionId"] == "ont-1"
    assert bound["runtimeBinding"]["ontologyApplicable"] is True


def test_manifest_serializes_frozen_plan_and_parameter_hashes() -> None:
    manifest = _full_plan().to_manifest(workflow_id="wf", workflow_version=7)
    assert manifest["workflowId"] == "wf"
    assert manifest["workflowVersion"] == 7
    assert manifest["schemaVersion"] == "2.0"
    assert manifest["catalogVersion"] == "1"
    assert manifest["executionPlan"]["documentOrder"][-1] == "asset_persist"
    parse = next(node for node in manifest["nodes"] if node["type"] == "segment_compile")
    assert parse["params"]["maxTokens"] == 512
    assert parse["paramsHash"] == value_hash(parse["params"])
    assert "domain" not in canonical_json(manifest).lower()


def test_binding_without_ontology_marks_the_global_graph_not_applicable() -> None:
    published = _full_plan().to_manifest(workflow_id="wf", workflow_version=1)
    bound = bind_run_manifest(
        published,
        domain="generic",
        channel="prod",
        ontology_version_id=None,
        upload_batch_id="batch-2",
        config_fingerprint="cfg-hash",
    )
    assert bound["runtimeBinding"]["ontologyApplicable"] is False


def test_canonical_json_and_hash_ignore_mapping_insertion_order() -> None:
    left = {"a": 1, "b": {"x": 2, "y": 3}}
    right = {"b": {"y": 3, "x": 2}, "a": 1}
    assert canonical_json(left) == canonical_json(right)
    assert value_hash(left) == value_hash(right)
