"""M1 契约（批次8，24 号 §3.3/§5.2/§5.3）：版本化 MiningDocumentBundle.

- document_parse 直交 bundle（快照/IR 指针 + 文档生命周期事实），不再产
  legacy DocumentContext；
- segment_compile 消费 bundle → 仍产 bundle（compiled 计数 + document_facts），
  **兼容投影（to_raw_segment_data → ctx.segments）整体删除**；
- 序列化边界：bundle 不携带切片本体，切片落在 SegmentStore（按
  snapshot_ref 读），bundle 只带引用与计数；
- asset_persist 在 M5 前对 bundle 显式 clean-fail，不 AttributeError。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from knowledge_mining.mining.contracts.models import DocumentProfile
from knowledge_mining.mining.contracts.segment_compiler import (
    CompiledSegment,
    SegmentElementLink,
)
from knowledge_mining.mining.pipeline import DocumentContext


def _runtime(**services):
    return SimpleNamespace(
        services=SimpleNamespace(**services),
        manifest={"runId": "run-1"},
    )


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
    def parse_document(self, raw_file, *, params, domain, run_document_id):
        return SimpleNamespace(
            run_id="parse_1", snapshot_id="snap_1",
            parse_ir_storage_object_id="so_ir",
            parser_fingerprint="native_pdf@2.0.0",
        )


class _FakeCompileService:
    def __init__(self, segments) -> None:
        self._segments = segments

    def compile_for_snapshot(self, *, snapshot_id, parse_ir_storage_object_id, params):
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
            token_count=120,
        ),
        CompiledSegment(
            segment_index=1, block_type="table_row", raw_text="A-101\t风扇停转",
            heading_chain=((1, "章一"), (2, "告警表")),
            element_ids=("t0",),
            links=(SegmentElementLink(element_id="t0"),),
            metadata={"table_header": ["告警码", "原因"]},
            token_count=30,
        ),
    )


# ---------------------------------------------------------------------------
# bundle 契约
# ---------------------------------------------------------------------------


def test_bundle_is_versioned_frozen_and_ref_only() -> None:
    from knowledge_mining.mining.workflow.bundle import (
        BUNDLE_VERSION,
        MiningDocumentBundle,
    )

    bundle = MiningDocumentBundle(
        document_ref="doc.md",
        run_document_id="rd-1",
        snapshot_ref="snap_1",
        parse_ir_ref="so_ir",
        compiled_segment_count=2,
        document_facts={"segment_count": 2},
    )
    assert bundle.bundle_version == BUNDLE_VERSION == "1"
    with pytest.raises(Exception):
        bundle.snapshot_ref = "tampered"  # type: ignore[misc]

    # 序列化边界：bundle 永不携带切片/表示本体，只带引用与计数
    assert not hasattr(bundle, "segments")
    assert not hasattr(bundle, "representations")

    updated = bundle.with_updates(compiled_segment_count=3)
    assert updated.compiled_segment_count == 3
    assert bundle.compiled_segment_count == 2  # 不可变：原 bundle 不变


def test_compute_document_facts_from_compiled_segments() -> None:
    from knowledge_mining.mining.workflow.bundle import compute_document_facts

    facts = compute_document_facts(_segments())
    assert facts["segment_count"] == 2
    assert facts["token_total"] == 150
    assert facts["block_type_counts"] == {"paragraph": 1, "table_row": 1}
    assert facts["section_count"] == 2  # 章一 / 章一>告警表 两条不同路径
    assert facts["max_section_depth"] == 2
    assert facts["block_type_ratios"]["table_row"] == 0.5


def test_compute_document_facts_on_empty_is_zeroed() -> None:
    from knowledge_mining.mining.workflow.bundle import compute_document_facts

    facts = compute_document_facts(())
    assert facts == {
        "segment_count": 0,
        "token_total": 0,
        "section_count": 0,
        "max_section_depth": 0,
        "block_type_counts": {},
        "block_type_ratios": {},
    }


# ---------------------------------------------------------------------------
# document_parse → bundle
# ---------------------------------------------------------------------------


def test_document_parse_yields_versioned_bundle_with_lifecycle_facts() -> None:
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle
    from knowledge_mining.mining.workflow.handlers.document import (
        document_parse_handler,
    )

    raw = SimpleNamespace(file_type="markdown", document_key="doc.md")
    source = DocumentContext(
        raw_file=raw,
        profile=DocumentProfile(document_key="doc.md", title="source title"),
        action="UPDATE",
        existing_doc={"id": "document-existing"},
        document_id="document-existing",
        run_document_id="rd-1",
    )

    result = document_parse_handler(
        _state(source), {}, _runtime(document_parse_service=_FakeParseService()),
    )

    assert result.status.value == "success"
    assert "parsed_documents" in result.capabilities
    bundle = result.outputs.context
    assert isinstance(bundle, MiningDocumentBundle)
    assert bundle.snapshot_ref == "snap_1"
    assert bundle.parse_ir_ref == "so_ir"
    assert bundle.parser_fingerprint == "native_pdf@2.0.0"
    # 文档生命周期事实必须保留（asset_persist/M5 消费）
    assert bundle.raw_file is raw
    assert bundle.profile is source.profile
    assert bundle.action == "UPDATE"
    assert bundle.existing_doc == {"id": "document-existing"}
    assert bundle.document_id == "document-existing"


# ---------------------------------------------------------------------------
# segment_compile → bundle（删兼容投影）
# ---------------------------------------------------------------------------


def _parsed_bundle(raw):
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle
    from knowledge_mining.mining.workflow.handlers.document import (
        document_parse_handler,
    )

    source = DocumentContext(
        raw_file=raw,
        profile=DocumentProfile(document_key="doc.md"),
        run_document_id="rd-1",
    )
    result = document_parse_handler(
        _state(source), {}, _runtime(document_parse_service=_FakeParseService()),
    )
    return result.outputs.context


def test_segment_compile_returns_bundle_without_legacy_projection() -> None:
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle
    from knowledge_mining.mining.workflow.handlers.document import (
        segment_compile_handler,
    )

    raw = SimpleNamespace(file_type="markdown", document_key="doc.md")
    bundle = _parsed_bundle(raw)
    state = _state(bundle)

    result = segment_compile_handler(
        state, {"tableView": "rows"}, _runtime(
            segment_compile_service=_FakeCompileService(_segments()),
        ),
    )

    assert result.status.value == "success"
    assert "parsed_segments" in result.capabilities
    out = result.outputs.context
    assert isinstance(out, MiningDocumentBundle)
    assert out.compiled_segment_count == 2
    assert out.compiler_fingerprint == "segc-test"
    assert out.document_facts["segment_count"] == 2
    assert out.document_facts["block_type_counts"]["table_row"] == 1
    # 兼容投影已删除：bundle 上没有 legacy segments / RawSegmentData
    assert not hasattr(out, "segments")
    assert out.snapshot_ref == "snap_1"


def test_segment_compile_rejects_non_bundle_input() -> None:
    from knowledge_mining.mining.workflow.handlers.document import (
        segment_compile_handler,
    )

    legacy = DocumentContext(
        profile=DocumentProfile(document_key="doc.md"), run_document_id="rd-1",
    )
    result = segment_compile_handler(
        _state(legacy), {}, _runtime(
            segment_compile_service=_FakeCompileService(_segments()),
        ),
    )
    assert result.status.value == "failed"
    assert result.error_code == "segment_compile_bad_input"


def test_segment_compile_empty_segments_skips() -> None:
    from knowledge_mining.mining.workflow.handlers.document import (
        segment_compile_handler,
    )

    raw = SimpleNamespace(file_type="markdown", document_key="doc.md")
    result = segment_compile_handler(
        _state(_parsed_bundle(raw)), {}, _runtime(
            segment_compile_service=_FakeCompileService(()),
        ),
    )
    assert result.status.value == "skipped"


# ---------------------------------------------------------------------------
# asset_persist 对 bundle 的过渡期 clean-fail
# ---------------------------------------------------------------------------


def test_asset_persist_without_service_fails_explicitly() -> None:
    """M5：bundle 到达而持久化服务未接线 → asset_persist_unavailable。"""
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle
    from knowledge_mining.mining.workflow.handlers.persist import (
        asset_persist_handler,
    )

    repository = SimpleNamespace(document_persist_marker=lambda _rd: None)
    runtime = SimpleNamespace(
        services=SimpleNamespace(),
        runtime_repository=repository,
        manifest={"runId": "run-1"},
    )
    bundle = MiningDocumentBundle(document_ref="doc.md", run_document_id="rd-1")

    result = asset_persist_handler(_state(bundle), {}, runtime)

    assert result.status.value == "failed"
    assert result.error_code == "asset_persist_unavailable"
