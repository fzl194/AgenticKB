from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from .manifest import bind_run_manifest


@dataclass(frozen=True)
class WorkflowRunBinding:
    workflow_id: str
    workflow_version: int
    workflow_version_id: str
    graph_hash: str
    manifest: dict[str, Any]


class WorkflowRunBinder:
    def __init__(
        self,
        workflow_service: Any,
        ontology_lookup: Callable[[str], Any],
        config_fingerprint: Callable[[], str],
    ) -> None:
        self.workflow_service = workflow_service
        self.ontology_lookup = ontology_lookup
        self.config_fingerprint = config_fingerprint

    async def resolve(
        self,
        *,
        workflow_id: str | None,
        workflow_version: int | None,
        domain: str,
        channel: str,
        upload_batch_id: str | None,
        run_overrides: dict[str, Any] | None = None,
    ) -> WorkflowRunBinding:
        from .presets import DEFAULT_WORKFLOW_ID

        version = await self.workflow_service.resolve_published_version(
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            default_workflow_id=DEFAULT_WORKFLOW_ID,
        )
        ontology_version_id = await self.ontology_lookup(domain)
        manifest = bind_run_manifest(
            version["compiled_manifest_json"],
            domain=domain,
            channel=channel,
            ontology_version_id=ontology_version_id,
            upload_batch_id=upload_batch_id,
            config_fingerprint=self.config_fingerprint(),
        )
        manifest["workflowName"] = version.get("workflow_name")
        manifest["templateKey"] = version.get("template_key")
        manifest["runOverrides"] = deepcopy(run_overrides or {})
        return WorkflowRunBinding(
            workflow_id=version["workflow_id"],
            workflow_version=version["version"],
            workflow_version_id=version["id"],
            graph_hash=version["graph_hash"],
            manifest=manifest,
        )
