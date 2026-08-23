from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any, Literal

from psycopg.errors import UniqueViolation

from .compiler import WorkflowCompileException, WorkflowCompiler
from .graph import WorkflowGraph
from .operators.catalog import builtin_catalog
from .paradigms import ORDINARY_WORKFLOW_PARADIGMS
from .templates import builtin_templates
from .v2_migration import upgrade_graph_to_v2


def _self_heal_schema_version(graph: dict) -> dict:
    """Normalize the only supported persisted workflow schema."""
    nodes = graph.get("nodes") or []
    types = {str(n.get("operatorType", "")) for n in nodes}
    if "parse_segment" in types:
        raise ValueError(
            "legacy parse_segment is not supported; migrate the workflow to v2"
        )
    normalized = dict(graph)
    normalized["schemaVersion"] = "2.0"
    return normalized


def _build_initial_draft(
    *,
    template_key: str,
    schema_version: str,
    graph: dict | None,
) -> dict:
    """create 的初始草稿：显式图优先，否则按版本选模板（M6 v2）."""
    if graph is not None:
        return WorkflowGraph.from_dict(graph).to_dict()
    if str(schema_version) != "2.0":
        raise ValueError("Only workflow schema_version '2.0' is supported")
    templates = builtin_templates()
    try:
        return templates[template_key].to_dict()
    except KeyError as exc:
        raise ValueError(f"Unknown template: {template_key}") from exc


async def _insert_workflow_record(
    service: "WorkflowService",
    *,
    normalized_name: str,
    description: str | None,
    draft: dict,
    workflow_id: str | None,
    is_system: bool,
    is_system_default: bool,
    created_by: str | None,
    metadata_json: dict[str, Any] | None,
) -> dict:
    record = {
        "id": workflow_id or uuid.uuid4().hex,
        "name": normalized_name,
        "description": description,
        "status": "active",
        "draft_graph_json": draft,
        "draft_revision": 0,
        "current_version": None,
        "is_system": is_system,
        "is_system_default": is_system_default,
        "created_by": created_by,
        "updated_by": created_by,
        "metadata_json": deepcopy(metadata_json or {}),
    }
    try:
        return await service.repository.insert_workflow(record)
    except UniqueViolation as exc:
        raise WorkflowNameConflict(normalized_name) from exc
    except RuntimeError as exc:
        if "duplicate" in str(exc).lower():
            raise WorkflowNameConflict(normalized_name) from exc
        raise


_PARADIGM_SEED_METADATA_KEY = "workflowParadigmSeed"


class WorkflowNotFound(LookupError):
    pass


class WorkflowArchived(ValueError):
    pass


class DraftRevisionConflict(RuntimeError):
    pass


class WorkflowNameConflict(RuntimeError):
    pass


class WorkflowService:
    def __init__(self, repository: Any, *, catalog=None, compiler=None) -> None:
        self.repository = repository
        self.catalog = catalog or builtin_catalog()
        self.compiler = compiler or WorkflowCompiler(self.catalog)

    async def validate_graph(
        self, graph: dict, *, mode: Literal["draft", "publish"] = "draft"
    ) -> dict:
        parsed = WorkflowGraph.from_dict(graph)
        return self.compiler.compile(parsed, mode=mode).to_dict()

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        template_key: str = "full",
        schema_version: str = "2.0",
        graph: dict | None = None,
        created_by: str | None = None,
        workflow_id: str | None = None,
        is_system: bool = False,
        is_system_default: bool = False,
        metadata_json: dict[str, Any] | None = None,
    ) -> dict:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Workflow name is required")
        if await self.repository.get_by_name(normalized_name) is not None:
            raise WorkflowNameConflict(normalized_name)
        draft = _build_initial_draft(
            template_key=template_key, schema_version=schema_version,
            graph=graph,
        )
        return await _insert_workflow_record(
            self,
            normalized_name=normalized_name, description=description,
            draft=draft, workflow_id=workflow_id, is_system=is_system,
            is_system_default=is_system_default, created_by=created_by,
            metadata_json=metadata_json,
        )


    async def get(self, workflow_id: str) -> dict:
        workflow = await self.repository.get_workflow(workflow_id)
        if workflow is None:
            raise WorkflowNotFound(workflow_id)
        return workflow

    async def list(self, *, include_archived: bool = False) -> list[dict]:
        return await self.repository.list_workflows(include_archived=include_archived)

    async def upgrade_active_workflows_to_v2(
        self, *, updated_by: str = "v2-rollout"
    ) -> list[str]:
        """Upgrade every active persisted paradigm and publish its v2 version.

        Workflow identity and prior versions remain intact for audit, but only
        the newly published v2 version is selected by future runs.  The method
        is idempotent: already-v2 drafts are left untouched.
        """
        upgraded_ids: list[str] = []
        for workflow in await self.repository.list_workflows(
            include_archived=False
        ):
            upgraded_graph = upgrade_graph_to_v2(
                dict(workflow["draft_graph_json"])
            )
            if upgraded_graph == workflow["draft_graph_json"]:
                continue
            updated = await self.repository.update_draft(
                workflow["id"],
                graph=upgraded_graph,
                expected_revision=workflow["draft_revision"],
                updated_by=updated_by,
            )
            if updated is None:
                raise DraftRevisionConflict(workflow["id"])
            await self.publish(
                workflow["id"],
                expected_revision=updated["draft_revision"],
                release_notes="统一升级至 v2 解析与切片范式",
                created_by=updated_by,
            )
            upgraded_ids.append(workflow["id"])
        return upgraded_ids

    async def save_draft(
        self,
        workflow_id: str,
        *,
        graph: dict,
        expected_revision: int,
        updated_by: str | None = None,
    ) -> dict:
        await self._require_active(workflow_id)
        normalized_graph = WorkflowGraph.from_dict(
            _self_heal_schema_version(graph)
        ).to_dict()
        updated = await self.repository.update_draft(
            workflow_id,
            graph=normalized_graph,
            expected_revision=expected_revision,
            updated_by=updated_by,
        )
        if updated is None:
            raise DraftRevisionConflict(workflow_id)
        return updated

    async def publish(
        self,
        workflow_id: str,
        *,
        expected_revision: int,
        release_notes: str | None = None,
        created_by: str | None = None,
    ) -> dict:
        workflow = await self._require_active(workflow_id)
        if workflow["draft_revision"] != expected_revision:
            raise DraftRevisionConflict(workflow_id)
        result = self.compiler.compile(
            WorkflowGraph.from_dict(workflow["draft_graph_json"]), mode="publish"
        )
        plan = result.require_plan()
        next_version = (workflow["current_version"] or 0) + 1
        manifest = plan.to_manifest(
            workflow_id=workflow_id, workflow_version=next_version
        )
        inserted = await self.repository.insert_version_and_advance(
            workflow_id,
            expected_revision=expected_revision,
            version_record={
                "id": uuid.uuid4().hex,
                "version": next_version,
                "graph_json": plan.graph.to_dict(),
                "compiled_manifest_json": manifest,
                "graph_hash": manifest["graphHash"],
                "schema_version": manifest["schemaVersion"],
                "operator_catalog_version": manifest["catalogVersion"],
                "release_notes": release_notes,
                "created_by": created_by,
                "metadata_json": {},
            },
        )
        if inserted is None:
            raise DraftRevisionConflict(workflow_id)
        return inserted

    async def clone(
        self,
        workflow_id: str,
        *,
        name: str,
        description: str | None = None,
        source_version: int | None = None,
        created_by: str | None = None,
    ) -> dict:
        source = await self.get(workflow_id)
        if source_version is None:
            graph = source["draft_graph_json"]
        else:
            graph = (await self.get_version(workflow_id, source_version))["graph_json"]
        return await self.create(
            name=name,
            description=description,
            graph=deepcopy(graph),
            created_by=created_by,
        )

    async def restore_draft(
        self,
        workflow_id: str,
        *,
        version: int,
        expected_revision: int,
        updated_by: str | None = None,
    ) -> dict:
        await self._require_active(workflow_id)
        historical = await self.get_version(workflow_id, version)
        return await self.save_draft(
            workflow_id,
            graph=historical["graph_json"],
            expected_revision=expected_revision,
            updated_by=updated_by,
        )

    async def archive(
        self, workflow_id: str, *, updated_by: str | None = None
    ) -> dict:
        workflow = await self._require_active(workflow_id)
        if workflow["is_system_default"]:
            raise WorkflowArchived("The system default Workflow cannot be archived")
        archived = await self.repository.archive(workflow_id, updated_by=updated_by)
        if archived is None:
            raise WorkflowArchived(workflow_id)
        return archived

    async def list_versions(self, workflow_id: str) -> list[dict]:
        await self.get(workflow_id)
        return await self.repository.list_versions(workflow_id)

    async def get_version(self, workflow_id: str, version: int) -> dict:
        await self.get(workflow_id)
        result = await self.repository.get_version(workflow_id, version)
        if result is None:
            raise WorkflowNotFound(f"{workflow_id}@{version}")
        return result

    async def published_options(self) -> list[dict]:
        workflows = await self.repository.list_workflows(include_archived=False)
        return [
            {
                "id": item["id"],
                "name": item["name"],
                "description": item.get("description"),
                "current_version": item["current_version"],
                "is_system_default": item["is_system_default"],
            }
            for item in workflows
            if item["current_version"] is not None
        ]

    async def resolve_published_version(
        self,
        *,
        workflow_id: str | None,
        workflow_version: int | None,
        default_workflow_id: str = "system-full-baseline",
    ) -> dict:
        selected_id = workflow_id or default_workflow_id
        workflow = await self._require_active(selected_id)
        selected_version = workflow_version or workflow["current_version"]
        if selected_version is None:
            raise WorkflowNotFound(f"{selected_id} has no published version")
        return await self.get_version(selected_id, selected_version)

    async def ensure_system_workflows(self) -> dict:
        workflow = await self.repository.get_workflow("system-full-baseline")
        if workflow is None:
            try:
                workflow = await self.create(
                    workflow_id="system-full-baseline",
                    name="system-full-baseline",
                    description="System FULL mining workflow baseline",
                    template_key="full",
                    is_system=True,
                    is_system_default=True,
                )
            except WorkflowNameConflict:
                workflow = await self.repository.get_workflow(
                    "system-full-baseline"
                )
                if workflow is None:
                    raise
        if workflow["current_version"] is None:
            workflow = await self._ensure_initial_publication(
                workflow,
                release_notes="Initial FULL baseline",
            )
        return workflow

    async def ensure_workflow_library(self) -> dict:
        """Create missing published paradigms without owning their lifecycle.

        The compatibility default remains a system Workflow. Every additional
        paradigm is inserted exactly like an ordinary user-created Workflow:
        it receives a regular generated ID and can be edited or archived. An
        existing name, including an archived one, is never changed or revived.
        """
        default = await self.ensure_system_workflows()
        for paradigm in ORDINARY_WORKFLOW_PARADIGMS:
            workflow = await self.repository.get_by_name(paradigm.name)
            if workflow is None:
                try:
                    workflow = await self.create(
                        name=paradigm.name,
                        description=paradigm.description,
                        template_key=paradigm.template_key,
                        metadata_json={
                            _PARADIGM_SEED_METADATA_KEY: paradigm.template_key
                        },
                    )
                except WorkflowNameConflict:
                    # A concurrent startup may have inserted the same name
                    # after the existence check. Only continue initialization
                    # when that winner is one of our own ordinary seeds.
                    workflow = await self.repository.get_by_name(paradigm.name)
            if workflow is None:
                continue
            if (
                workflow["status"] != "active"
                or workflow["current_version"] is not None
                or workflow.get("metadata_json", {}).get(
                    _PARADIGM_SEED_METADATA_KEY
                )
                != paradigm.template_key
            ):
                continue
            await self._ensure_initial_publication(
                workflow,
                release_notes="初始范式版本",
            )
        return default

    async def _ensure_initial_publication(
        self,
        workflow: dict,
        *,
        release_notes: str,
    ) -> dict:
        if workflow["current_version"] is not None:
            return workflow
        try:
            await self.publish(
                workflow["id"],
                expected_revision=workflow["draft_revision"],
                release_notes=release_notes,
            )
        except DraftRevisionConflict:
            # Another app instance may have won the publication transaction.
            # Only absorb the conflict when a published version now exists.
            refreshed = await self.get(workflow["id"])
            if refreshed["current_version"] is None:
                raise
            return refreshed
        return await self.get(workflow["id"])

    async def _require_active(self, workflow_id: str) -> dict:
        workflow = await self.get(workflow_id)
        if workflow["status"] != "active":
            raise WorkflowArchived(workflow_id)
        return workflow


__all__ = [
    "DraftRevisionConflict",
    "WorkflowArchived",
    "WorkflowCompileException",
    "WorkflowNameConflict",
    "WorkflowNotFound",
    "WorkflowService",
]
