"""Tests for legacy .doc (binary Word) handling.

Covers:
- doc_to_docx backend fallback chain (LibreOffice -> Word COM -> clear error)
- parser routing: .doc must NOT be sent to python-docx (DocxParser)
- ingestion wiring: a .doc is converted to .docx at ingest, file_type -> "docx",
  while provenance (source_uri/file_name) still points at the original .doc

The converter backends are monkeypatched so these tests are deterministic and
run anywhere (no LibreOffice/Word required). One optional live test runs only
when a real backend is available.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_mining.mining.ingestion import doc_preprocessing as dp
from knowledge_mining.mining.ingestion.errors import PreprocessingError


# ===================================================================
# doc_to_docx backend chain
# ===================================================================

def test_doc_to_docx_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        dp.doc_to_docx(tmp_path / "does_not_exist.doc")


def test_doc_to_docx_prefers_soffice(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "a.doc"
    src.write_bytes(b"\xd0\xcf\x11\xe0")  # OLE magic header, content irrelevant
    expected = tmp_path / "a.docx"

    monkeypatch.setattr(dp, "_find_soffice", lambda: "/fake/soffice")
    monkeypatch.setattr(dp, "_word_com_available", lambda: True)  # available but not used
    captured = {}

    def fake_soffice(s, exe, out_dir):
        captured["exe"] = exe
        return expected

    monkeypatch.setattr(dp, "_convert_with_soffice", fake_soffice)
    monkeypatch.setattr(
        dp, "_convert_with_word_com",
        lambda s, out_dir: pytest.fail("should not use Word"),
    )

    assert dp.doc_to_docx(src) == expected
    assert captured["exe"] == "/fake/soffice"


def test_doc_to_docx_falls_back_to_word_com(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "b.doc"
    src.write_bytes(b"\xd0\xcf\x11\xe0")
    expected = tmp_path / "b.docx"

    monkeypatch.setattr(dp, "_find_soffice", lambda: None)
    monkeypatch.setattr(dp, "_word_com_available", lambda: True)
    monkeypatch.setattr(dp, "_convert_with_word_com", lambda s, out_dir: expected)

    assert dp.doc_to_docx(src) == expected


def test_doc_to_docx_no_backend_raises_clear_error(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "c.doc"
    src.write_bytes(b"\xd0\xcf\x11\xe0")

    monkeypatch.setattr(dp, "_find_soffice", lambda: None)
    monkeypatch.setattr(dp, "_word_com_available", lambda: False)

    with pytest.raises(RuntimeError) as exc:
        dp.doc_to_docx(src)
    msg = str(exc.value)
    assert "No .doc converter available" in msg
    assert ".docx" in msg  # actionable hint


def test_doc_to_docx_missing_converter_has_stable_error(
    tmp_path: Path, monkeypatch
) -> None:
    src = tmp_path / "legacy.doc"
    src.write_bytes(b"legacy")
    monkeypatch.setattr(dp, "_find_soffice", lambda: None)
    monkeypatch.setattr(dp, "_word_com_available", lambda: False)

    with pytest.raises(PreprocessingError) as caught:
        dp.doc_to_docx(src)

    assert caught.value.code == "doc_converter_unavailable"


def test_doc_to_docx_converter_failure_has_stable_error(
    tmp_path: Path, monkeypatch
) -> None:
    src = tmp_path / "broken.doc"
    src.write_bytes(b"legacy")
    monkeypatch.setattr(dp, "_find_soffice", lambda: "/fake/soffice")
    monkeypatch.setattr(
        dp,
        "_convert_with_soffice",
        lambda *args: (_ for _ in ()).throw(RuntimeError("private details")),
    )

    with pytest.raises(PreprocessingError) as caught:
        dp.doc_to_docx(src)

    assert caught.value.code == "doc_conversion_failed"
    assert "private details" not in caught.value.safe_message


# ===================================================================
# Parser routing: .doc must not reach python-docx
# ===================================================================

def test_create_parser_doc_not_routed_to_docx() -> None:
    from knowledge_mining.mining.stages.parse import (
        create_parser, DocxParser, PassthroughParser,
    )

    assert isinstance(create_parser("docx"), DocxParser)
    # .doc is converted to .docx at ingest; if a raw "doc" type ever reaches the
    # parser factory it must NOT go to python-docx (which crashes on binary .doc).
    assert not isinstance(create_parser("doc"), DocxParser)
    assert isinstance(create_parser("doc"), PassthroughParser)


# ===================================================================
# Ingestion wiring: .doc converted to .docx on the fly
# ===================================================================

def test_ingest_doc_converts_to_docx(tmp_path: Path, monkeypatch) -> None:
    from knowledge_mining.mining import ingestion

    src = tmp_path / "光缆传输知识.doc"
    src.write_bytes(b"\xd0\xcf\x11\xe0")
    converted = tmp_path / "converted" / "光缆传输知识.docx"

    monkeypatch.setattr(ingestion, "doc_to_docx", lambda p: converted)

    docs, summary = ingestion.ingest_directory(tmp_path)

    assert len(docs) == 1
    doc = docs[0]
    assert doc.file_type == "docx"  # converted -> DocxParser
    assert doc.content == ""  # docx is parsed from the file, not inlined
    # Parser opens the converted .docx ...
    assert doc.file_path == str(converted)
    # ... but provenance still points at the original .doc.
    assert doc.file_name == "光缆传输知识.doc"
    assert doc.source_uri == str(src)
    assert doc.relative_path == "光缆传输知识.doc"
    assert doc.metadata_json.get("source_format") == "doc"
    assert summary["parsed_documents"] == 1


def test_ingest_doc_conversion_failure_registers_without_content(
    tmp_path: Path, monkeypatch
) -> None:
    from knowledge_mining.mining import ingestion

    (tmp_path / "broken.doc").write_bytes(b"\xd0\xcf\x11\xe0")

    def boom(p):
        raise PreprocessingError(
            "doc_converter_unavailable", "No .doc converter available"
        )

    monkeypatch.setattr(ingestion, "doc_to_docx", boom)

    docs, summary = ingestion.ingest_directory(tmp_path)

    assert len(docs) == 1
    doc = docs[0]
    assert doc.content == ""
    assert doc.metadata_json.get("source_format") == "doc"
    assert doc.metadata_json["preprocess_status"] == "failed"
    assert doc.metadata_json["preprocess_error_code"] == "doc_converter_unavailable"
    assert "preprocess_error" in doc.metadata_json
    assert summary["unparsed_documents"] == 1


# ===================================================================
# Optional live test (runs only when a real backend is installed)
# ===================================================================

@pytest.mark.skipif(
    dp._find_soffice() is None and not dp._word_com_available(),
    reason="no .doc converter backend (LibreOffice/Word) installed",
)
def test_doc_to_docx_live_roundtrip(tmp_path: Path) -> None:
    """Create a real .doc via Word COM, convert it, read it back with python-docx."""
    if not dp._word_com_available():
        pytest.skip("live .doc authoring requires Word COM")

    import pythoncom
    import win32com.client as win32

    doc_path = tmp_path / "probe.doc"
    pythoncom.CoInitialize()
    word = win32.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = False
    try:
        d = word.Documents.Add()
        d.Content.Text = "光缆传输知识测试段落。"
        d.SaveAs(str(doc_path), FileFormat=0)  # wdFormatDocument97 (.doc)
        d.Close(False)
    finally:
        word.Quit()
        pythoncom.CoUninitialize()

    out = dp.doc_to_docx(doc_path)
    assert out.is_file()
    assert out.suffix == ".docx"

    Document = pytest.importorskip("docx").Document

    text = "\n".join(p.text for p in Document(str(out)).paragraphs)
    assert "光缆传输知识" in text
