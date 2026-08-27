from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from knowledge_mining.mining.workflow.run_binding import WorkflowRunBinder


class FakeWorkflowService:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.current_versions = {
            "system-full-baseline": 4,
            "wf": 7,
        }

    async def resolve_published_version(
        self,
        *,
        workflow_id: str | None,
        workflow_version: int | None,
        default_workflow_id: str,
    ) -> dict:
        self.calls.append({
            "workflow_id": workflow_id,
            "workflow_version": workflow_version,
            "default_workflow_id": default_workflow_id,
        })
        selected_id = workflow_id or default_workflow_id
        selected_version = workflow_version or self.current_versions[selected_id]
        manifest = {
            "schemaVersion": "1.0",
            "catalogVersion": "1",
            "workflowId": selected_id,
            "workflowVersion": selected_version,
            "graphHash": f"hash-{selected_id}-{selected_version}",
            "graph": {"nodes": [], "edges": [], "output": {}},
            "nodes": [],
            "edges": [],
            "executionPlan": {},
        }
        return {
            "id": f"version-{selected_id}-{selected_version}",
            "workflow_id": selected_id,
            "version": selected_version,
            "graph_hash": manifest["graphHash"],
            "compiled_manifest_json": manifest,
        }


@pytest.fixture
def fake_binding_deps() -> dict:
    async def ontology_lookup(domain: str) -> str | None:
        assert domain == "odn"
        return "ontology-v3"

    return {
        "workflow_service": FakeWorkflowService(),
        "ontology_lookup": ontology_lookup,
        "config_fingerprint": lambda: "config-hash",
    }


@pytest.mark.asyncio
async def test_missing_workflow_binds_system_full_current_version(
    fake_binding_deps: dict,
) -> None:
    binder = WorkflowRunBinder(**fake_binding_deps)

    binding = await binder.resolve(
        workflow_id=None,
        workflow_version=None,
        domain="odn",
        channel="prod",
        upload_batch_id="abcdef123456",
    )

    assert binding.workflow_id == "system-full-baseline"
    assert binding.workflow_version == 4
    assert binding.manifest["runtimeBinding"] == {
        "domain": "odn",
        "channel": "prod",
        "ontologyVersionId": "ontology-v3",
        "ontologyApplicable": True,
        "uploadBatchId": "abcdef123456",
        "configFingerprint": "config-hash",
    }


@pytest.mark.asyncio
async def test_exact_version_is_not_replaced_by_current(
    fake_binding_deps: dict,
) -> None:
    binder = WorkflowRunBinder(**fake_binding_deps)

    binding = await binder.resolve(
        workflow_id="wf",
        workflow_version=2,
        domain="odn",
        channel="prod",
        upload_batch_id=None,
    )

    assert binding.workflow_version == 2
    assert binding.workflow_version_id == "version-wf-2"
    assert binding.graph_hash == "hash-wf-2"


@pytest.mark.asyncio
async def test_run_overrides_are_frozen_without_mutating_published_manifest(
    fake_binding_deps: dict,
) -> None:
    service = fake_binding_deps["workflow_service"]
    original_resolve = service.resolve_published_version
    published_seen: list[dict] = []

    async def capture(**kwargs):
        version = await original_resolve(**kwargs)
        published_seen.append(deepcopy(version["compiled_manifest_json"]))
        return version

    service.resolve_published_version = capture
    binder = WorkflowRunBinder(**fake_binding_deps)

    binding = await binder.resolve(
        workflow_id="wf",
        workflow_version=2,
        domain="odn",
        channel="prod",
        upload_batch_id=None,
        run_overrides={
            "maxWorkers": 8,
            "executionMode": "assets_only",
            "publishOnPartialFailure": True,
        },
    )

    assert binding.manifest["runOverrides"] == {
        "maxWorkers": 8,
        "executionMode": "assets_only",
        "publishOnPartialFailure": True,
    }
    assert "runtimeBinding" not in published_seen[0]
    assert "runOverrides" not in published_seen[0]
