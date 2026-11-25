"""S1 · 旧二进制 Office（.doc/.xls/.ppt）→ OOXML 转换通道泛化的单元闸门。

Seam：
- 转换器：``office_to_ooxml(src, target_format)``（doc_to_docx 保持为
  target="docx" 的薄包装，旧链 ingestion 零感知）；
- 解析服务：``DocumentParseService._convert_legacy_office``（msword/ms-excel/
  ms-powerpoint → docx/xlsx/pptx，产物对象键沿用 doc 的既有约定，
  幂等复用派生对象）。

真实文件的端到端验收（上传→挖掘→检索）见仓库根 temp-test-corpus 流程，
不在本文件。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from knowledge_mining.mining.frozen_input.contracts import FrozenInput
from knowledge_mining.mining.ingestion import doc_preprocessing
from knowledge_mining.mining.parse_operator.service import DocumentParseService

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _make_frozen(mime: str, object_key: str, filename: str) -> FrozenInput:
    return FrozenInput(
        document_id="doc-1",
        source_storage_object_id="obj-1",
        source_raw_hash="a" * 64,
        source_content_revision=1,
        mime=mime,
        size=128,
        original_filename=filename,
        captured_at="2026-11-25T00:00:00Z",
        provider="minio",
        bucket="kb-source",
        object_key=object_key,
        object_version_id=None,
    )


class _Stat:
    def __init__(self, sha256: str, size: int) -> None:
        self.sha256 = sha256
        self.size = size


class _FakeStore:
    """对象存储最小桩：stat/get_stream/put_stream。"""

    def __init__(self, source_bytes: bytes) -> None:
        self._source = source_bytes
        self.puts: list[tuple[str, bytes, dict]] = []

    async def stat(self, location):
        raise KeyError("no derived object yet")

    async def get_stream(self, location):
        yield self._source

    async def put_stream(self, location, stream, options):
        data = b"".join([chunk async for chunk in stream])
        self.puts.append((location.object_key, data, dict(vars(options))))
        return _Stat(hashlib.sha256(data).hexdigest(), len(data))


def _service(store) -> DocumentParseService:
    svc = DocumentParseService.__new__(DocumentParseService)
    svc._store = store
    return svc


# ---------------------------------------------------------------------------
# 转换器分派
# ---------------------------------------------------------------------------

def test_office_to_ooxml_dispatches_target_format(tmp_path, monkeypatch):
    calls = {}

    def fake_soffice_convert(src, soffice, out_dir, target_format="docx"):
        calls["target"] = target_format
        out = out_dir / (src.stem + f".{target_format}")
        out.write_bytes(b"converted")
        return out

    monkeypatch.setattr(doc_preprocessing, "_find_soffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(doc_preprocessing, "_convert_with_soffice", fake_soffice_convert)
    src = tmp_path / "legacy.xls"
    src.write_bytes(b"\xd0\xcf legacy")

    out = doc_preprocessing.office_to_ooxml(src, "xlsx")

    assert out.name == "legacy.xlsx"
    assert calls["target"] == "xlsx"


def test_office_to_ooxml_rejects_unknown_target(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_preprocessing, "_find_soffice", lambda: "/usr/bin/soffice")
    src = tmp_path / "legacy.xls"
    src.write_bytes(b"x")
    with pytest.raises(ValueError):
        doc_preprocessing.office_to_ooxml(src, "ppt")


def test_doc_to_docx_routes_to_docx_target(tmp_path, monkeypatch):
    calls = {}

    def fake_ooxml(src, target_format):
        calls["target"] = target_format
        out = tmp_path / "out.docx"
        out.write_bytes(b"converted")
        return out

    monkeypatch.setattr(doc_preprocessing, "office_to_ooxml", fake_ooxml)
    src = tmp_path / "legacy.doc"
    src.write_bytes(b"\xd0\xcf")

    assert doc_preprocessing.doc_to_docx(src).name == "out.docx"
    assert calls["target"] == "docx"


# ---------------------------------------------------------------------------
# 解析服务重定向（frozen 重指派生对象）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_convert_legacy_office_redirects_xls(monkeypatch, tmp_path):
    converted = tmp_path / "converted.xlsx"
    payload = b"PK converted xlsx bytes"
    converted.write_bytes(payload)

    def fake_converter(src, target_format):
        assert target_format == "xlsx"
        return converted

    monkeypatch.setattr(
        "knowledge_mining.mining.ingestion.doc_preprocessing.office_to_ooxml",
        fake_converter,
    )
    store = _FakeStore(b"\xd0\xcf legacy xls")
    svc = _service(store)

    frozen = _make_frozen("application/vnd.ms-excel", "kb-source/doc1.xls", "报表.xls")
    out = await svc._convert_legacy_office(frozen)

    assert out.mime == _XLSX_MIME
    assert out.object_key == "kb-source/doc1.xls--xls2xlsx-" + "a" * 16 + ".xlsx"
    assert out.source_raw_hash == hashlib.sha256(payload).hexdigest()
    assert out.size == len(payload)
    assert len(store.puts) == 1
    key, data, opts = store.puts[0]
    assert data == payload
    assert opts["artifact_class"] == "derived"
    assert opts["mime"] == _XLSX_MIME


@pytest.mark.asyncio
async def test_convert_legacy_office_msword_key_convention_kept(monkeypatch, tmp_path):
    """doc 的派生对象键沿用 --doc2docx-（存量对象复用约定，不换命名）。"""
    converted = tmp_path / "converted.docx"
    converted.write_bytes(b"PK docx")

    monkeypatch.setattr(
        "knowledge_mining.mining.ingestion.doc_preprocessing.office_to_ooxml",
        lambda src, target_format: (converted if target_format == "docx" else None),
    )
    store = _FakeStore(b"\xd0\xcf legacy doc")
    svc = _service(store)

    frozen = _make_frozen("application/msword", "kb-source/doc1.doc", "文档.doc")
    out = await svc._convert_legacy_office(frozen)

    assert out.mime == _DOCX_MIME
    assert "--doc2docx-" in out.object_key


@pytest.mark.asyncio
async def test_convert_legacy_office_redirects_ppt(monkeypatch, tmp_path):
    converted = tmp_path / "converted.pptx"
    payload = b"PK converted pptx"
    converted.write_bytes(payload)

    monkeypatch.setattr(
        "knowledge_mining.mining.ingestion.doc_preprocessing.office_to_ooxml",
        lambda src, target_format: converted,
    )
    store = _FakeStore(b"\xd0\xcf legacy ppt")
    svc = _service(store)

    frozen = _make_frozen(
        "application/vnd.ms-powerpoint", "kb-source/doc1.ppt", "胶片.ppt"
    )
    out = await svc._convert_legacy_office(frozen)

    assert out.mime == _PPTX_MIME
    assert "--ppt2pptx-" in out.object_key


@pytest.mark.asyncio
async def test_convert_legacy_office_passthrough_non_legacy():
    svc = _service(_FakeStore(b"anything"))
    frozen = _make_frozen("application/pdf", "kb-source/doc1.pdf", "文档.pdf")
    out = await svc._convert_legacy_office(frozen)
    assert out is frozen


@pytest.mark.asyncio
async def test_convert_legacy_office_idempotent_reuses_derived():
    """派生对象已存在（stat 命中）→ 直接复用哈希，不再转换/上传。"""
    existing_hash = "b" * 64
    store = _FakeStore(b"\xd0\xcf legacy")

    async def fake_stat(location):
        return _Stat(existing_hash, 999)

    store.stat = fake_stat  # type: ignore[method-assign]
    svc = _service(store)

    frozen = _make_frozen("application/vnd.ms-excel", "kb-source/doc1.xls", "报表.xls")
    out = await svc._convert_legacy_office(frozen)

    assert out.source_raw_hash == existing_hash
    assert out.size == 999
    assert store.puts == []


@pytest.mark.asyncio
async def test_convert_legacy_office_failure_raises(monkeypatch):
    def boom(src, target_format):
        raise RuntimeError("soffice died")

    monkeypatch.setattr(
        "knowledge_mining.mining.ingestion.doc_preprocessing.office_to_ooxml",
        boom,
    )
    svc = _service(_FakeStore(b"\xd0\xcf legacy"))

    frozen = _make_frozen("application/vnd.ms-excel", "kb-source/doc1.xls", "报表.xls")
    with pytest.raises(Exception, match="soffice died"):
        await svc._convert_legacy_office(frozen)
