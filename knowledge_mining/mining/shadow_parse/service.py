"""Shadow Parse orchestration service (M2, SRS §2.2 / §C08 / §4.6).

``ShadowParseService`` 驱动一次影子解析执行，全程与现有发布链路硬隔离：

```text
FrozenInput（SRS §3.2 冻结输入）
  -> ObjectStoreSourceArtifactReader.open_stream   # 流式读 + sha256 校验
  -> decode utf-8（严格）                            # 坏字节即 FAILED
  -> DocumentParser.parse(text, mime)               # 注入的 parser Protocol（§C06）
  -> ParseIRNormalizer.normalize(...)               # 注入的 normalizer Protocol（§C07）
  -> ParsedDocument.to_dict() -> JSON               # 制品字节
  -> ObjectStorePort.put_stream（parse bucket）     # artifact_class=parse_ir
  -> StorageObjectRepository.register（先 find_by_location 去重，D-002）
  -> ParseRunRepository.upsert（SUCCEEDED 投影行）
```

边界纪律（M2 退出条件）：
- **绝不写** ``asset_document_snapshots`` / ``asset_raw_segments`` /
  ``mining_run_documents`` —— 影子链路只落 Parse IR 制品与 ``asset_parse_runs``
  投影，不影响现有发布。
- 只依赖注入的 Protocol（parser / normalizer / ObjectStorePort / 两个仓储），
  **不 import 任何具体 parse_adapters 实现**（该包由适配器侧并行开发）。

幂等（SRS §2.2）：``run`` 先做幂等探针——命中同
``(document_id, source_raw_hash, parser_fingerprint)`` 的 SUCCEEDED 投影行时
直接返回 ``reused=True``，不重复解析、不写对象。

错误处理：读流 hash 不匹配（``StorageObjectCorrupt``）、解码失败（包装为
``ValueError``）、parse / normalize 异常，均先落一行 FAILED 投影（含
``error_message``）再原样 re-raise，调用方决定重试策略。

References:
- SRS §2.2（幂等复用）、§4.6（一次解析执行）、§C08（Shadow Parse）。
- ADR-0003 D-002（内容寻址去重）、D-020（location 寻址）、D-022（服务分层）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from knowledge_mining.mining.contracts.file_management import (
    StorageObjectRecord,
    StorageObjectRepository,
)
from knowledge_mining.mining.contracts.parse_ir.types import ParsedDocument
from knowledge_mining.mining.contracts.parser_adapter import (
    DocumentParser,
    ParseIRNormalizer,
)
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.contracts.storage.types import (
    ObjectLocation,
    PutOptions,
)
from knowledge_mining.mining.frozen_input.contracts import FrozenInput
from knowledge_mining.mining.frozen_input.source_reader import (
    ObjectStoreSourceArtifactReader,
)
from knowledge_mining.mining.infra.object_store.keys import build_object_key
from knowledge_mining.mining.shadow_parse.contracts import (
    ParseRunRecord,
    ParseRunRepository,
    ShadowParseResult,
)

# IR 制品上传分块大小（与 FakeObjectStore / source_reader 的 64 KiB 一致）。
_CHUNK_SIZE = 64 * 1024

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


async def _chunked(payload: bytes) -> AsyncIterator[bytes]:
    """把已物化的制品字节切成 64 KiB 块喂给 ``put_stream``。"""
    for offset in range(0, len(payload), _CHUNK_SIZE):
        yield payload[offset : offset + _CHUNK_SIZE]


class ShadowParseService:
    """编排一次影子解析执行（SRS §C08；只进影子链路，不进发布）。"""

    def __init__(
        self,
        *,
        object_store: ObjectStorePort,
        parse_runs: ParseRunRepository,
        storage_objects: StorageObjectRepository,
        parser: DocumentParser,
        normalizer: ParseIRNormalizer,
        reader: ObjectStoreSourceArtifactReader | None = None,
        config: Any | None = None,
        bucket_prefix: str | None = None,
        parse_bucket: str | None = None,
    ) -> None:
        self._store = object_store
        self._parse_runs = parse_runs
        self._storage_objects = storage_objects
        self._parser = parser
        self._normalizer = normalizer
        self._reader = reader or ObjectStoreSourceArtifactReader(
            object_store,
            Path(tempfile.gettempdir()) / "shadow_parse",
        )
        # bucket 解析优先级：显式 parse_bucket > bucket_prefix > config.bucket_prefix。
        # 无任何配置时 fail-fast：环境前缀不可猜测（写错命名空间 = 制品落错租户）。
        prefix = (
            bucket_prefix
            or getattr(config, "bucket_prefix", None)
        )
        if not prefix and not parse_bucket:
            raise ValueError(
                "ShadowParseService requires a bucket prefix (bucket_prefix=... "
                "or parse_bucket=...); refusing to guess a default namespace"
            )
        self._parse_bucket_name = parse_bucket or f"{prefix}parse"

    # -- 入口 ---------------------------------------------------------------

    async def run(
        self,
        frozen: FrozenInput,
        *,
        parse_run_id: str | None = None,
    ) -> ShadowParseResult:
        """执行一次影子解析；返回 :class:`ShadowParseResult`。

        幂等探针命中已有 SUCCEEDED 投影时直接返回 ``reused=True``（SRS §2.2）。
        任何失败先落 FAILED 投影行再 re-raise 原异常。
        """
        descriptor = self._parser.descriptor
        existing = await self._parse_runs.find_by_document_hash(
            frozen.document_id, frozen.source_raw_hash, descriptor.parser_fingerprint
        )
        if existing is not None and existing.status == "SUCCEEDED":
            reusable = await self._ir_object_available(
                existing.parse_ir_storage_object_id
            )
            if reusable:
                return ShadowParseResult(
                    parse_run_id=existing.id,
                    status="SUCCEEDED",
                    parse_ir_storage_object_id=existing.parse_ir_storage_object_id,
                    element_count=existing.element_count,
                    reused=True,
                )
            # 投影行成功但制品对象缺失（SRS §8.6 完整性事故）：不复用，
            # 落入完整重跑——内容寻址重放同字节后经 upsert 幂等回到原行。

        run_id = parse_run_id or _new_id("parse")
        started_at = _utcnow()
        try:
            doc = await self._read_and_parse(frozen)
            parse_ir_object_id = await self._persist_ir(doc)
        except Exception as exc:  # noqa: BLE001 —— 影子运行必须落 FAILED 后透传
            try:
                await self._record_failure(frozen, run_id, started_at, exc)
            except Exception:  # noqa: BLE001 —— 审计落库失败不得吞掉原始异常
                logger.exception(
                    "failed to persist FAILED projection for run %s", run_id
                )
            raise
        record = await self._record_success(
            frozen, run_id, started_at, doc, parse_ir_object_id
        )
        return ShadowParseResult(
            parse_run_id=record.id,
            status="SUCCEEDED",
            parse_ir_storage_object_id=parse_ir_object_id,
            element_count=record.element_count,
            reused=False,
        )

    # -- 读取 + 解析 --------------------------------------------------------

    async def _ir_object_available(self, storage_object_id: str | None) -> bool:
        """幂等复用前置校验：IR 对象注册行在且字节确实在（SRS §2.2/§8.6）。"""
        if not storage_object_id:
            return False
        record = await self._storage_objects.get(storage_object_id)
        if record is None:
            return False
        available = await self._store.head_exists(
            ObjectLocation(bucket=record.bucket, object_key=record.object_key)
        )
        if available:
            await self._storage_objects.mark_verified(record.id, _utcnow())
        return available

    async def _read_and_parse(self, frozen: FrozenInput) -> ParsedDocument:
        """流式读冻结对象（sha256 增量校验）、严格解码、parse + normalize。

        - parse 是同步 CPU 工作，经 ``asyncio.to_thread`` 下放（D-021 惯例），
          避免大文档阻塞事件循环。
        - normalize **不传 parse_run_id**：IR 制品字节必须对同一输入完全确定，
          否则内容寻址去重（D-002 / §2.2 幂等）永远 miss；run 归属只记录在
          ``asset_parse_runs`` 投影行，不进制品。
        """
        chunks: list[bytes] = []
        async for chunk in self._reader.open_stream(frozen):
            chunks.append(chunk)
        try:
            text = b"".join(chunks).decode("utf-8")  # 严格：坏字节即失败
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"source bytes of {frozen.document_id!r} are not valid UTF-8: {exc}"
            ) from exc
        artifact = await asyncio.to_thread(self._parser.parse, text, mime=frozen.mime)
        return await asyncio.to_thread(
            self._normalizer.normalize,
            artifact,
            source_raw_hash=frozen.source_raw_hash,
        )

    # -- 制品落存（parse bucket + 对象注册） ---------------------------------

    async def _persist_ir(self, doc: ParsedDocument) -> str:
        """IR JSON 落 parse bucket 并注册 StorageObject；返回 storage_object_id。

        key 为内容寻址（SRS §8.1）：同 IR 字节必然同 key，先
        ``find_by_location`` 探测；命中**且对象确实在**（head_exists，SRS
        §8.6：注册行在而对象缺失属完整性事故，不得盲信注册行）才复用；
        对象缺失时重放同内容寻址字节（同 key 同 sha，幂等安全），并刷新
        注册行的校验时间。
        """
        payload = json.dumps(
            doc.to_dict(), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        sha = hashlib.sha256(payload).hexdigest()
        key = build_object_key("parse_ir", sha)
        bucket = self._parse_bucket_name
        location = ObjectLocation(bucket=bucket, object_key=key)

        existing = await self._storage_objects.find_by_location(bucket, key, None)
        if existing is not None and await self._store.head_exists(location):
            await self._storage_objects.mark_verified(existing.id, _utcnow())
            return existing.id  # D-002：同 IR 字节复用对象注册行

        put = await self._store.put_stream(
            location,
            _chunked(payload),
            PutOptions(
                artifact_class="parse_ir",
                mime="application/json",
                expected_sha256=sha,
                metadata={"schema_version": doc.schema_version},
            ),
        )
        if existing is not None:
            # 注册行在但对象曾缺失：字节已重放，刷新校验时间后复用原行。
            await self._storage_objects.mark_verified(existing.id, _utcnow())
            return existing.id
        record = await self._storage_objects.register(
            StorageObjectRecord(
                id=_new_id("so"),
                provider=self._provider(),
                bucket=bucket,
                object_key=key,
                object_version_id=put.version_id,
                sha256=put.sha256,
                size=put.size,
                mime="application/json",
                artifact_class="parse_ir",
                state="AVAILABLE",
                etag=put.etag,
                created_at=_utcnow(),
                last_verified_at=_utcnow(),
            )
        )
        return record.id

    def _provider(self) -> str:
        """从对象存储适配器读 provider 标识（Fake/Minio 均暴露 ``provider``）。

        provider 是溯源/完整性字段，不得猜测（缺属性即配置错误，fail-fast）。
        """
        provider = getattr(self._store, "provider", None)
        if not provider:
            raise ValueError(
                f"object store {type(self._store).__name__} does not expose a "
                "'provider' attribute; cannot register storage object"
            )
        return provider

    # -- 投影行 ---------------------------------------------------------------

    async def _record_success(
        self,
        frozen: FrozenInput,
        run_id: str,
        started_at: str,
        doc: ParsedDocument,
        parse_ir_object_id: str,
    ) -> ParseRunRecord:
        record = ParseRunRecord(
            id=run_id,
            document_id=frozen.document_id,
            source_storage_object_id=frozen.source_storage_object_id,
            source_raw_hash=frozen.source_raw_hash,
            source_content_revision=frozen.source_content_revision,
            parser_id=self._parser.descriptor.parser_id,
            parser_fingerprint=self._parser.descriptor.parser_fingerprint,
            status="SUCCEEDED",
            parse_ir_storage_object_id=parse_ir_object_id,
            parse_ir_schema_version=doc.schema_version,
            element_count=len(doc.elements),
            container_count=len(doc.containers),
            relation_count=len(doc.relations),
            started_at=started_at,
            finished_at=_utcnow(),
            metadata_json=self._run_metadata(doc),
        )
        return await self._parse_runs.upsert(record)

    async def _record_failure(
        self,
        frozen: FrozenInput,
        run_id: str,
        started_at: str,
        exc: Exception,
    ) -> None:
        record = ParseRunRecord(
            id=run_id,
            document_id=frozen.document_id,
            source_storage_object_id=frozen.source_storage_object_id,
            source_raw_hash=frozen.source_raw_hash,
            source_content_revision=frozen.source_content_revision,
            parser_id=self._parser.descriptor.parser_id,
            parser_fingerprint=self._parser.descriptor.parser_fingerprint,
            status="FAILED",
            error_message=f"{type(exc).__name__}: {exc}",
            started_at=started_at,
            finished_at=_utcnow(),
            metadata_json=self._fail_metadata(),
        )
        await self._parse_runs.upsert(record)

    # -- 元数据 ---------------------------------------------------------------

    def _run_metadata(self, doc: ParsedDocument) -> str:
        descriptor = self._parser.descriptor
        return json.dumps(
            {
                "mode": "shadow",  # 影子链路标记：不进发布（M2 退出条件）
                "parser_id": descriptor.parser_id,
                "parser_version": descriptor.version,
                "schema_version": doc.schema_version,
                "warnings": list(doc.diagnostics.warnings),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _fail_metadata(self) -> str:
        descriptor = self._parser.descriptor
        return json.dumps(
            {
                "mode": "shadow",
                "parser_id": descriptor.parser_id,
                "parser_version": descriptor.version,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


__all__ = ["ShadowParseService"]
