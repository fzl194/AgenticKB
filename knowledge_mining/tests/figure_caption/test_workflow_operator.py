"""figure_caption_generate 算子（handler + 工作流门面）单元测试。"""
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

from knowledge_mining.mining.contracts.parse_ir.types import (
    Diagnostics,
    Element,
    FigureAsset,
    ParseIdentity,
    ParsedDocument,
)
from knowledge_mining.mining.figure_caption.keys import caption_cache_key
from knowledge_mining.mining.figure_caption.prompts import FIGCAP_PROMPT_VERSION
from knowledge_mining.mining.figure_caption.workflow_facade import (
    FigureCaptionWorkflowFacade,
)
from knowledge_mining.mining.workflow.core import OperatorStatus
from knowledge_mining.mining.workflow.handlers.document import (
    figure_caption_generate_handler,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 测试工厂
# ---------------------------------------------------------------------------


def _ir_doc(sha: str) -> ParsedDocument:
    return ParsedDocument(
        schema_version="1.0",
        source_identity=ParseIdentity(
            source_raw_hash="rawhash-1", parser_fingerprint="pptx-fp-1",
        ),
        containers=(),
        elements=(
            Element(
                element_id="h1", element_type="heading", order_index=0,
                text="架构", style={"level": 1},
            ),
            Element(
                element_id="fig1", element_type="figure", order_index=1,
            ),
        ),
        diagnostics=Diagnostics(),
        structured_assets={
            "fig1-figure": FigureAsset(
                figure_id="fig1-figure",
                original_image_ref=f"bin-{sha[:16]}",
                image_hash=sha,
            ),
        },
        binary_assets={
            f"bin-{sha[:16]}": {"sha256": sha, "mime": "image/png"},
        },
    )


class _Snapshots:
    async def get(self, snapshot_id):
        return SimpleNamespace(parse_ir_storage_object_id="ir-obj-1")


class _StorageObjects:
    async def get(self, object_id):
        return SimpleNamespace(
            bucket="parse", object_key=f"{object_id}.json",
            object_version_id=None, sha256=_sha(_IR_PAYLOAD),
        )


_IR_PAYLOAD = b"placeholder"  # 由 fixture 覆盖


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Store:
    """object_store：IR 对象 + 源 pptx 对象。"""

    def __init__(self, ir_payload: bytes, source_bytes: bytes) -> None:
        self.objects = {
            "parse/ir-obj-1.json": ir_payload,
            "src/bucket/source.pptx": source_bytes,
        }

    async def get_stream(self, location):
        data = self.objects[f"{location.bucket}/{location.object_key}"]
        yield data


class _Cache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def put(self, key, caption, **_):
        self.store[key] = caption


class _Appender:
    def __init__(self) -> None:
        self.batches = []

    async def append_figures_for_snapshot(self, snapshot_id, document_key, segments):
        self.batches.append((snapshot_id, document_key, segments))
        return len(segments)


class _Captioner:
    # 真实 VlmFigureCaptioner 有 default_model（llm_service vision-default 解析）；
    # 门面 params.model 留空时 service 用它解析 effective_model。
    default_model = "fake-vision-model"

    def __init__(self) -> None:
        self.calls = []

    def caption(self, *, image_bytes, mime, native_caption, model):
        self.calls.append({"model": model, "size": len(image_bytes)})
        return "图注：系统架构图"


def _build_facade(ir_doc: ParsedDocument, source_bytes: bytes):
    ir_payload = json.dumps(ir_doc.to_dict()).encode("utf-8")

    class _StorageObjectsWithHash(_StorageObjects):
        async def get(self, object_id):
            return SimpleNamespace(
                bucket="parse", object_key=f"{object_id}.json",
                object_version_id=None, sha256=_sha(ir_payload),
            )

    return FigureCaptionWorkflowFacade(
        snapshots=_Snapshots(),
        storage_objects=_StorageObjectsWithHash(),
        object_store=_Store(ir_payload, source_bytes),
        cache=_Cache(),
        segment_store=_Appender(),
        captioner=_Captioner(),
    )


def _frozen(mime: str = "application/vnd.openxmlformats-officedocument"
                          ".presentationml.presentation"):
    return SimpleNamespace(
        mime=mime, bucket="src", object_key="bucket/source.pptx",
        object_version_id=None,
    )


# ---------------------------------------------------------------------------
# 门面（sync，含 IR 加载与源字节回读）
# ---------------------------------------------------------------------------


def _pptx_bytes_with_image(blob: bytes) -> bytes:
    """真实 PPTX（含一张 PNG）——PptxImageByteSource 需要能重开."""
    import io

    from pptx import Presentation

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_picture(io.BytesIO(blob), 0, 0, width=100, height=100)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def test_workflow_facade_pptx_end_to_end():
    blob = _png_bytes()
    sha = _sha(blob)
    facade = _build_facade(_ir_doc(sha), _pptx_bytes_with_image(blob))
    outcome = facade.generate_for_snapshot(
        snapshot_id="snap-1",
        document_key="doc-1",
        domain="default",
        params={"model": "glm-4.5v", "maxFigures": 5, "minImageBytes": 0},
        frozen_input=_frozen(),
    )
    assert outcome.figures_found == 1
    assert outcome.captioned == 1
    assert outcome.appended == 1
    assert not outcome.degraded
    (snap, key, segments), = facade._segment_store.batches  # noqa: SLF001
    assert (snap, key) == ("snap-1", "doc-1")
    assert segments[0].metadata["vlm_model"] == "glm-4.5v"
    # 模型逐请求传入
    assert facade._captioner.calls[0]["model"] == "glm-4.5v"  # noqa: SLF001
    # 缓存键成分齐全（audit 列）
    cached_key = next(iter(facade._cache.store))  # noqa: SLF001
    assert cached_key == caption_cache_key(
        domain="default", source_raw_hash="rawhash-1",
        parser_fingerprint="pptx-fp-1", figure_element_id="fig1",
        model="glm-4.5v", prompt_version=FIGCAP_PROMPT_VERSION,
    )


def _png_bytes() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_workflow_facade_non_pptx_degrades_to_missing_bytes():
    blob = _png_bytes()
    sha = _sha(blob)
    facade = _build_facade(_ir_doc(sha), blob)
    outcome = facade.generate_for_snapshot(
        snapshot_id="snap-1",
        document_key="doc-1",
        domain="default",
        params={},
        frozen_input=_frozen(mime="application/pdf"),
    )
    assert outcome.figures_found == 1
    assert outcome.skipped_missing_bytes == 1
    assert outcome.degraded


def test_workflow_facade_empty_model_falls_back_to_captioner_default():
    """回归（401 事故）：params.model 留空/空串时不得回落硬编码模型名。"""
    blob = _png_bytes()
    sha = _sha(blob)
    facade = _build_facade(_ir_doc(sha), _pptx_bytes_with_image(blob))
    # minImageBytes=0：测试 PNG 只有 ~184B，否则被小图过滤
    for model_value in (None, "", "explicit-model"):
        facade._captioner.calls.clear()  # noqa: SLF001
        facade._cache.store.clear()  # noqa: SLF001 —— 前一轮的缓存命中会绕过 captioner
        params = {"maxFigures": 5, "minImageBytes": 0}
        if model_value is not None:
            params["model"] = model_value
        outcome = facade.generate_for_snapshot(
            snapshot_id="snap-1",
            document_key="doc-1",
            domain="default",
            params=params,
            frozen_input=_frozen(mime="application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        )
        assert outcome.captioned == 1, params
        expected = "explicit-model" if model_value == "explicit-model" else "fake-vision-model"
        # 门面不得引入自己的模型名；解析交给 service（captioner.default_model）
        assert facade._captioner.calls[0]["model"] == expected, params  # noqa: SLF001


def test_workflow_facade_no_snapshot_is_noop():
    facade = _build_facade(_ir_doc("a" * 64), b"")
    outcome = facade.generate_for_snapshot(
        snapshot_id=None, document_key="doc-1", domain="default", params={},
    )
    assert outcome.figures_found == 0


# ---------------------------------------------------------------------------
# handler
# ---------------------------------------------------------------------------


class _State:
    def __init__(self, context):
        self.context = context

    def with_context(self, context, capabilities=None):
        return self


def _bundle() -> Any:
    from knowledge_mining.mining.workflow.bundle import MiningDocumentBundle

    return MiningDocumentBundle(
        document_ref="doc-1",
        run_document_id="rd-1",
        snapshot_ref="snap-1",
        parse_ir_ref="ir-1",
        frozen_input=None,
        parser_fingerprint="fp-1",
        quality_status="PASS",
        raw_file=None,
        profile=None,
        action=None,
        existing_doc=None,
        document_id="doc-1",
    )


class _Runtime:
    def __init__(self, services):
        self.services = services


class _Outcome:
    figures_found = 1
    captioned = 1
    cache_hits = 0
    failed = 0
    skipped_small = 0
    skipped_missing_bytes = 0
    appended = 1
    degraded = False

    def as_metrics(self):
        return {
            "figures_found": 1, "captioned": 1, "cache_hits": 0, "failed": 0,
            "skipped_small": 0, "skipped_missing_bytes": 0, "appended": 1,
            "degraded": False,
        }


def test_handler_success():
    class _Service:
        def generate_for_snapshot(self, **kwargs):
            assert kwargs["snapshot_id"] == "snap-1"
            return _Outcome()

    result = figure_caption_generate_handler(
        _State(_bundle()), {}, _Runtime(SimpleNamespace(
            figure_caption_service=_Service(), domain="default",
        ))
    )
    assert result.status is OperatorStatus.SUCCESS


def test_handler_service_missing_falls_back():
    result = figure_caption_generate_handler(
        _State(_bundle()), {}, _Runtime(SimpleNamespace(
            figure_caption_service=None, domain="default",
        ))
    )
    assert result.status is OperatorStatus.FALLBACK
    # FALLBACK 语义：降级记录为 warning（能力事实照记），不是 error
    assert "figure_captions" in result.capabilities
    assert result.warnings and result.warnings[0].code == "figure_caption_unavailable"


def test_handler_degraded_outcome_falls_back():
    class _DegradedOutcome(_Outcome):
        captioned = 0
        appended = 0
        degraded = True

        def as_metrics(self):
            return {
                "figures_found": 1, "captioned": 0, "cache_hits": 0,
                "failed": 1, "skipped_small": 0, "skipped_missing_bytes": 0,
                "appended": 0, "degraded": True,
            }

    class _Service:
        def generate_for_snapshot(self, **kwargs):
            return _DegradedOutcome()

    result = figure_caption_generate_handler(
        _State(_bundle()), {}, _Runtime(SimpleNamespace(
            figure_caption_service=_Service(), domain="default",
        ))
    )
    assert result.status is OperatorStatus.FALLBACK
