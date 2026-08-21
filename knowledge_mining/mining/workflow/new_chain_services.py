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
        storage_objects: Any | None = None,
        plan_factory: Callable[[Any, dict], ParsePlan] | None = None,
    ) -> None:
        self._operator = operator
        self._documents = documents
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
        descriptor = None
        for candidate in registry.all():
            if candidate.supports(mime) and candidate.license_status == "ok":
                descriptor = candidate
                break
        parser_id = descriptor.parser_id if descriptor else "legacy_markdown"
        # resolve_pipeline 保证 parser_id 可解析（描述符来自同一 registry）。
        resolve_pipeline(parser_id)
        budget = AttemptBudget(
            max_backend_attempts=int(params.get("maxBackendAttempts", 3)),
        )
        return ParsePlan(
            plan_id=f"workflow-{parser_id}",
            primary_parser_id=parser_id,
            budget=budget,
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
        from knowledge_mining.mining.frozen_input.contracts import FrozenInput
        from knowledge_mining.mining.infra.object_store.keys import (
            build_object_key,
        )

        current = await self._documents.get(document_id)
        if current is None or not current.storage_object_id:
            return None  # 旧链 storage_path 文档：v2 骨架下跳过（不混跑）
        if self._storage_objects is None:
            return None  # 无注册仓储无法定位对象（组合根必须注入）
        record = await self._storage_objects.get(current.storage_object_id)
        if record is None:
            return None  # 对象未注册（完整性缺口）：跳过并留 Run 无快照
        frozen = FrozenInput(
            document_id=document_id,
            source_storage_object_id=current.storage_object_id,
            source_raw_hash=current.source_raw_hash,
            source_content_revision=current.content_revision,
            mime=_as_str(
                getattr(raw_file, "mime", None), record.mime or "text/plain",
            ),
            size=record.size,
            original_filename=_as_str(
                getattr(raw_file, "document_key", None), document_id,
            ),
            captured_at="1970-01-01T00:00:00+00:00",
            provider=record.provider,
            bucket=record.bucket,
            object_key=record.object_key,
            object_version_id=record.object_version_id,
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
            max_tokens=int(params.get("maxTokens", 512)),
            min_tokens=int(params.get("minTokens", 64)),
            merge_adjacent_paragraphs=bool(
                params.get("mergeAdjacentParagraphs", True)
            ),
            inject_heading_context=bool(
                params.get("injectHeadingContext", True)
            ),
            table_view=_as_str(params.get("tableView"), "rows"),
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
) -> NewChainServices:
    """组合根：默认组装 memory 组件（测试/开发）；传入 PG/MinIO 即生产.

    生产接线（真实环境）：传 ``pool``（psycopg AsyncConnectionPool）与
    ``object_store``（MinioObjectStore），其余仓储自动取 PG 实现；
    未提供 pool 时使用 Fake/memory 组件——便于单测与本地链路验证。
    """
    if pool is not None:
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

        storage_objects = storage_objects or PgStorageObjectRepository(pool)
        documents = documents or PgDocumentCurrentContentRepository(pool)
        parse_runs = parse_runs or PgParseRunRepository(pool)
        attempts = attempts or PgParseAttemptRepository(pool)
        snapshots = snapshots or PgSnapshotRepository(pool)
        segment_store = segment_store or PgSegmentStore(pool)
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
        import tempfile

        from knowledge_mining.mining.infra.object_store.fake import (
            FakeObjectStore,
        )

        object_store = FakeObjectStore(tempfile.mkdtemp(prefix="m6-chain-"))

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

    async def _no_stale(frozen: Any) -> None:
        return None

    commit = SnapshotCommitService(
        snapshots=snapshots, stale_checker=_no_stale,
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
            storage_objects=storage_objects,
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
