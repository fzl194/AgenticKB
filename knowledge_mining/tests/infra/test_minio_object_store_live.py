"""真 MinIO 集成测试（环境门控，默认 skip）.

对齐业务目标「对接 MinIO」：用生产配置（main_control_service storage.yaml）
驱动 MinioObjectStore 与 DocumentService 上传链路。仅在显式开启时运行：

  KB_RUN_MINIO_E2E=1 python -m pytest tests/infra/test_minio_object_store_live.py

覆盖：内容寻址写入 / 校验读回 / 注册行 / 文档指针 / 幂等重复上传复用 /
删除清理。数据用独立前缀，跑完自清理。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("KB_RUN_MINIO_E2E") != "1",
    reason="set KB_RUN_MINIO_E2E=1 to run live MinIO integration tests",
)

REPO = Path(__file__).resolve().parents[2]


def _store_and_config():
    from knowledge_mining.mining.infra.object_store.config import (
        ObjectStoreConfig,
    )
    from knowledge_mining.mining.infra.object_store.factory import (
        make_object_store,
    )

    config = ObjectStoreConfig.from_yaml(
        REPO.parent / "main_control_service" / "config" / "system" / "storage.yaml"
    )
    assert config.provider == "minio", "live test requires provider=minio"
    return make_object_store(config), config


@pytest.mark.asyncio
async def test_minio_roundtrip_and_upload_pipeline():
    import hashlib
    from knowledge_mining.mining.contracts.file_management import (
        StorageObjectRecord,
    )
    from knowledge_mining.mining.contracts.storage.types import (
        ObjectLocation,
        PutOptions,
    )
    from knowledge_mining.mining.file_management.repositories_memory import (
        MemoryStorageObjectRepository,
    )
    from knowledge_mining.mining.infra.object_store.keys import build_object_key
    from knowledge_mining.mining.kb.services.document_service import (
        DocumentService,
    )

    store, config = _store_and_config()
    await store.ensure_buckets()
    registry = MemoryStorageObjectRepository()
    bucket = f"{config.bucket_prefix}source"

    # 1. 内容寻址写入 + 读回校验
    payload = "# MinIO live\n\n真库往返验证。\n".encode("utf-8")
    sha = hashlib.sha256(payload).hexdigest()
    key = build_object_key("source", sha)

    async def _chunked():
        yield payload

    put = await store.put_stream(
        ObjectLocation(bucket=bucket, object_key=key), _chunked(),
        PutOptions(artifact_class="source", expected_sha256=sha),
    )
    assert put.sha256 == sha
    chunks = []
    async for chunk in store.get_stream(ObjectLocation(bucket=bucket, object_key=key)):
        chunks.append(chunk)
    assert b"".join(chunks) == payload

    # 2. 注册行 + DocumentService 上传链路（幂等复用同一对象）
    await registry.register(StorageObjectRecord(
        id="so_live", provider="minio", bucket=bucket, object_key=key,
        object_version_id=None, sha256=sha, size=len(payload),
        mime="text/markdown", artifact_class="source", state="AVAILABLE",
        created_at="2026-08-21T00:00:00+00:00",
    ))

    class _Db:
        async def get_kb(self, kb_id):
            return {"id": kb_id, "domain": "live"}

        async def is_visible(self, **kw):
            return True

        async def can_write(self, **kw):
            return True

        async def insert_document_from_storage(self, **values):
            return {"id": "doc-live", "storage_path": None, **values}

    service = DocumentService(
        _Db(), object_store=store, storage_objects=registry,
        source_bucket=bucket,
    )
    doc = await service.upload(
        kb_id="kb-live", owner_id="live", filename="minio-live.md",
        content=payload, directory_path="", mime="text/markdown",
    )
    assert doc["storage_object_id"] == "so_live"  # 复用已注册对象，不重复上传
    assert doc["source_raw_hash"] == sha

    # 3. 清理
    await store.delete(ObjectLocation(bucket=bucket, object_key=key))
    print(f"minio live ok: bucket={bucket} key={key[:24]}…")
