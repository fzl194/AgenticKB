"""Legacy .doc conversion image observability."""
from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_mining.mining.ingestion import ingest_directory
from knowledge_mining.mining.infra.docx_parser import count_embedded_images


def test_count_embedded_images_on_fixture(tmp_path: Path):
    pytest.importorskip("docx")
    from knowledge_mining.tests.test_md_html_docx_images import (
        _png_bytes,
        _write_minimal_docx_with_png,
    )

    docx = _write_minimal_docx_with_png(tmp_path / "x.docx", _png_bytes())
    assert count_embedded_images(str(docx)) >= 1


def test_doc_ingest_records_converted_image_count(tmp_path: Path, monkeypatch):
    pytest.importorskip("docx")
    from knowledge_mining.tests.test_md_html_docx_images import (
        _png_bytes,
        _write_minimal_docx_with_png,
    )

    converted = _write_minimal_docx_with_png(tmp_path / "out.docx", _png_bytes())
    fake_doc = tmp_path / "legacy.doc"
    fake_doc.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 32)

    import knowledge_mining.mining.ingestion as ingestion

    monkeypatch.setattr(ingestion, "doc_to_docx", lambda _p: converted)
    docs, _summary = ingest_directory(tmp_path)
    doc = next(d for d in docs if d.file_name == "legacy.doc")
    assert doc.file_type == "docx"
    assert doc.metadata_json.get("source_format") == "doc"
    assert doc.metadata_json.get("converted_image_count", 0) >= 1
    assert "image_conversion_warning" not in doc.metadata_json


def test_doc_ingest_warns_when_zero_images(tmp_path: Path, monkeypatch):
    fake_doc = tmp_path / "legacy.doc"
    fake_doc.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 8)
    empty_docx = tmp_path / "empty.docx"
    empty_docx.write_bytes(b"not-a-real-docx")

    import knowledge_mining.mining.ingestion as ingestion

    monkeypatch.setattr(ingestion, "doc_to_docx", lambda _p: empty_docx)
    monkeypatch.setattr(
        "knowledge_mining.mining.infra.docx_parser.count_embedded_images",
        lambda _p: 0,
    )
    docs, _ = ingest_directory(tmp_path)
    doc = next(d for d in docs if d.file_name.endswith(".doc"))
    assert doc.metadata_json.get("converted_image_count") == 0
    assert (
        doc.metadata_json.get("image_conversion_warning")
        == "no_embedded_images_after_doc_conversion"
    )
