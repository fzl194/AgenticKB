from __future__ import annotations

from copy import deepcopy

import pytest

from knowledge_mining.mining.workflow.service import (
    DraftRevisionConflict,
    WorkflowArchived,
    WorkflowNotFound,
    WorkflowService,
)
from knowledge_mining.tests.formal_chain_helper import formal_chain_graph_dict


class MemoryWorkflowRepository:
    def __init__(self) -> None:
        self.workflows: dict[str, dict] = {}
        self.versions: dict[tuple[str, int], dict] = {}

    async def list_workflows(self, *, include_archived: bool = False) -> list[dict]:
        items = self.workflows.values()
        if not include_archived:
            items = (item for item in items if item["status"] == "active")
        return deepcopy(sorted(items, key=lambda item: item["name"]))

    async def get_workflow(self, workflow_id: str) -> dict | None:
        return deepcopy(self.workflows.get(workflow_id))

    async def get_by_name(self, name: str) -> dict | None:
        return deepcopy(next(
            (item for item in self.workflows.values() if item["name"] == name),
            None,
        ))

    async def insert_workflow(self, record: dict) -> dict:
        if record["id"] in self.workflows or await self.get_by_name(record["name"]):
            raise RuntimeError("duplicate workflow")
        self.workflows[record["id"]] = deepcopy(record)
        return deepcopy(record)

    async def update_draft(
        self,
        workflow_id: str,
        *,
        graph: dict,
        expected_revision: int,
        updated_by: str | None,
    ) -> dict | None:
        item = self.workflows.get(workflow_id)
        if (
            item is None
            or item["status"] != "active"
            or item["draft_revision"] != expected_revision
        ):
            return None
        item["draft_graph_json"] = deepcopy(graph)
        item["draft_revision"] += 1
        item["updated_by"] = updated_by
        return deepcopy(item)

    async def insert_version_and_advance(
        self,
        workflow_id: str,
        *,
        expected_revision: int,
        version_record: dict,
    ) -> dict | None:
        item = self.workflows.get(workflow_id)
        if (
            item is None
            or item["status"] != "active"
            or item["draft_revision"] != expected_revision
        ):
            return None
        next_version = (item["current_version"] or 0) + 1
        if version_record["version"] != next_version:
            raise AssertionError("service did not allocate the next version")
        stored = deepcopy(version_record)
        stored["workflow_id"] = workflow_id
        self.versions[(workflow_id, next_version)] = stored
        item["current_version"] = next_version
        return deepcopy(stored)

    async def list_versions(self, workflow_id: str) -> list[dict]:
        return deepcopy([
            value
            for (owner, _), value in sorted(
                self.versions.items(), key=lambda pair: pair[0][1], reverse=True
            )
            if owner == workflow_id
        ])

    async def get_version(self, workflow_id: str, version: int) -> dict | None:
        return deepcopy(self.versions.get((workflow_id, version)))

    async def archive(
        self, workflow_id: str, *, updated_by: str | None
    ) -> dict | None:
        item = self.workflows.get(workflow_id)
        if item is None or item["status"] != "active":
            return None
        item["status"] = "archived"
        item["updated_by"] = updated_by
        return deepcopy(item)


@pytest.fixture
def memory_workflow_repo() -> MemoryWorkflowRepository:
    return MemoryWorkflowRepository()


@pytest.mark.asyncio
async def test_upgrade_active_workflows_to_v2_preserves_identity_and_publishes_new_version(
    memory_workflow_repo: MemoryWorkflowRepository,
) -> None:
    service = WorkflowService(memory_workflow_repo)
    from knowledge_mining.tests.test_workflow_v2_migration import _v1_graph

    created = await memory_workflow_repo.insert_workflow({
        "id": "existing-v1", "name": "existing-v1", "description": None,
        "status": "active", "draft_graph_json": _v1_graph(),
        "draft_revision": 0, "current_version": 1, "is_system": False,
        "is_system_default": False, "created_by": None, "updated_by": None,
        "metadata_json": {},
    })

    upgraded = await service.upgrade_active_workflows_to_v2(
        updated_by="v2-rollout",
    )

    assert upgraded == [created["id"]]
    workflow = await service.get(created["id"])
    assert workflow["draft_graph_json"]["schemaVersion"] == "2.0"
    assert workflow["current_version"] == 2
    published = await service.get_version(created["id"], 2)
    assert published["schema_version"] == "2.0"
    assert "parse_segment" not in {
        node["operatorType"] for node in published["graph_json"]["nodes"]
    }


@pytest.mark.asyncio
async def test_publish_is_immutable_and_restore_creates_a_new_draft(
    memory_workflow_repo: MemoryWorkflowRepository,
) -> None:
    service = WorkflowService(memory_workflow_repo)
    created = await service.create(
        name="custom", description="demo",
        graph=formal_chain_graph_dict(), created_by="tester",
    )
    v1 = await service.publish(
        created["id"],
        expected_revision=created["draft_revision"],
        release_notes="first",
        created_by="tester",
    )
    published_graph = deepcopy(v1["graph_json"])

    restored = await service.restore_draft(
        created["id"],
        version=1,
        expected_revision=created["draft_revision"],
        updated_by="tester",
    )
    restored["draft_graph_json"]["nodes"][0]["params"]["clientMutation"] = True

    assert restored["draft_revision"] == created["draft_revision"] + 1
    assert (await service.get_version(created["id"], 1))["graph_json"] == published_graph
    assert (await service.get(created["id"]))["current_version"] == 1


@pytest.mark.asyncio
async def test_stale_draft_revision_is_rejected(
    memory_workflow_repo: MemoryWorkflowRepository,
) -> None:
    service = WorkflowService(memory_workflow_repo)
    created = await service.create(name="custom", graph=formal_chain_graph_dict())
    await service.save_draft(
        created["id"],
        graph=created["draft_graph_json"],
        expected_revision=0,
        updated_by="a",
    )
    with pytest.raises(DraftRevisionConflict):
        await service.save_draft(
            created["id"],
            graph=created["draft_graph_json"],
            expected_revision=0,
            updated_by="b",
        )


@pytest.mark.asyncio
async def test_ensure_workflow_library_seeds_four_presets_and_preserves_user_workflows(
    memory_workflow_repo: MemoryWorkflowRepository,
) -> None:
    """批次8 M6：seed 恰好 4 套官方预置（M6_presets 测试展开断言）；
    用户已有 Workflow 不被触碰；旧 system-full-baseline 永不复活。"""
    service = WorkflowService(memory_workflow_repo)
    existing = await service.create(
        name="我的自定义范式",
        description="人工维护草稿",
        graph=formal_chain_graph_dict(),
        created_by="owner",
    )

    default = await service.ensure_workflow_library()
    await service.ensure_workflow_library()

    assert default is not None and default["id"] == "system-hybrid-assets"
    preserved = await service.get(existing["id"])
    assert preserved["current_version"] is None
    ids = {item["id"] for item in await service.list(include_archived=True)}
    assert "system-full-baseline" not in ids
    assert len(ids) == 5  # 4 预置 + 1 用户草稿


@pytest.mark.asyncio
async def test_clone_can_start_from_an_exact_historical_version(
    memory_workflow_repo: MemoryWorkflowRepository,
) -> None:
    service = WorkflowService(memory_workflow_repo)
    source = await service.create(name="source", graph=formal_chain_graph_dict())
    await service.publish(source["id"], expected_revision=0)

    clone = await service.clone(
        source["id"], name="clone", source_version=1, created_by="tester"
    )

    assert clone["name"] == "clone"
    assert clone["current_version"] is None
    assert clone["draft_graph_json"] == (
        await service.get_version(source["id"], 1)
    )["graph_json"]


@pytest.mark.asyncio
async def test_system_default_cannot_be_archived(
    memory_workflow_repo: MemoryWorkflowRepository,
) -> None:
    service = WorkflowService(memory_workflow_repo)
    created = await service.create(
        name="system-default",
        graph=formal_chain_graph_dict(),
        is_system=True,
        is_system_default=True,
    )

    with pytest.raises(WorkflowArchived):
        await service.archive(created["id"], updated_by="tester")


@pytest.mark.asyncio
async def test_published_options_and_exact_version_resolution(
    memory_workflow_repo: MemoryWorkflowRepository,
) -> None:
    service = WorkflowService(memory_workflow_repo)
    system_default = await service.create(
        name="system-default",
        workflow_id="system-default",
        graph=formal_chain_graph_dict(),
        is_system=True,
        is_system_default=True,
    )
    await service.publish(system_default["id"], expected_revision=0)
    custom = await service.create(name="custom", graph=formal_chain_graph_dict())
    await service.publish(custom["id"], expected_revision=0)
    draft_only = await service.create(name="draft-only", graph=formal_chain_graph_dict())

    options = await service.published_options()
    exact = await service.resolve_published_version(
        workflow_id=custom["id"], workflow_version=1
    )
    default = await service.resolve_published_version(
        workflow_id=None,
        workflow_version=None,
        default_workflow_id="system-default",
    )

    assert {item["id"] for item in options} == {
        "system-default",
        custom["id"],
    }
    assert exact["workflow_id"] == custom["id"]
    assert exact["version"] == 1
    assert default["workflow_id"] == "system-default"
    with pytest.raises(WorkflowNotFound):
        await service.resolve_published_version(
            workflow_id=draft_only["id"], workflow_version=None
        )


@pytest.mark.asyncio
async def test_create_requires_explicit_graph_until_m6_presets_land(
    memory_workflow_repo: MemoryWorkflowRepository,
):
    """M0 后无内置模板：不给 graph 必须显式失败；显式链图正常建。"""
    service = WorkflowService(memory_workflow_repo)

    with pytest.raises(ValueError, match="No builtin template"):
        await service.create(name="no-template")

    created = await service.create(
        name="v2-chain", graph=formal_chain_graph_dict(), schema_version="2.0",
    )
    draft = created["draft_graph_json"]
    if isinstance(draft, str):
        import json as _j

        draft = _j.loads(draft)
    types = {n["operatorType"] for n in draft["nodes"]}
    assert "document_parse" in types and "segment_compile" in types
    assert "parse_segment" not in types
    assert draft["schemaVersion"] == "2.0"


@pytest.mark.asyncio
async def test_save_draft_self_heals_schema_version_mismatch(
    memory_workflow_repo: MemoryWorkflowRepository,
):
    """错误标记版本的 v2 图保存时固定回唯一支持的 2.0。"""
    import json as _j

    service = WorkflowService(memory_workflow_repo)
    created = await service.create(
        name="heal", graph=formal_chain_graph_dict(), schema_version="2.0",
    )
    bad = _j.loads(created["draft_graph_json"]) if isinstance(
        created["draft_graph_json"], str) else dict(created["draft_graph_json"])
    bad["schemaVersion"] = "1.0"  # 模拟旧前端污染
    healed = await service.save_draft(
        created["id"], graph=bad, expected_revision=created["draft_revision"],
    )
    draft = healed["draft_graph_json"]
    if isinstance(draft, str):
        draft = _j.loads(draft)
    assert draft["schemaVersion"] == "2.0"
    types = {n["operatorType"] for n in draft["nodes"]}
    assert "document_parse" in types
