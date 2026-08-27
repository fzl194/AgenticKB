"""批次3-问题3：扫描件 PDF 守卫接线生产解析链。

file_inspector 的 has_text_layer 检测此前只在未挂线的路由模块里——
生产链由 registry 顺序直接构链，扫描件靠 FAIL 终态"巧合"兜住。
接线：DocumentParseService.execute 入口对 pdf 做文本层采样，无文本层
直接走 FAILED 终态并给明确"需 OCR"错误（完整留痕 attempt）。
"""
from __future__ import annotations

import hashlib

import pytest

pytestmark = pytest.mark.asyncio

from knowledge_mining.mining.contracts.parse_plan import AttemptBudget, ParsePlan
from knowledge_mining.mining.frozen_input.contracts import FrozenInput
from knowledge_mining.tests.parse_operator.test_document_parse_service import (
    Harness, _plan,
)


def _pdf(pages: list[str]) -> bytes:
    """最小 PDF 构造：每页一段文本内容（pages 元素为文本或空串=扫描页）。"""
    def stream(c: str) -> bytes:
        d = c.encode("latin-1")
        return (b"<< /Length %d >>\nstream\n" % len(d)) + d + b"\nendstream"

    n = len(pages)
    kids = " ".join(str(4 + 2 * i) + " 0 R" for i in range(n))
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        ("<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n)).encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, content in enumerate(pages):
        objs.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                     "/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>"
                     % (5 + 2 * i)).encode())
        objs.append(stream(content))
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for idx, b in enumerate(objs, 1):
        offsets.append(len(out))
        out += (b"%d 0 obj\n" % idx) + b + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offsets:
        out += b"%010d 00000 n \n" % o
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, x))
    return bytes(out)


TEXT_PAGE = "BT /F1 12 Tf 72 700 Td (real text page.) Tj ET\n"
SCANNED_PAGE = "1 0 0 RG 2 w\n100 100 m 500 100 l s\n"  # 只有矢量绘图，零字符


def _pdf_frozen(data: bytes) -> FrozenInput:
    sha = hashlib.sha256(data).hexdigest()
    return FrozenInput(
        document_id="doc-pdf",
        source_storage_object_id=f"so_{sha[:8]}",  # 按内容区分（守卫缓存键）
        source_raw_hash=sha,
        source_content_revision=1,
        mime="application/pdf",
        size=len(data),
        original_filename="scan.pdf",
        captured_at="2026-08-27T00:00:00+00:00",
        provider="fake",
        bucket="testop-source",
        object_key=f"v1/ab/{sha[:8]}",
        object_version_id=None,
    )


@pytest.fixture
def harness(tmp_path):  # noqa: ANN001
    from knowledge_mining.tests.parse_operator.test_document_parse_service import (
        StubParser,
    )
    h = Harness(tmp_path)
    h.register(StubParser("good", text="line one"))
    return h


@pytest.fixture(autouse=True)
def _clear_guard_cache():
    from knowledge_mining.mining.parse_operator.service import DocumentParseService
    DocumentParseService._pdf_bytes_cache.clear()
    yield
    DocumentParseService._pdf_bytes_cache.clear()


async def test_scanned_pdf_rejected_with_clear_ocr_message(harness) -> None:
    """无文本层 PDF：不进解析链，FAILED 终态 + 明确"需 OCR"信息 + attempt 留痕。"""
    data = _pdf([SCANNED_PAGE])
    frozen = _pdf_frozen(data)
    await harness.seed_source(frozen, data)
    service = harness.make_service()

    guard = await service._scanned_pdf_rejection(frozen)
    assert guard is not None and "OCR" in guard


async def test_text_pdf_passes_guard(harness):
    data = _pdf([TEXT_PAGE])
    frozen = _pdf_frozen(data)
    await harness.seed_source(frozen, data)
    service = harness.make_service()
    guard = await service._scanned_pdf_rejection(frozen)
    assert guard is None


async def test_non_pdf_skips_guard(harness):
    frozen = FrozenInput(
        document_id="d", source_storage_object_id="s", source_raw_hash="h",
        source_content_revision=1, mime="text/plain", size=1,
        original_filename="a.txt", captured_at="t", provider="fake",
        bucket="b", object_key="k", object_version_id=None,
    )
    service = harness.make_service()
    assert await service._scanned_pdf_rejection(frozen) is None


async def test_scanned_pdf_execute_fails_with_ocr_reason(harness) -> None:
    data = _pdf([SCANNED_PAGE])
    frozen = _pdf_frozen(data)
    await harness.seed_source(frozen, data)
    service = harness.make_service()
    from knowledge_mining.mining.parse_operator.service import (
        DocumentParseService as _S,
    )
    _S._pdf_bytes_cache.clear()
    run = await service.execute(
        frozen, _plan("good"), domain="default",
    )
    assert run.status == "FAILED"
    assert run.error_message and "OCR" in run.error_message
    events = await harness.attempts.list_by_run(run.id)
    assert len(events) == 1 and events[0].outcome == "FAILED"
