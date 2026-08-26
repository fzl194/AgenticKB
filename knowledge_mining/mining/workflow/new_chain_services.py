"""M6.3 新链服务门面 + 组合根（SRS §10.2/§4.6/§4.12）.

工作流 handler 是同步调用（executor 逐文档推进），而 M4/M5 的解析与
切片服务是 async（仓储/对象存储契约）。本模块提供：

- :class:`DocumentParseFacade`：raw_file（工作流上下文）→ 查文档当前
  内容对象 → 冻结输入 → :class:`DocumentParseService`（质量门控 + 快照
  转正）→ 解析指针。文档无新链对象（旧 storage_path 形态）→ None
  （handler SKIP——v2 骨架只对新链文档产知识，不静默回落旧解析）。
  Run 终态非 SUCCEEDED → 抛错（handler 归一为算子失败，原因可见）。
- :class:`SegmentCompileFacade`：参数档位 → :class:`SegmentPolicy` →
  编译服务 → 切片列表（handler 兼容投影消费）。
- :func:`build_new_chain_services`：组合根——真组件组装为可注入
  ``runtime.services`` 的同步门面包。

async 桥接：executor 在工作线程执行 handler（无运行中事件循环）时直接
``asyncio.run``；若被 event loop 线程直接调用（不应发生），退到一次性
线程执行，保证两种环境下都可用。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass
from typing import Any, Callable

from knowledge_mining.mining.contracts.parse_plan import (
    AttemptBudget,
    ParsePlan,
)
from knowledge_mining.mining.contracts.segment_compiler import SegmentPolicy


def _run_sync(coro) -> Any:  # noqa: ANN001
    """同步上下文执行 async 服务调用（线程安全兜底两种运行环境）."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _as_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) and value else default


class _SyncPoolAsyncAdapter:
    """Expose a psycopg synchronous pool through the async repository shape.

    Workflow document handlers execute in worker threads and drive their async
    services with :func:`asyncio.run`.  The older mining runtime owns a
    synchronous ``psycopg_pool.ConnectionPool``; creating an unrelated async
    pool for every handler would lose its lifecycle and connection limits.

    This adapter intentionally performs the database calls synchronously on
    that worker thread.  It is therefore *not* a general async-pool adapter,
    but keeps each repository call and repository-local transaction on the
    same checked-out connection.  Cross-service parse/segment atomicity is
    still not provided here; that requires the planned single snapshot commit
    boundary rather than a pool adapter.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def connection(self) -> "_SyncConnectionContext":
        return _SyncConnectionContext(self._pool.connection())


class _SyncConnectionContext:
    def __init__(self, context: Any) -> None:
        self._context = context
        self._connection: Any | None = None

    async def __aenter__(self) -> "_SyncConnectionAdapter":
        self._connection = self._context.__enter__()
        return _SyncConnectionAdapter(self._connection)

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._context.__exit__(exc_type, exc, traceback)


class _SyncConnectionAdapter:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def execute(self, query: str, params: Any = None) -> "_SyncCursorAdapter":
        if params is None:
            cursor = self._connection.execute(query)
        else:
            cursor = self._connection.execute(query, params)
        return _SyncCursorAdapter(cursor)

    def transaction(self) -> "_SyncTransactionContext":
        return _SyncTransactionContext(self._connection.transaction())


class _SyncCursorAdapter:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    async def fetchone(self) -> Any:
        return self._cursor.fetchone()

    async def fetchall(self) -> Any:
        return self._cursor.fetchall()


class _SyncTransactionContext:
    def __init__(self, context: Any) -> None:
        self._context = context

    async def __aenter__(self) -> None:
        self._context.__enter__()

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._context.__exit__(exc_type, exc, traceback)


_MEMORY_OBJECT_ROOT: str | None = None


def _memory_object_root() -> str:
    """Single process-wide scratch root for the default Fake object store.

    Every ``build_new_chain_services`` call without an object store used to
    leak its own ``mkdtemp`` directory; tests and dev loops now share one
    root, removed when the process exits.
    """
    global _MEMORY_OBJECT_ROOT
    if _MEMORY_OBJECT_ROOT is None:
        import atexit
        import shutil
        import tempfile

        root = tempfile.mkdtemp(prefix="m6-chain-")
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        _MEMORY_OBJECT_ROOT = root
    return _MEMORY_OBJECT_ROOT


@dataclass(frozen=True)
class _NewChainSettings:
    bucket_prefix: str = "agentickb-"
    domain: str = "default"


class DocumentParseFacade:
    """工作流 → 新链解析的同步门面（§4.6：冻结输入 + ParsePlan）."""

    def __init__(
        self,
        *,
        operator: Any,
        documents: Any,
        frozen_inputs: Any | None = None,
        storage_objects: Any | None = None,
        plan_factory: Callable[[Any, dict], ParsePlan] | None = None,
    ) -> None:
        self._operator = operator
        self._documents = documents
        self._frozen_inputs = frozen_inputs
        self._storage_objects = storage_objects
        self._plan_factory = plan_factory or self.default_plan_factory

    @staticmethod
    def default_plan_factory(raw_file: Any, params: dict) -> ParsePlan:
        """按 MIME 选主解析器；后端预算取参数档位（§4.9）."""
        from knowledge_mining.mining.parse_adapters.factory import (
            resolve_pipeline,
        )
        from knowledge_mining.mining.parse_adapters.registry import (
            build_default_registry,
        )

        registry = build_default_registry()
        mime = _as_str(
            getattr(raw_file, "mime", None), "text/plain",
        )
        parser_ids = tuple(
            candidate.parser_id for candidate in registry.all()
            if candidate.supports(mime)
            and candidate.license_status == "ok"
            and resolve_pipeline(candidate.parser_id) is not None
        )
        parser_id = parser_ids[0] if parser_ids else "legacy_markdown"
        budget = AttemptBudget(
            max_backend_attempts=int(params.get("maxBackendAttempts", 3)),
        )
        return ParsePlan(
            plan_id=f"workflow-{parser_id}",
            primary_parser_id=parser_id,
            fallback_parser_ids=parser_ids[1:budget.max_backend_attempts],
            budget=budget,
            quality_profile=params.get("qualityProfile", "default"),
        )

    def parse_document(
        self,
        raw_file: Any,
        *,
        params: dict,
        domain: str,
        run_document_id: str,
    ) -> Any | None:
        """解析一份文档（同步门面）；无新链对象 → None（SKIP）."""
        from knowledge_mining.mining.frozen_input.contracts import FrozenInput

        document_id = _as_str(getattr(raw_file, "document_id", None))
        if not document_id:
            return None
        plan = self._plan_factory(raw_file, params)
        outcome = _run_sync(
            self._parse(document_id, raw_file, plan, domain)
        )
        return outcome

    async def _parse(
        self, document_id: str, raw_file: Any, plan: ParsePlan, domain: str
    ) -> Any | None:
        from dataclasses import replace
        from knowledge_mining.mining.contracts.storage.errors import StorageObjectMissing

        if self._frozen_inputs is None:
            return None
        try:
            frozen = await self._frozen_inputs.freeze(document_id)
        except StorageObjectMissing:
            return None  # Preserve legacy/no-object SKIP semantics.
        frozen = replace(
            frozen,
            original_filename=_as_str(getattr(raw_file, "document_key", None), document_id),
        )
        run = await self._operator.execute(frozen, plan, domain=domain)
        if run.status != "SUCCEEDED":
            raise RuntimeError(
                f"parse run {run.id} ended {run.status}: "
                f"{run.error_message or 'no snapshot produced'}"
            )
        return run


class SegmentCompileFacade:
    """工作流 → 新链切片编译的同步门面（§4.12：策略档位生效）."""

    def __init__(self, *, compiler: Any, segment_store: Any) -> None:
        self._compiler = compiler
        self._store = segment_store

    def compile_for_snapshot(
        self,
        *,
        snapshot_id: str | None,
        parse_ir_storage_object_id: str | None,
        params: dict,
    ) -> Any:
        """编译并返回切片列表（CompiledSegment 元组）."""
        if not snapshot_id or not parse_ir_storage_object_id:
            from types import SimpleNamespace

            return SimpleNamespace(segments=(), segment_count=0)
        policy = SegmentPolicy(
            max_tokens=int(params.get("maxTokens", 2048)),
            min_tokens=int(params.get("minTokens", 512)),
            merge_adjacent_paragraphs=bool(
                params.get("mergeAdjacentParagraphs", True)
            ),
            inject_heading_context=bool(
                params.get("injectHeadingContext", True)
            ),
            table_view=_as_str(params.get("tableView"), "whole"),
            include_figure_captions=bool(
                params.get("includeFigureCaptions", True)
            ),
        )
        result = _run_sync(
            self._compiler.compile(
                snapshot_id,
                parse_ir_storage_object_id=parse_ir_storage_object_id,
                document_key=snapshot_id,
                policy=policy,
            )
        )
        segments = _run_sync(self._store.list_for_snapshot(snapshot_id))
        from types import SimpleNamespace

        return SimpleNamespace(
            snapshot_id=snapshot_id,
            segment_count=getattr(result, "segment_count", len(segments)),
            compiler_fingerprint=getattr(result, "compiler_fingerprint", ""),
            segments=segments,
        )


@dataclass(frozen=True)
class NewChainServices:
    """注入 ``runtime.services`` 的同步门面包（handler 只认这两个属性）."""

    document_parse_service: DocumentParseFacade
    segment_compile_service: SegmentCompileFacade


def build_new_chain_services(
    *,
    bucket_prefix: str,
    domain: str = "default",
    object_store: Any | None = None,
    storage_objects: Any | None = None,
    documents: Any | None = None,
    parse_runs: Any | None = None,
    attempts: Any | None = None,
    snapshots: Any | None = None,
    segment_store: Any | None = None,
    pool: Any | None = None,
    sync_pool: Any | None = None,
) -> NewChainServices:
    """组合根：默认组装 memory 组件（测试/开发）；传入 PG/MinIO 即生产.

    生产接线（真实环境）：传 ``pool``（psycopg AsyncConnectionPool）或
    ``sync_pool``（既有 worker 的 psycopg ConnectionPool）以及
    ``object_store``（MinioObjectStore），其余仓储自动取 PG 实现；
    未提供 pool 时使用 Fake/memory 组件——便于单测与本地链路验证。
    """
    if pool is not None and sync_pool is not None:
        raise ValueError("provide either pool or sync_pool, not both")
    repository_pool = pool or (
        _SyncPoolAsyncAdapter(sync_pool) if sync_pool is not None else None
    )
    if repository_pool is not None:
        from knowledge_mining.mining.file_management.repositories_pg import (
            PgDocumentCurrentContentRepository,
            PgStorageObjectRepository,
        )
        from knowledge_mining.mining.segment_compiler.repositories_pg import (
            PgSegmentStore,
        )
        from knowledge_mining.mining.shadow_parse.repositories_pg import (
            PgParseAttemptRepository,
            PgParseRunRepository,
        )
        from knowledge_mining.mining.snapshot_store.repositories_pg import (
            PgSnapshotRepository,
        )

        storage_objects = storage_objects or PgStorageObjectRepository(repository_pool)
        documents = documents or PgDocumentCurrentContentRepository(repository_pool)
        parse_runs = parse_runs or PgParseRunRepository(repository_pool)
        attempts = attempts or PgParseAttemptRepository(repository_pool)
        snapshots = snapshots or PgSnapshotRepository(repository_pool)
        segment_store = segment_store or PgSegmentStore(repository_pool)
    else:
        from knowledge_mining.mining.file_management.repositories_memory import (
            MemoryDocumentCurrentContentRepository,
            MemoryStorageObjectRepository,
        )
        from knowledge_mining.mining.segment_compiler.repositories_memory import (
            MemorySegmentStore,
        )
        from knowledge_mining.mining.shadow_parse.repositories_memory import (
            MemoryParseAttemptRepository,
            MemoryParseRunRepository,
        )
        from knowledge_mining.mining.snapshot_store.repositories_memory import (
            MemorySnapshotRepository,
        )

        storage_objects = storage_objects or MemoryStorageObjectRepository()
        documents = documents or MemoryDocumentCurrentContentRepository()
        parse_runs = parse_runs or MemoryParseRunRepository()
        attempts = attempts or MemoryParseAttemptRepository()
        snapshots = snapshots or MemorySnapshotRepository()
        segment_store = segment_store or MemorySegmentStore()

    if object_store is None:
        from knowledge_mining.mining.infra.object_store.fake import (
            FakeObjectStore,
        )

        object_store = FakeObjectStore(_memory_object_root())

    from knowledge_mining.mining.parse_adapters.factory import resolve_pipeline
    from knowledge_mining.mining.parse_operator.service import (
        DocumentParseService,
    )
    from knowledge_mining.mining.parse_quality.gate import QualityGate
    from knowledge_mining.mining.parse_reconciler import StructuralReconciler
    from knowledge_mining.mining.segment_compiler.service import (
        SegmentCompileService,
    )
    from knowledge_mining.mining.snapshot_store.service import (
        SnapshotCommitService,
    )

    from knowledge_mining.mining.frozen_input.service import FrozenInputService

    frozen_inputs = FrozenInputService(
        documents=documents,
        storage_objects=storage_objects,
        object_store=object_store,
    )

    commit = SnapshotCommitService(
        snapshots=snapshots, stale_checker=frozen_inputs.check_stale,
        storage_objects=storage_objects, object_store=object_store,
    )
    operator = DocumentParseService(
        object_store=object_store, parse_runs=parse_runs, attempts=attempts,
        storage_objects=storage_objects, parser_resolver=resolve_pipeline,
        commit_service=commit, quality_gate=QualityGate(),
        reconciler=StructuralReconciler(), snapshots=snapshots,
        bucket_prefix=bucket_prefix,
    )
    compiler = SegmentCompileService(
        object_store=object_store, storage_objects=storage_objects,
        segment_store=segment_store,
    )
    return NewChainServices(
        document_parse_service=DocumentParseFacade(
            operator=operator, documents=documents,
            storage_objects=storage_objects, frozen_inputs=frozen_inputs,
        ),
        segment_compile_service=SegmentCompileFacade(
            compiler=compiler, segment_store=segment_store,
        ),
    )


__all__ = [
    "DocumentParseFacade",
    "NewChainServices",
    "SegmentCompileFacade",
    "build_new_chain_services",
]
