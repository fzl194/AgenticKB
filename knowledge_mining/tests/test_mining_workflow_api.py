from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from knowledge_mining.mining.api.routes.workflows import router
from knowledge_mining.mining.workflow.compiler import CompileError, WorkflowCompileException
from knowledge_mining.mining.workflow.service import (
    DraftRevisionConflict,
    WorkflowNotFound,
)


class FakeWorkflowService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def list(self, *, include_archived: bool = False) -> list[dict]:
        self.calls.append(("list", {"include_archived": include_archived}))
        return [{"id": "wf", "name": "demo", "current_version": 5}]

    async def create(self, **kwargs) -> dict:
        self.calls.append(("create", kwargs))
        return {"id": "wf", "draft_revision": 0, **kwargs}

    async def get(self, workflow_id: str) -> dict:
        self.calls.append(("get", {"workflow_id": workflow_id}))
        if workflow_id == "missing":
            raise WorkflowNotFound(workflow_id)
        return {
            "id": workflow_id,
            "draft_graph_json": {"nodes": [], "edges": [], "output": {}},
            "current_version": 5,
            "draft_revision": 7,
        }

    async def save_draft(self, workflow_id: str, **kwargs) -> dict:
        self.calls.append(("save_draft", {"workflow_id": workflow_id, **kwargs}))
        raise DraftRevisionConflict(workflow_id)

    async def validate_graph(self, graph: dict, *, mode: str = "draft") -> dict:
        self.calls.append(("validate_graph", {"graph": graph, "mode": mode}))
        return {"valid": True, "errors": [], "executionPlan": {}}

    async def publish(self, workflow_id: str, **kwargs) -> dict:
        self.calls.append(("publish", {"workflow_id": workflow_id, **kwargs}))
        if workflow_id == "invalid":
            raise WorkflowCompileException((
                CompileError("missing_capability", "entity input missing", node_id="graph"),
            ))
        return {"workflow_id": workflow_id, "version": 6, "graph_hash": "hash"}

    async def list_versions(self, workflow_id: str) -> list[dict]:
        self.calls.append(("list_versions", {"workflow_id": workflow_id}))
        return [{"workflow_id": workflow_id, "version": 5}]

    async def get_version(self, workflow_id: str, version: int) -> dict:
        self.calls.append((
            "get_version", {"workflow_id": workflow_id, "version": version}
        ))
        return {"workflow_id": workflow_id, "version": version}

    async def restore_draft(self, workflow_id: str, **kwargs) -> dict:
        self.calls.append(("restore_draft", {"workflow_id": workflow_id, **kwargs}))
        return {
            "id": workflow_id,
            "current_version": 5,
            "draft_revision": kwargs["expected_revision"] + 1,
        }

    async def clone(self, workflow_id: str, **kwargs) -> dict:
        self.calls.append(("clone", {"workflow_id": workflow_id, **kwargs}))
        return {"id": "clone", "name": kwargs["name"], "draft_revision": 0}

    async def archive(self, workflow_id: str, **kwargs) -> dict:
        self.calls.append(("archive", {"workflow_id": workflow_id, **kwargs}))
        return {"id": workflow_id, "status": "archived"}

    async def published_options(self) -> list[dict]:
        self.calls.append(("published_options", {}))
        return [{
            "id": "system-full-baseline",
            "current_version": 1,
            "is_system_default": True,
        }]


@pytest.fixture
def fake_workflow_service() -> FakeWorkflowService:
    return FakeWorkflowService()


def client_for(fake_service: FakeWorkflowService) -> TestClient:
    app = FastAPI()
    app.state.workflow_service = fake_service
    app.include_router(router)
    return TestClient(app)


def test_catalog_has_6_formal_operators(
    fake_workflow_service: FakeWorkflowService,
) -> None:
    """批次8 M0：正式目录收敛到零 LLM 默认线骨架 6 算子。"""
    response = client_for(fake_workflow_service).get(
        "/api/mining-operators/catalog?domain=odn"
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == 9
    assert fake_workflow_service.calls == []


def test_list_and_options_are_global(
    fake_workflow_service: FakeWorkflowService,
) -> None:
    client = client_for(fake_workflow_service)

    listed = client.get("/api/mining-workflows?domain=plant-a&include_archived=true")
    options = client.get("/api/mining-workflows/options?domain=plant-b")

    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == "wf"
    assert options.status_code == 200
    assert options.json()["items"][0]["id"] == "system-full-baseline"
    assert fake_workflow_service.calls == [
        ("list", {"include_archived": True}),
        ("published_options", {}),
    ]


def test_create_uses_strict_request_model(
    fake_workflow_service: FakeWorkflowService,
) -> None:
    client = client_for(fake_workflow_service)
    response = client.post(
        "/api/mining-workflows",
        json={"name": "demo", "template_key": "minimal", "domain": "forbidden"},
    )

    assert response.status_code == 422
    assert fake_workflow_service.calls == []


@pytest.mark.parametrize(
    "template_key",
    [
        "minimal",
        "fast_retrieval",
        "discourse_only",
        "entity_graph",
        "hybrid_knowledge",
        "ontology_only",
        "full",
    ],
)
def test_create_accepts_all_seven_paradigm_template_keys(
    fake_workflow_service: FakeWorkflowService,
    template_key: str,
) -> None:
    response = client_for(fake_workflow_service).post(
        "/api/mining-workflows",
        json={"name": f"workflow-{template_key}", "template_key": template_key},
    )

    assert response.status_code == 201
    assert response.json()["template_key"] == template_key


def test_save_draft_requires_expected_revision(
    fake_workflow_service: FakeWorkflowService,
) -> None:
    response = client_for(fake_workflow_service).put(
        "/api/mining-workflows/wf/draft",
        json={"graph": {}, "expected_revision": 4},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "draft_revision_conflict"


def test_validate_can_use_the_saved_draft(
    fake_workflow_service: FakeWorkflowService,
) -> None:
    response = client_for(fake_workflow_service).post(
        "/api/mining-workflows/wf/validate", json={}
    )

    assert response.status_code == 200
    assert fake_workflow_service.calls == [
        ("get", {"workflow_id": "wf"}),
        (
            "validate_graph",
            {
                "graph": {"nodes": [], "edges": [], "output": {}},
                "mode": "publish",
            },
        ),
    ]


def test_publish_maps_compile_errors_to_structured_422(
    fake_workflow_service: FakeWorkflowService,
) -> None:
    response = client_for(fake_workflow_service).post(
        "/api/mining-workflows/invalid/publish",
        json={"expected_revision": 7},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "workflow_compile_failed",
        "message": "Workflow compilation failed",
        "details": {
            "errors": [{
                "kind": "missing_capability",
                "message": "entity input missing",
                "nodeId": "graph",
            }]
        },
    }


def test_restore_version_creates_draft_and_does_not_switch_current(
    fake_workflow_service: FakeWorkflowService,
) -> None:
    response = client_for(fake_workflow_service).post(
        "/api/mining-workflows/wf/versions/2/restore-draft",
        json={"expected_revision": 7},
    )

    assert response.status_code == 200
    assert response.json()["current_version"] == 5
    assert response.json()["draft_revision"] == 8


def test_missing_workflow_maps_to_structured_404(
    fake_workflow_service: FakeWorkflowService,
) -> None:
    response = client_for(fake_workflow_service).get(
        "/api/mining-workflows/missing"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "workflow_not_found"


def test_versions_clone_and_archive_contracts(
    fake_workflow_service: FakeWorkflowService,
) -> None:
    client = client_for(fake_workflow_service)

    assert client.get("/api/mining-workflows/wf/versions").status_code == 200
    assert client.get("/api/mining-workflows/wf/versions/2").json()["version"] == 2
    assert client.post(
        "/api/mining-workflows/wf/clone",
        json={"name": "copy", "source_version": 2},
    ).json()["name"] == "copy"
    assert client.post(
        "/api/mining-workflows/wf/archive", json={"updated_by": "tester"}
    ).json()["status"] == "archived"
