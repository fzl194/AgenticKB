"""document_parse / segment_compile handler 接线（批次8 M1 更新版）.

- document_parse handler：经注入的服务组件执行新链解析（冻结输入→
  质量门控→快照转正），产出**版本化 MiningDocumentBundle**（快照/IR 指针
  + 文档生命周期事实）；
- segment_compile handler：从快照 IR 按参数档位编译切片，续写 bundle
  （compiled 计数 + document_facts）——legacy DocumentContext 兼容投影
  已在 M1 删除（24 号 §2.1/§5.3）；
- 组件未接线 → 显式 FAILED（不静默混跑旧解析，保证快照一致性）；
- 参数映射：SegmentCompileOptions → SegmentPolicy（面板档位生效）。
"""
from __future__ import annotations

from types import SimpleNamespace

from knowledge_mining.mining.contracts.models import DocumentProfile
from knowledge_mining.mining.contracts.segment_compiler import (
    CompiledSegment,
    SegmentElementLink,
)
from knowledge_mining.mining.pipeline import DocumentContext


def _runtime(**services):
    return SimpleNamespace(services=SimpleNamespace(**services))


def _state(context):
    return SimpleNamespace(
        run_document_id="rd-1", doc_key="doc.md", context=context,
        capabilities=frozenset(), tags=(),
        with_context=lambda ctx, capabilities=frozenset(): SimpleNamespace(
            run_document_id="rd-1", doc_key="doc.md", context=ctx,
            capabilities=capabilities, tags=(),
        ),
    )


class _FakeParseService:
    """document_parse 服务组件同步门面的测试替身."""

    def __init__(self, *, snapshot_id="snap_1", ir_id="so_ir",
                 run_id="parse_1") -> None:
        self.snapshot_id = snapshot_id
        self.ir_id = ir_id
        self.run_id = run_id
        self.calls: list[dict] = []

    def parse_document(self, raw_file, *, params, domain, run_document_id):
        self.calls.append({
            "raw_file": raw_file, "params": dict(params),
            "domain": domain, "run_document_id": run_document_id,
        })
        return SimpleNamespace(
            run_id=self.run_id, snapshot_id=self.snapshot_id,
            parse_ir_storage_object_id=self.ir_id,
            parser_fingerprint="native_pdf@2.0.0",
        )


class _FakeCompileService:
    """segment_compile 服务组件同步门面的测试替身."""

    def __init__(self, segments) -> None:
        self._segments = segments
        self.calls: list[dict] = []

    def compile_for_snapshot(
        self, *, snapshot_id, parse_ir_storage_object_id, params,
    ):
        self.calls.append({
            "snapshot_id": snapshot_id, "ir": parse_ir_storage_object_id,
            "params": dict(params),
        })
        return SimpleNamespace(
            snapshot_id=snapshot_id, segment_count=len(self._segments),
            compiler_fingerprint="segc-test", segments=self._segments,
        )


def _segments() -> tuple[CompiledSegment, ...]:
    return (
        CompiledSegment(
            segment_index=0, block_type="paragraph", raw_text="章一\n正文甲。",
            heading_chain=((1, "章一"),),
            element_ids=("e0",),
            links=(SegmentElementLink(element_id="e0"),),
            token_count=100,
        ),
        CompiledSegment(
            segment_index=1, block_type="table_row", raw_text="A-101\t风扇停转",
            heading_chain=((1, "章一"),),
            element_ids=("t0",),
            links=(SegmentElementLink(element_id="t0"),),
            metadata={"table_header": ["告警码", "原因"]},
            token_count=20,
        ),
    )


def _parsed_state(**context_extra):
    from knowledge_mining.mining.workflow.handlers.document import (
        document_parse_handler,
    )

    raw = SimpleNamespace(file_type="markdown", document_key="doc.md")
    source = DocumentContext(
        raw_file=raw,
        profile=DocumentProfile(document_key="doc.md"),
        run_document_id="rd-1",
        **context_extra,
    )
    result = document_parse_handler(
        _state(source), {}, _runtime(document_parse_service=_FakeParseService()),
    )
    assert result.status.value == "success"
    return _state(result.outputs.context), result


# ---------------------------------------------------------------------------
# document_parse handler
# ---------------------------------------------------------------------------


def test_document_parse_invokes_service_and_yields_bundle():
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle
    from knowledge_mining.mining.workflow.handlers.document import (
        document_parse_handler,
    )

    svc = _FakeParseService()
    runtime = _runtime(document_parse_service=svc)
    raw = SimpleNamespace(file_type="markdown", document_key="doc.md")
    state = _state(SimpleNamespace(raw_file=raw))

    result = document_parse_handler(state, {"qualityProfile": "strict"}, runtime)

    assert result.status.value == "success"
    assert "parsed_documents" in result.capabilities
    assert svc.calls and svc.calls[0]["params"]["qualityProfile"] == "strict"
    bundle = result.outputs.context
    assert isinstance(bundle, MiningDocumentBundle)
    assert bundle.snapshot_ref == "snap_1"
    assert bundle.parse_ir_ref == "so_ir"
    assert bundle.parser_fingerprint == "native_pdf@2.0.0"


def test_document_parse_without_service_fails_explicitly():
    from knowledge_mining.mining.workflow.handlers.document import (
        document_parse_handler,
    )

    runtime = _runtime()  # 组件未接线
    state = _state(SimpleNamespace(raw_file=SimpleNamespace()))

    result = document_parse_handler(state, {}, runtime)
    assert result.status.value == "failed"
    assert result.error_code == "document_parse_unavailable"


def test_document_parse_missing_raw_file_skips():
    from knowledge_mining.mining.workflow.handlers.document import (
        document_parse_handler,
    )

    runtime = _runtime(document_parse_service=_FakeParseService())
    state = _state(SimpleNamespace(raw_file=None))
    result = document_parse_handler(state, {}, runtime)
    assert result.status.value == "skipped"


# ---------------------------------------------------------------------------
# segment_compile handler
# ---------------------------------------------------------------------------


def test_segment_compile_writes_counts_and_facts_into_bundle():
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle
    from knowledge_mining.mining.workflow.handlers.document import (
        segment_compile_handler,
    )

    compile_svc = _FakeCompileService(_segments())
    state, _ = _parsed_state()

    result = segment_compile_handler(
        state, {"tableView": "rows", "maxTokens": 256},
        _runtime(segment_compile_service=compile_svc),
    )

    assert result.status.value == "success"
    assert "parsed_segments" in result.capabilities
    # 参数档位透传给服务（面板 → SegmentPolicy 映射在服务侧）
    assert compile_svc.calls[0]["params"]["tableView"] == "rows"
    bundle = result.outputs.context
    assert isinstance(bundle, MiningDocumentBundle)
    assert bundle.compiled_segment_count == 2
    assert bundle.compiler_fingerprint == "segc-test"
    assert bundle.document_facts["segment_count"] == 2
    assert bundle.document_facts["token_total"] == 120
    assert bundle.document_facts["block_type_counts"]["table_row"] == 1
    # 兼容投影已删：bundle 上没有 legacy segments
    assert not hasattr(bundle, "segments")


def test_parse_compile_preserves_document_lifecycle_in_bundle():
    """parse→compile 必须在 bundle 中保留 asset_persist 需要的生命周期事实."""
    from knowledge_mining.mining.workflow.handlers.document import (
        segment_compile_handler,
    )

    state, _ = _parsed_state(
        action="UPDATE",
        existing_doc={"id": "document-existing"},
        document_id="document-existing",
    )

    result = segment_compile_handler(
        state, {}, _runtime(segment_compile_service=_FakeCompileService(_segments())),
    )

    assert result.status.value == "success"
    bundle = result.outputs.context
    assert bundle.action == "UPDATE"
    assert bundle.existing_doc == {"id": "document-existing"}
    assert bundle.document_id == "document-existing"
    assert bundle.snapshot_ref == "snap_1"


def test_segment_compile_without_service_fails_explicitly():
    from knowledge_mining.mining.workflow.handlers.document import (
        segment_compile_handler,
    )

    runtime = _runtime()
    state, _ = _parsed_state()
    result = segment_compile_handler(state, {}, runtime)
    assert result.status.value == "failed"
    assert result.error_code == "segment_compile_unavailable"


def test_segment_compile_empty_segments_skips():
    from knowledge_mining.mining.workflow.handlers.document import (
        segment_compile_handler,
    )

    compile_svc = _FakeCompileService(())
    runtime = _runtime(segment_compile_service=compile_svc)
    state, _ = _parsed_state()
    result = segment_compile_handler(state, {}, runtime)
    assert result.status.value == "skipped"


def test_handlers_registered_in_builtin_registry():
    from knowledge_mining.mining.workflow.handler_registry import (
        builtin_handler_registry,
    )

    registry = builtin_handler_registry()
    assert registry.resolve("document_parse", "1") is not None
    assert registry.resolve("segment_compile", "1") is not None
