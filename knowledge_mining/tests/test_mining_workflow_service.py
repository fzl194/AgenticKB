from __future__ import annotations

from copy import deepcopy

import pytest

from knowledge_mining.mining.workflow.service import (
    DraftRevisionConflict,
    WorkflowArchived,
    WorkflowNotFound,
    WorkflowService,
)
from knowledge_mining.mining.workflow.templates import builtin_templates


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


class DefaultCreateRaceRepository(MemoryWorkflowRepository):
    def __init__(self) -> None:
        super().__init__()
        self.simulated = False

    async def insert_workflow(self, record: dict) -> dict:
        if record["id"] == "system-full-baseline" and not self.simulated:
            self.simulated = True
            self.workflows[record["id"]] = deepcopy(record)
            raise RuntimeError("duplicate workflow")
        return await super().insert_workflow(record)


class DefaultPublishRaceRepository(MemoryWorkflowRepository):
    def __init__(self) -> None:
        super().__init__()
        self.simulated = False

    async def insert_version_and_advance(
        self,
        workflow_id: str,
        *,
        expected_revision: int,
        version_record: dict,
    ) -> dict | None:
        if workflow_id == "system-full-baseline" and not self.simulated:
            self.simulated = True
            stored = deepcopy(version_record)
            stored["workflow_id"] = workflow_id
            self.versions[(workflow_id, version_record["version"])] = stored
            self.workflows[workflow_id]["current_version"] = version_record["version"]
            return None
        return await super().insert_version_and_advance(
            workflow_id,
            expected_revision=expected_revision,
            version_record=version_record,
        )


class FailFirstParadigmPublishRepository(MemoryWorkflowRepository):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def insert_version_and_advance(
        self,
        workflow_id: str,
        *,
        expected_revision: int,
        version_record: dict,
    ) -> dict | None:
        workflow = self.workflows[workflow_id]
        if workflow["name"] == "基础文档入库" and not self.failed:
            self.failed = True
            raise RuntimeError("simulated publish outage")
        return await super().insert_version_and_advance(
            workflow_id,
            expected_revision=expected_revision,
            version_record=version_record,
        )


@pytest.fixture
def memory_workflow_repo() -> MemoryWorkflowRepository:
    return MemoryWorkflowRepository()


@pytest.mark.asyncio
async def test_publish_is_immutable_and_restore_creates_a_new_draft(
    memory_workflow_repo: MemoryWorkflowRepository,
) -> None:
    service = WorkflowService(memory_workflow_repo)
    created = await service.create(
        name="custom", description="demo", template_key="minimal", created_by="tester"
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
    created = await service.create(name="custom", template_key="minimal")
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
async def test_seed_creates_one_global_full_default_idempotently(
    memory_workflow_repo: MemoryWorkflowRepository,
) -> None:
    service = WorkflowService(memory_workflow_repo)

    await service.ensure_system_workflows()
    await service.ensure_system_workflows()

    items = await service.list(include_archived=True)
    defaults = [item for item in items if item["is_system_default"]]
    assert [(item["id"], item["current_version"]) for item in defaults] == [
        ("system-full-baseline", 1)
    ]
    assert len(
        (await service.get_version("system-full-baseline", 1))["graph_json"]["nodes"]
    ) == 16


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repository",
    [DefaultCreateRaceRepository(), DefaultPublishRaceRepository()],
)
async def test_system_default_tolerates_concurrent_create_and_publish_winners(
    repository: MemoryWorkflowRepository,
) -> None:
    service = WorkflowService(repository)

    workflow = await service.ensure_system_workflows()

    assert workflow["id"] == "system-full-baseline"
    assert workflow["current_version"] == 1


@pytest.mark.asyncio
async def test_workflow_library_seeds_six_published_ordinary_paradigms_once(
    memory_workflow_repo: MemoryWorkflowRepository,
) -> None:
    service = WorkflowService(memory_workflow_repo)

    await service.ensure_workflow_library()
    await service.ensure_workflow_library()

    items = await service.list(include_archived=True)
    ordinary = [item for item in items if not item["is_system_default"]]
    assert {item["name"] for item in ordinary} == {
        "基础文档入库",
        "快速向量检索",
        "篇章增强检索",
        "固定本体图谱构建",
        "检索与图谱联合构建",
        "本体演化专项",
    }
    assert all(item["is_system"] is False for item in ordinary)
    assert all(item["is_system_default"] is False for item in ordinary)
    assert all(item["current_version"] == 1 for item in ordinary)
    for item in ordinary:
        assert len(await service.list_versions(item["id"])) == 1
    assert len(await service.published_options()) == 7
    expected_templates = {
        "基础文档入库": "minimal",
        "快速向量检索": "fast_retrieval",
        "篇章增强检索": "discourse_only",
        "固定本体图谱构建": "entity_graph",
        "检索与图谱联合构建": "hybrid_knowledge",
        "本体演化专项": "ontology_only",
    }
    for item in ordinary:
        version = await service.get_version(item["id"], 1)
        assert version["graph_json"] == builtin_templates()[
            expected_templates[item["name"]]
        ].to_dict()


@pytest.mark.asyncio
async def test_workflow_library_resumes_its_own_unpublished_seed_after_failure() -> None:
    repository = FailFirstParadigmPublishRepository()
    service = WorkflowService(repository)

    with pytest.raises(RuntimeError, match="simulated publish outage"):
        await service.ensure_workflow_library()

    partial = await repository.get_by_name("基础文档入库")
    assert partial is not None
    assert partial["current_version"] is None
    assert partial["metadata_json"] == {"workflowParadigmSeed": "minimal"}

    await service.ensure_workflow_library()

    recovered = await service.get(partial["id"])
    assert recovered["current_version"] == 1
    assert len(await service.list(include_archived=True)) == 7


@pytest.mark.asyncio
async def test_workflow_library_preserves_existing_same_name_and_archived_items(
    memory_workflow_repo: MemoryWorkflowRepository,
) -> None:
    service = WorkflowService(memory_workflow_repo)
    existing = await service.create(
        name="基础文档入库",
        description="人工维护的同名草稿",
        template_key="minimal",
        created_by="owner",
    )

    await service.ensure_workflow_library()

    preserved = await service.get(existing["id"])
    assert preserved["description"] == "人工维护的同名草稿"
    assert preserved["current_version"] is None
    fast = next(
        item
        for item in await service.list(include_archived=True)
        if item["name"] == "快速向量检索"
    )
    await service.archive(fast["id"], updated_by="owner")

    await service.ensure_workflow_library()

    archived = await service.get(fast["id"])
    assert archived["status"] == "archived"
    assert len(await service.list_versions(fast["id"])) == 1


@pytest.mark.asyncio
async def test_clone_can_start_from_an_exact_historical_version(
    memory_workflow_repo: MemoryWorkflowRepository,
) -> None:
    service = WorkflowService(memory_workflow_repo)
    source = await service.create(name="source", template_key="minimal")
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
    await service.ensure_system_workflows()

    with pytest.raises(WorkflowArchived):
        await service.archive("system-full-baseline", updated_by="tester")


@pytest.mark.asyncio
async def test_published_options_and_exact_version_resolution(
    memory_workflow_repo: MemoryWorkflowRepository,
) -> None:
    service = WorkflowService(memory_workflow_repo)
    await service.ensure_system_workflows()
    custom = await service.create(name="custom", template_key="minimal")
    await service.publish(custom["id"], expected_revision=0)
    draft_only = await service.create(name="draft-only", template_key="minimal")

    options = await service.published_options()
    exact = await service.resolve_published_version(
        workflow_id=custom["id"], workflow_version=1
    )
    default = await service.resolve_published_version(
        workflow_id=None,
        workflow_version=None,
        default_workflow_id="system-full-baseline",
    )

    assert {item["id"] for item in options} == {
        "system-full-baseline",
        custom["id"],
    }
    assert exact["workflow_id"] == custom["id"]
    assert exact["version"] == 1
    assert default["workflow_id"] == "system-full-baseline"
    with pytest.raises(WorkflowNotFound):
        await service.resolve_published_version(
            workflow_id=draft_only["id"], workflow_version=None
        )


@pytest.mark.asyncio
async def test_create_with_schema_version_2_uses_split_parse_template(
    memory_workflow_repo: MemoryWorkflowRepository,
):
    """M6：新建工作流可选 v2 骨架（document_parse→segment_compile）."""
    service = WorkflowService(memory_workflow_repo)
    created = await service.create(
        name="v2-chain", template_key="minimal", schema_version="2.0",
    )
    draft = created["draft_graph_json"]  # memory repo 保 dict；PG 为 str
    if isinstance(draft, str):
        import json as _j

        draft = _j.loads(draft)
    types = {n["operatorType"] for n in draft["nodes"]}
    assert "document_parse" in types and "segment_compile" in types
    assert "parse_segment" not in types
    assert draft["schemaVersion"] == "2.0"
    # v1 默认不变
    v1 = await WorkflowService(
        MemoryWorkflowRepository()
    ).create(name="v1-chain", template_key="minimal")
    v1_draft = v1["draft_graph_json"]
    if isinstance(v1_draft, str):
        import json as _j

        v1_draft = _j.loads(v1_draft)
    v1_types = {n["operatorType"] for n in v1_draft["nodes"]}
    assert "parse_segment" in v1_types
    assert "document_parse" not in v1_types
