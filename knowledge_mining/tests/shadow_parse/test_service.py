"""Tests for ``ShadowParseService``（M2 Shadow Parse 写入链路，SRS §2.2/§C08）.

覆盖（M2 退出条件：影子链路不写发布表——本套件只断言 parse bucket 制品 +
asset_parse_runs 投影 + storage object 注册，从不触碰 snapshots / segments /
mining_run_documents）：

- happy path：对象落 parse bucket（artifact_class=parse_ir）、投影 SUCCEEDED、
  计数正确。
- 幂等重跑：同 frozen 二次 run → reused=True，不重复解析、不新增对象/投影行。
- 解析失败：stub parser raise → FAILED 投影行 + 异常传播。
- hash 不匹配：frozen.source_raw_hash 与对象字节不符 → StorageObjectCorrupt
  传播 + FAILED 投影行。
- 内容寻址去重（D-002）：同 IR 字节（不同 document 的相同内容）复用
  StorageObjectRecord，不再 put。
- 坏字节：适配器解码职责（契约 v1.1）——stub 用宽松断言（异常 + FAILED）。

注入 FakeObjectStore + Memory 仓储 + 测试内定义的 stub parser/normalizer
（实现 contracts.parser_adapter 的 Protocol，不 import 具体 parse_adapters）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys

import pytest

# psycopg-async needs the SelectorEventLoop on Windows（与 frozen_input 测试一致）。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from knowledge_mining.mining.contracts.parse_ir.enums import (  # noqa: E402
    PARSE_IR_SCHEMA_VERSION,
)
from knowledge_mining.mining.contracts.parse_ir.types import (  # noqa: E402
    Container,
    Element,
    ParseIdentity,
    ParsedDocument,
    Relation,
)
from knowledge_mining.mining.contracts.parser_adapter import (  # noqa: E402
    BackendBlock,
    BackendParseArtifact,
    ParserDescriptor,
)
from knowledge_mining.mining.contracts.storage.errors import (  # noqa: E402
    StorageObjectCorrupt,
)
from knowledge_mining.mining.contracts.storage.types import (  # noqa: E402
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.file_management.repositories_memory import (  # noqa: E402
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.frozen_input.contracts import FrozenInput  # noqa: E402
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore  # noqa: E402
from knowledge_mining.mining.infra.object_store.keys import (  # noqa: E402
    build_object_key,
)
from knowledge_mining.mining.shadow_parse.repositories_memory import (  # noqa: E402
    MemoryParseRunRepository,
)
from knowledge_mining.mining.shadow_parse.service import ShadowParseService  # noqa: E402

pytestmark = pytest.mark.asyncio

_BUCKET_PREFIX = "testshadow-"
PARSE_BUCKET = f"{_BUCKET_PREFIX}parse"


# ---------------------------------------------------------------------------
# 测试替身：stub parser / normalizer（实现 Protocol，产最小合法 ParsedDocument）
# ---------------------------------------------------------------------------

_STUB_DESCRIPTOR = ParserDescriptor(
    parser_id="stub-txt",
    display_name="Stub Text Parser",
    version="0.1.0",
    supported_mimes=frozenset({"text/plain", "text/markdown"}),
    parser_fingerprint="stub-fp-v1",
)


class StubParser:
    """最简 DocumentParser：按行产 paragraph 块；``fail=True`` 时抛错。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.descriptor = _STUB_DESCRIPTOR
        self._fail = fail
        self.parse_calls = 0

    def supports(self, mime: str) -> bool:
        return mime.lower() in self.descriptor.supported_mimes

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        self.parse_calls += 1
        if self._fail:
            raise RuntimeError("stub parser boom")
        text = data.decode("utf-8")  # stub：契约 v1.1 bytes 输入
        blocks = tuple(
            BackendBlock(block_type="paragraph", text=line, line_start=i, line_end=i + 1)
            for i, line in enumerate(text.splitlines())
            if line
        )
        return BackendParseArtifact(
            parser_id=self.descriptor.parser_id,
            parser_version=self.descriptor.version,
            mime=mime,
            blocks=blocks,
            raw_output=text,
        )


class StubNormalizer:
    """最简 ParseIRNormalizer：1 container + 2 elements + 1 阅读序关系。

    ParsedDocument.parse_run_id 留空（None）——保证同输入产出的 IR 字节完全
    确定一致，内容寻址去重（D-002）测试才能命中同 key。
    """

    def normalize(
        self,
        artifact: BackendParseArtifact,
        *,
        source_raw_hash: str,
        parse_run_id: str | None = None,
    ) -> ParsedDocument:
        texts = [b.text for b in artifact.blocks[:2]]
        while len(texts) < 2:
            texts.append("")
        return ParsedDocument(
            schema_version=PARSE_IR_SCHEMA_VERSION,
            source_identity=ParseIdentity(
                source_raw_hash=source_raw_hash,
                parser_fingerprint=(
                    f"{artifact.parser_id}@{artifact.parser_version}"
                ),
                parse_ir_schema_version=PARSE_IR_SCHEMA_VERSION,
                normalizer_version="stub-norm-1",
            ),
            containers=(Container(container_id="c0", container_type="page", order_index=0),),
            elements=(
                Element(element_id="e1", element_type="paragraph", order_index=0, text=texts[0]),
                Element(element_id="e2", element_type="paragraph", order_index=1, text=texts[1]),
            ),
            relations=(
                Relation(
                    source_element_id="e1",
                    target_element_id="e2",
                    relation_type="next_in_reading_order",
                    method="stub",
                ),
            ),
        )


class CountingStore(FakeObjectStore):
    """FakeObjectStore + put_stream 计数（断言幂等/去重不再写对象）。"""

    def __init__(self, root_path: str) -> None:
        super().__init__(root_path)
        self.put_count = 0

    async def put_stream(self, location, stream, options):  # noqa: ANN001
        self.put_count += 1
        return await super().put_stream(location, stream, options)


# ---------------------------------------------------------------------------
# 组装 helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _frozen(
    data: bytes,
    *,
    document_id: str = "doc1",
    storage_object_id: str = "so_1",
    bucket: str = "kb1-source",
    raw_hash_override: str | None = None,
) -> FrozenInput:
    sha = raw_hash_override or _sha256(data)
    return FrozenInput(
        document_id=document_id,
        source_storage_object_id=storage_object_id,
        source_raw_hash=sha,
        source_content_revision=1,
        mime="text/plain",
        size=len(data),
        original_filename="doc.txt",
        captured_at="2026-08-13T00:00:00+00:00",
        provider="fake",
        bucket=bucket,
        object_key=build_object_key("source", sha),
        object_version_id=None,
    )


async def _seed_source(store: CountingStore, frozen: FrozenInput, data: bytes) -> None:
    await store.put_bytes(
        ObjectLocation(bucket=frozen.bucket, object_key=frozen.object_key),
        data,
        PutOptions(artifact_class="source", mime="text/plain"),
    )


def _service(
    store: CountingStore,
    parse_runs: MemoryParseRunRepository,
    storage_objects: MemoryStorageObjectRepository,
    parser: StubParser | None = None,
) -> ShadowParseService:
    return ShadowParseService(
        object_store=store,
        parse_runs=parse_runs,
        storage_objects=storage_objects,
        parser=parser or StubParser(),
        normalizer=StubNormalizer(),
        bucket_prefix=_BUCKET_PREFIX,
    )


@pytest.fixture
def harness(tmp_path):
    """(store, parse_runs, storage_objects, parser) 四元组。"""
    store = CountingStore(str(tmp_path / "objects"))
    return store, MemoryParseRunRepository(), MemoryStorageObjectRepository(), StubParser()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_happy_path_persists_ir_and_projection(tmp_path, harness):
    store, parse_runs, storage_objects, parser = harness
    data = "第一段落内容。\n第二段落内容。\n".encode("utf-8")
    frozen = _frozen(data)
    await _seed_source(store, frozen, data)

    result = await _service(store, parse_runs, storage_objects, parser).run(frozen)

    # 返回值：新执行（非复用），计数来自 ParsedDocument。
    assert result.status == "SUCCEEDED"
    assert result.reused is False
    assert result.element_count == 2
    assert result.parse_ir_storage_object_id is not None

    # 投影行：SUCCEEDED + 计数 + 指纹 + schema 版本。
    record = await parse_runs.get(result.parse_run_id)
    assert record is not None
    assert record.status == "SUCCEEDED"
    assert record.document_id == frozen.document_id
    assert record.source_raw_hash == frozen.source_raw_hash
    assert record.parser_id == "stub-txt"
    assert record.parser_fingerprint == "stub-fp-v1"
    assert record.element_count == 2
    assert record.container_count == 1
    assert record.relation_count == 1
    assert record.parse_ir_schema_version == PARSE_IR_SCHEMA_VERSION
    assert record.parse_ir_storage_object_id == result.parse_ir_storage_object_id
    assert record.error_message is None
    assert json.loads(record.metadata_json)["mode"] == "shadow"

    # StorageObject 注册：parse bucket、parse_ir 类、AVAILABLE。
    so = await storage_objects.get(result.parse_ir_storage_object_id)
    assert so is not None
    assert so.bucket == PARSE_BUCKET
    assert so.artifact_class == "parse_ir"
    assert so.state == "AVAILABLE"
    assert so.mime == "application/json"
    assert so.sha256 == _sha256(await _read_object(store, so.bucket, so.object_key))

    # 对象元数据（stat）：artifact_class / mime 正确，内容可回读为合法 IR。
    stat = await store.stat(ObjectLocation(bucket=so.bucket, object_key=so.object_key))
    assert stat.artifact_class == "parse_ir"
    assert stat.mime == "application/json"
    ir = json.loads(await _read_object(store, so.bucket, so.object_key))
    assert len(ir["elements"]) == 2
    assert ir["source_identity"]["source_raw_hash"] == frozen.source_raw_hash
    # IR 可经 from_dict 重建（round-trip 契约）。
    assert ParsedDocument.from_dict(ir).elements[0].element_id == "e1"

    assert store.put_count == 1  # 仅 1 次 parse_ir put_stream（source seeding 走 put_bytes 不计数）


async def _read_object(store: FakeObjectStore, bucket: str, key: str) -> bytes:
    stream = store.get_stream(ObjectLocation(bucket=bucket, object_key=key))
    chunks = [chunk async for chunk in stream]
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# 幂等重跑（SRS §2.2）
# ---------------------------------------------------------------------------


async def test_idempotent_rerun_reuses_succeeded_row(harness):
    store, parse_runs, storage_objects, parser = harness
    data = "alpha\nbeta\n".encode("utf-8")
    frozen = _frozen(data)
    await _seed_source(store, frozen, data)
    service = _service(store, parse_runs, storage_objects, parser)

    first = await service.run(frozen)
    puts_after_first = store.put_count

    second = await service.run(frozen)

    assert second.reused is True
    assert second.parse_run_id == first.parse_run_id
    assert second.parse_ir_storage_object_id == first.parse_ir_storage_object_id
    assert second.element_count == first.element_count
    # 未重复解析、未写对象、未新增投影行。
    assert parser.parse_calls == 1
    assert store.put_count == puts_after_first
    assert parse_runs.count() == 1


# ---------------------------------------------------------------------------
# 解析失败 → FAILED 投影 + 异常传播
# ---------------------------------------------------------------------------


async def test_parse_failure_records_failed_and_reraises(harness):
    store, parse_runs, storage_objects, _ = harness
    failing = StubParser(fail=True)
    data = "alpha\nbeta\n".encode("utf-8")
    frozen = _frozen(data)
    await _seed_source(store, frozen, data)

    with pytest.raises(RuntimeError, match="stub parser boom"):
        await _service(store, parse_runs, storage_objects, failing).run(
            frozen, parse_run_id="parse_fail_1"
        )

    record = await parse_runs.get("parse_fail_1")
    assert record is not None
    assert record.status == "FAILED"
    assert "RuntimeError" in (record.error_message or "")
    assert record.parse_ir_storage_object_id is None
    assert record.parser_fingerprint == "stub-fp-v1"
    # 失败路径不落任何 IR 对象（put_stream 从未被调用）。
    assert store.put_count == 0

    # 修复 parser 后重跑：FAILED 行翻转为 SUCCEEDED（同幂等键覆盖，id 稳定）。
    result = await _service(store, parse_runs, storage_objects).run(frozen)
    assert result.status == "SUCCEEDED"
    assert result.parse_run_id == "parse_fail_1"
    assert parse_runs.count() == 1


# ---------------------------------------------------------------------------
# hash 不匹配（源对象被篡改 / frozen hash 错）→ StorageObjectCorrupt + FAILED
# ---------------------------------------------------------------------------


async def test_source_hash_mismatch_propagates_and_records_failed(harness):
    store, parse_runs, storage_objects, _ = harness
    data = "alpha\nbeta\n".encode("utf-8")
    # frozen 声明的 hash 与对象实际字节不符（模拟篡改/过期绑定）。
    frozen = _frozen(data, raw_hash_override="0" * 64)
    await _seed_source(store, frozen, data)

    with pytest.raises(StorageObjectCorrupt):
        await _service(store, parse_runs, storage_objects).run(
            frozen, parse_run_id="parse_corrupt_1"
        )

    record = await parse_runs.get("parse_corrupt_1")
    assert record is not None
    assert record.status == "FAILED"
    assert "StorageObjectCorrupt" in (record.error_message or "")
    assert record.parse_ir_storage_object_id is None


# ---------------------------------------------------------------------------
# 内容寻址去重（D-002）：同 IR 字节复用 StorageObjectRecord
# ---------------------------------------------------------------------------


async def test_dedup_same_ir_bytes_across_documents(harness):
    store, parse_runs, storage_objects, _ = harness
    data = "shared content\nsecond line\n".encode("utf-8")
    frozen_a = _frozen(data, document_id="doc-a", storage_object_id="so_a")
    frozen_b = _frozen(
        data, document_id="doc-b", storage_object_id="so_b", bucket="kb2-source"
    )
    await _seed_source(store, frozen_a, data)
    await _seed_source(store, frozen_b, data)
    service = _service(store, parse_runs, storage_objects)

    result_a = await service.run(frozen_a)
    puts_after_a = store.put_count
    result_b = await service.run(frozen_b)

    # doc-b 是独立文档（非幂等复用），但 IR 字节相同 → 复用同一 StorageObject。
    assert result_b.reused is False
    assert result_b.parse_ir_storage_object_id == result_a.parse_ir_storage_object_id
    assert store.put_count == puts_after_a  # 没有第二次 IR put
    # 两条投影行各自指向同一个 IR 对象注册行。
    assert parse_runs.count() == 2
    so = await storage_objects.get(result_a.parse_ir_storage_object_id)
    assert so is not None and so.bucket == PARSE_BUCKET


# ---------------------------------------------------------------------------
# 严格 UTF-8 解码
# ---------------------------------------------------------------------------


async def test_invalid_utf8_records_failed_and_raises(harness):
    store, parse_runs, storage_objects, _ = harness
    data = b"\xff\xfe\x00bad bytes"
    frozen = _frozen(data)
    await _seed_source(store, frozen, data)

    # 契约 v1.1：decode 责任在适配器——stub 解码坏字节抛 UnicodeDecodeError
    with pytest.raises(Exception):
        await _service(store, parse_runs, storage_objects).run(
            frozen, parse_run_id="parse_decode_1"
        )

    record = await parse_runs.get("parse_decode_1")
    assert record is not None
    assert record.status == "FAILED"
    assert record.parse_ir_storage_object_id is None
    assert record.error_message is not None
