"""Keep a parse source binding and isolate compiled snapshots before writing assets."""
from dataclasses import dataclass
from typing import Any

from knowledge_mining.mining.segment_compiler.service import SnapshotRecompileService


@dataclass(frozen=True)
class ParsedInputResult:
    run: Any
    frozen_input: Any

    def __getattr__(self, name: str) -> Any:
        return getattr(self.run, name)


class SnapshotCompilationCoordinator:
    def __init__(self, *, snapshots: Any, commit_service: Any, compiler: Any) -> None:
        self._snapshots = snapshots
        self._recompiler = SnapshotRecompileService(
            snapshots=snapshots, commit_service=commit_service, compile_service=compiler,
        )

    async def compile(self, snapshot_id: str, *, frozen_input: Any, policy: Any) -> Any:
        if frozen_input is None:
            raise ValueError("snapshot compilation requires the original frozen input")
        source = await self._snapshots.get(snapshot_id)
        if source is None:
            raise KeyError(f"unknown snapshot id: {snapshot_id!r}")
        _, result = await self._recompiler.recompile(
            snapshot_id, frozen=frozen_input, domain=source.domain, policy=policy,
        )
        return result
