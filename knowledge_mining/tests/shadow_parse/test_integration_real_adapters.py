"""M2 端到端集成：真实 legacy 适配器 × ``ShadowParseService``（in-memory 全链路）.

与 ``test_service.py``（stub parser/normalizer 验服务编排）互补，本文件把
**真实交付物**串成一条链（SRS §14 M2 退出条件的内存版验收）：

```text
FakeObjectStore(source bucket) -> FrozenInput -> ShadowParseService
  -> LegacyMarkdownParser / LegacyPlainTextParser（§C06）
  -> LegacyLineNormalizer（§C07，真实 stable id / 行级 EvidenceSpan）
  -> Parse IR JSON -> FakeObjectStore(parse bucket) -> 投影 SUCCEEDED
```

验收点（SRS §A01 line-addressable + M2 退出条件）：
- Markdown 产 heading 父链 + 阅读序 + 表格 TableAsset，IR 可从 parse bucket
  读回并 ``from_dict`` 重建、再 validate 通过（round-trip）。
- EvidenceSpan 行定位可回溯原文行文本。
- TXT 长段无 token 切分（一个长段 == 一个 element）。
- 幂等：同 frozen 二次 run → ``reused=True``。
- registry 按 MIME 路由到正确 parser。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from collections.abc import AsyncIterator

import pytest

# psycopg-async needs the SelectorEventLoop on Windows（与 frozen_input 测试一致）。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_mining.mining.contracts.parse_ir.schema import validate  # noqa: E402
from knowledge_mining.mining.contracts.parse_ir.types import ParsedDocument  # noqa: E402
from knowledge_mining.mining.contracts.storage.types import (  # noqa: E402
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.file_management.repositories_memory import (  # noqa: E402
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.frozen_input.contracts import FrozenInput  # noqa: E402
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402
from knowledge_mining.mining.parse_adapters import (  # noqa: E402
    LEGACY_MARKDOWN_PARSER_ID,
    LEGACY_TXT_PARSER_ID,
    LegacyLineNormalizer,
    LegacyMarkdownParser,
    LegacyPlainTextParser,
    build_default_registry,
)
from knowledge_mining.mining.shadow_parse.repositories_memory import (  # noqa: E402
    MemoryParseRunRepository,
)
from knowledge_mining.mining.shadow_parse.service import ShadowParseService  # noqa: E402

_BUCKET_PREFIX = "testm2int-"
SOURCE_BUCKET = f"{_BUCKET_PREFIX}source"
PARSE_BUCKET = f"{_BUCKET_PREFIX}parse"

_MD_SAMPLE = """# 设备告警处理手册

## 告警概述

设备告警分为两级。处理时先确认告警码。

## 告警对照表

| 告警码 | 原因 | 处理建议 |
| --- | --- | --- |
| 0x1001 | 风扇故障 | 更换风扇模块 |
| 0x1002 | 温度过高 | 检查机房空调 |

### 补充说明

超过 24 小时未处理的告警需要升级上报。
"""

_TXT_SAMPLE = (
    "第一段。\n\n"
    "第二段，包含较多的文字内容但没有任何空行分隔标记继续延续。\n\n"
    "第三段。"
)


async def _chunks(payload: bytes) -> AsyncIterator[bytes]:
    for i in range(0, len(payload), 4096):
        yield payload[i : i + 4096]


def _make_frozen(content: bytes, mime: str, object_key: str) -> FrozenInput:
    return FrozenInput(
        document_id="doc-m2-integration",
        source_storage_object_id="so_src_m2",
        source_raw_hash=hashlib.sha256(content).hexdigest(),
        source_content_revision=1,
        mime=mime,
        size=len(content),
        original_filename="sample",
        captured_at="2026-08-14T00:00:00+00:00",
        provider="fake",
        bucket=SOURCE_BUCKET,
        object_key=object_key,
    )


class _Harness:
    """一组集成测试共用的装配：store + 双仓储 + service 构造。"""

    def __init__(self, parser, tmp_path) -> None:
        self.store = FakeObjectStore(str(tmp_path / "store_root"))
        self.runs = MemoryParseRunRepository()
        self.storage_objects = MemoryStorageObjectRepository()
        self.service = ShadowParseService(
            object_store=self.store,
            parse_runs=self.runs,
            storage_objects=self.storage_objects,
            parser=parser,
            normalizer=LegacyLineNormalizer(),
            bucket_prefix=_BUCKET_PREFIX,
        )

    async def stage_and_run(self, content: bytes, mime: str, key: str):
        await self.store.put_stream(
            ObjectLocation(bucket=SOURCE_BUCKET, object_key=key),
            _chunks(content),
            PutOptions(
                artifact_class="temporary",
                expected_sha256=hashlib.sha256(content).hexdigest(),
            ),
        )
        return await self.service.run(_make_frozen(content, mime, key))

    async def read_ir(self, storage_object_id: str) -> ParsedDocument:
        """按注册行定位 parse bucket 对象并重建 ParsedDocument。"""
        record = await self.storage_objects.get(storage_object_id)
        assert record is not None, "IR storage object not registered"
        assert record.artifact_class == "parse_ir"
        buf = b""
        async for chunk in self.store.get_stream(
            ObjectLocation(bucket=record.bucket, object_key=record.object_key)
        ):
            buf += chunk
        return ParsedDocument.from_dict(json.loads(buf.decode("utf-8")))


# ---------------------------------------------------------------------------
# Markdown 全链路
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_markdown_end_to_end_roundtrip_and_projection(tmp_path) -> None:
    """MD → IR：投影计数正确、parse bucket 读回可重建并再次 validate 通过。"""
    harness = _Harness(LegacyMarkdownParser(), tmp_path)
    result = await harness.stage_and_run(
        _MD_SAMPLE.encode("utf-8"), "text/markdown", "src/md-a"
    )

    assert result.status == "SUCCEEDED"
    assert result.reused is False
    record = await harness.runs.get(result.parse_run_id)
    assert record is not None
    assert record.parser_id == LEGACY_MARKDOWN_PARSER_ID
    assert record.parse_ir_schema_version == "0.1.0"

    doc = await harness.read_ir(result.parse_ir_storage_object_id)
    assert record.element_count == len(doc.elements)
    assert record.container_count == len(doc.containers)
    assert record.relation_count == len(doc.relations)

    # round-trip 后仍通过 IR 校验（SRS §4.7 schema validation）
    verdict = validate(doc)
    assert verdict.valid, [f"{i.code}: {i.message}" for i in verdict.issues]


@pytest.mark.asyncio
async def test_markdown_structure_and_line_evidence(tmp_path) -> None:
    """父链（h1→h2→h3）、表格 TableAsset、行级 EvidenceSpan 回溯原文。"""
    harness = _Harness(LegacyMarkdownParser(), tmp_path)
    result = await harness.stage_and_run(
        _MD_SAMPLE.encode("utf-8"), "text/markdown", "src/md-b"
    )
    doc = await harness.read_ir(result.parse_ir_storage_object_id)

    by_id = {e.element_id: e for e in doc.elements}
    headings = {e.text: e for e in doc.elements if e.element_type == "heading"}
    h3 = headings["补充说明"]
    h2 = by_id[h3.parent_id]
    assert h2.text == "告警对照表"
    assert by_id[h2.parent_id].text == "设备告警处理手册"

    # 表格资产：3 列表头 + 2 数据行，cell 保 raw text
    tables = [a for a in doc.structured_assets.values() if getattr(a, "rows", None)]
    assert tables, "TableAsset missing"
    table = tables[0]
    assert table.rows == 3 and table.columns == 3
    assert len([c for c in table.cells if c.is_header]) == 3
    assert any("0x1001" in c.text for c in table.cells)

    # 行级证据回溯（SRS §A01 line-addressable）
    lines = _MD_SAMPLE.splitlines()
    evidence_failures = []
    for element in doc.elements:
        for span in element.source_spans:
            loc = span.source_locator or {}
            if "line_start" in loc and span.raw_text is not None:
                if span.raw_text not in _MD_SAMPLE or loc["line_start"] >= len(lines):
                    evidence_failures.append((element.element_id, span.span_id))
    assert not evidence_failures, evidence_failures


# ---------------------------------------------------------------------------
# TXT 全链路
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_txt_end_to_end_no_token_split_and_idempotent(tmp_path) -> None:
    """TXT：三段 == 3 个 paragraph；长段不切分；同 frozen 二次 run reused。"""
    harness = _Harness(LegacyPlainTextParser(), tmp_path)
    result = await harness.stage_and_run(
        _TXT_SAMPLE.encode("utf-8"), "text/plain", "src/txt-a"
    )
    assert result.status == "SUCCEEDED"
    record = await harness.runs.get(result.parse_run_id)
    assert record is not None
    assert record.parser_id == LEGACY_TXT_PARSER_ID

    doc = await harness.read_ir(result.parse_ir_storage_object_id)
    paragraphs = [e for e in doc.elements if e.element_type == "paragraph"]
    assert len(paragraphs) == 3
    assert paragraphs[1].text.startswith("第二段")
    # 无 token 切分（旧 PlainTextParser 300-token 行为不复现）：长段完整
    assert paragraphs[1].text.endswith("继续延续。")
    assert paragraphs[2].text == "第三段。"

    # 幂等（SRS §2.2）：同 frozen 二次 run 复用同一投影与制品
    result2 = await harness.service.run(
        _make_frozen(_TXT_SAMPLE.encode("utf-8"), "text/plain", "src/txt-a")
    )
    assert result2.reused is True
    assert result2.parse_run_id == result.parse_run_id


# ---------------------------------------------------------------------------
# Registry 路由
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_replays_when_registered_object_missing(tmp_path) -> None:
    """注册行在、对象被外部删除（SRS §8.6 完整性场景）→ 重放内容寻址字节."""
    import hashlib as _hl

    harness = _Harness(LegacyPlainTextParser(), tmp_path)
    content = _TXT_SAMPLE.encode("utf-8")
    first = await harness.stage_and_run(content, "text/plain", "src/txt-dedup")

    # 直接删除 MinIO 对象但保留注册行 + 投影行（模拟外部删除/事故残留）
    so = await harness.storage_objects.get(first.parse_ir_storage_object_id)
    await harness.store.delete(
        ObjectLocation(bucket=so.bucket, object_key=so.object_key)
    )

    # 投影行仍是 SUCCEEDED，但内容变了（新 hash）→ 新 run；注册行同 key
    # 命中但对象缺失 → service 必须重放字节而不是盲信注册行
    new_content = "全新内容，另一份文档。\n\n第二段。".encode("utf-8")
    second = await harness.stage_and_run(new_content, "text/plain", "src/txt-d2")
    assert second.status == "SUCCEEDED"
    doc = await harness.read_ir(second.parse_ir_storage_object_id)
    assert doc.elements[0].text == "全新内容，另一份文档。"

    # 原内容重跑：同 key 注册行在、对象已被我们删除 → 重放后可读回
    third = await harness.service.run(
        _make_frozen(content, "text/plain", "src/txt-dedup")
    )
    # 投影行命中 SUCCEEDED 幂等复用（对象层重放在 read_ir 校验）
    doc3 = await harness.read_ir(third.parse_ir_storage_object_id)
    assert doc3.elements[0].text == "第一段。"


def test_registry_routes_by_mime() -> None:
    registry = build_default_registry()
    md = registry.select_for("text/markdown")
    txt = registry.select_for("text/plain")
    assert md is not None and md.parser_id == LEGACY_MARKDOWN_PARSER_ID
    assert txt is not None and txt.parser_id == LEGACY_TXT_PARSER_ID
    # M3.5：PDF 由已实现的 native_pdf 承接；占位槽位（docling/cloud_vlm）
    # 仍注册但许可未过审，Router 不会选为 primary（SRS §C04）
    pdf_slot = registry.select_for("application/pdf")
    assert pdf_slot is not None and pdf_slot.parser_id == "native_pdf"
