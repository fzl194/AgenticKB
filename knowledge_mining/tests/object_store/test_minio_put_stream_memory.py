"""P01-S1 前置：MinioObjectStore.put_stream 必须真流式（内存有界）。

原实现 `_drain` 把整个流 join 成单个 bytes 再落盘——签名流式、实现整包，
100MB 上传 = 进程 RSS +100MB。改为增量 sha256 + 分块写临时文件，内存
峰值与对象大小无关。
"""
from __future__ import annotations

import asyncio
import hashlib
import tracemalloc
import pytest

from knowledge_mining.mining.contracts.storage.types import ObjectLocation, PutOptions
from knowledge_mining.mining.infra.object_store.minio import MinioObjectStore


CHUNK = 256 * 1024
TOTAL = 32 * 1024 * 1024  # 32MB 流


async def _gen(total: int, chunk: int) -> asyncio.AsyncIterator[bytes]:
    payload = b"x" * chunk
    sent = 0
    while sent < total:
        n = min(chunk, total - sent)
        yield payload[:n]
        sent += n


def _make_store_with_stubbed_put() -> MinioObjectStore:
    store = object.__new__(MinioObjectStore)
    captured: dict = {}

    def _stub_blocking(location, tmp_path, options, size, sha):
        captured["size"] = size
        captured["sha"] = sha
        # 流式校验磁盘产物（read_bytes 会自己把 32MB 读回内存，污染峰值断言）
        h = hashlib.sha256()
        disk_size = 0
        with open(tmp_path, "rb") as fh:
            while True:
                block = fh.read(256 * 1024)
                if not block:
                    break
                h.update(block)
                disk_size += len(block)
        captured["disk_size"] = disk_size
        captured["disk_sha"] = h.hexdigest()
        return type("R", (), {"version_id": None, "etag": sha[:32],
                              "sha256": sha, "size": size})()

    store._put_file_blocking = _stub_blocking  # type: ignore[method-assign]
    store._captured = captured  # type: ignore[attr-defined]
    return store


@pytest.mark.asyncio
async def test_put_stream_memory_bounded_and_checksum_correct():
    store = _make_store_with_stubbed_put()
    expected_sha = hashlib.sha256(b"x" * TOTAL).hexdigest()

    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    result = await store.put_stream(
        ObjectLocation(bucket="b", object_key="k"),
        _gen(TOTAL, CHUNK),
        PutOptions(artifact_class="source", mime="application/pdf",
                   expected_sha256=expected_sha, content_length=TOTAL),
    )
    peak = tracemalloc.get_traced_memory()[1] - before
    tracemalloc.stop()

    assert result.sha256 == expected_sha
    cap = store._captured  # type: ignore[attr-defined]
    assert cap["size"] == TOTAL == cap["disk_size"]
    assert cap["sha"] == cap["disk_sha"] == expected_sha
    # 32MB 对象：峰值必须远小于对象大小（允许分块缓冲与开销，阈值 8MB）
    assert peak < 8 * 1024 * 1024, f"memory peak {peak / 1024 / 1024:.1f}MB — put_stream 仍在整包物化"


@pytest.mark.asyncio
async def test_put_stream_checksum_mismatch_still_bounded():
    store = _make_store_with_stubbed_put()
    from knowledge_mining.mining.contracts.storage.errors import ChecksumMismatch
    with pytest.raises(ChecksumMismatch):
        await store.put_stream(
            ObjectLocation(bucket="b", object_key="k"),
            _gen(TOTAL, CHUNK),
            PutOptions(artifact_class="source", mime="application/pdf",
                       expected_sha256="0" * 64, content_length=TOTAL),
        )


@pytest.mark.asyncio
async def test_put_stream_empty_stream_ok():
    store = _make_store_with_stubbed_put()

    async def empty():
        return
        yield  # pragma: no cover

    result = await store.put_stream(
        ObjectLocation(bucket="b", object_key="k"),
        empty(),
        PutOptions(artifact_class="source", mime="text/plain",
                   expected_sha256=hashlib.sha256(b"").hexdigest(), content_length=0),
    )
    assert result.size == 0
    cap = store._captured  # type: ignore[attr-defined]
    assert cap["disk_size"] == 0
