"""57号D5：MCP 替换票据（begin-upload 校验矩阵）+ 开箱检查（magic bytes）。

begin-upload 直调路由函数（假 kbdb，免 PG）；magic bytes 纯函数直测。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.kb.routes import mcp_tools
from knowledge_mining.mining.kb.services.document_service import _validate_magic_bytes

pytestmark = pytest.mark.asyncio

KEY = {"user_id": "u-1", "domain": "generic", "status": "active"}
DOC = {
    "id": "doc-1", "kb_id": "kb-1", "document_name": "设备手册.pdf",
    "content_revision": 3,
}


class _FakeKbDB:
    def __init__(self, *, doc=None):
        self._doc = doc

    async def get_user_by_username(self, username):
        return {"id": "u-1", "username": username} if username else None

    async def get_mcp_key(self, key_id):
        return KEY if key_id else None

    async def key_open_kb_ids(self, key_id):
        return ["kb-1"]

    async def is_visible(self, *, kb_id, user_id):
        return kb_id == "kb-1"

    async def can_write(self, *, kb_id, user_id):
        return kb_id == "kb-1"

    async def get_kb(self, kb_id):
        return {"id": kb_id, "domain": "generic"} if kb_id == "kb-1" else None

    async def get_document_identity(self, document_id):
        return self._doc if document_id == self._doc.get("id") else None


def _body(**kw):
    base = {"username": "alice", "key_id": "k-1", "kb_id": "kb-1",
            "filename": "新版.pdf"}
    base.update(kw)
    return base


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


async def test_begin_upload_replace_issues_ticket(_clean_tickets):
    out = await mcp_tools.begin_upload(
        _body(document_id="doc-1", expected_revision=3),
        _request(), kbdb=_FakeKbDB(doc=DOC),
    )
    assert out["ticket"].startswith("up_")
    entry = mcp_tools._TICKETS.peek(out["ticket"])
    assert entry["document_id"] == "doc-1"
    assert entry["expected_revision"] == 3


async def test_begin_upload_replace_revision_conflict_409(_clean_tickets):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await mcp_tools.begin_upload(
            _body(document_id="doc-1", expected_revision=2),
            _request(), kbdb=_FakeKbDB(doc=DOC),
        )
    assert ei.value.status_code == 409
    assert "content_revision" in ei.value.detail


async def test_begin_upload_replace_extension_mismatch_400(_clean_tickets):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await mcp_tools.begin_upload(
            _body(filename="手册.docx", document_id="doc-1", expected_revision=3),
            _request(), kbdb=_FakeKbDB(doc=DOC),
        )
    assert ei.value.status_code == 400
    assert "格式相同" in ei.value.detail


async def test_begin_upload_replace_wrong_kb_404(_clean_tickets):
    from fastapi import HTTPException
    other = {**DOC, "kb_id": "kb-2"}
    with pytest.raises(HTTPException) as ei:
        await mcp_tools.begin_upload(
            _body(document_id="doc-1", expected_revision=3),
            _request(), kbdb=_FakeKbDB(doc=other),
        )
    assert ei.value.status_code == 404


async def test_begin_upload_replace_bad_revision_type_422(_clean_tickets):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await mcp_tools.begin_upload(
            _body(document_id="doc-1", expected_revision="3"),
            _request(), kbdb=_FakeKbDB(doc=DOC),
        )
    assert ei.value.status_code == 422


async def test_begin_upload_without_document_id_ignores_revision(_clean_tickets):
    out = await mcp_tools.begin_upload(
        _body(expected_revision=99),  # 无 document_id：不进入替换校验
        _request(), kbdb=_FakeKbDB(doc=DOC),
    )
    entry = mcp_tools._TICKETS.peek(out["ticket"])
    assert entry["document_id"] == ""
    assert entry["expected_revision"] is None


@pytest.fixture
def _clean_tickets():
    mcp_tools._TICKETS._tickets.clear()
    yield
    mcp_tools._TICKETS._tickets.clear()


# ── 开箱检查（magic bytes 纯函数）──────────────────────────────────────────


def _tmp(tmp_path: Path, head: bytes) -> Path:
    p = tmp_path / "staged.bin"
    p.write_bytes(head)
    return p


def test_magic_pdf_ok(tmp_path):
    _validate_magic_bytes(_tmp(tmp_path, b"%PDF-1.7 rest"), ".pdf")  # 不抛


def test_magic_fake_pdf_rejected(tmp_path):
    with pytest.raises(ValueError, match="文件内容与扩展名不符"):
        _validate_magic_bytes(_tmp(tmp_path, b"<html>not a pdf"), ".pdf")


def test_magic_zip_family_ok(tmp_path):
    _validate_magic_bytes(_tmp(tmp_path, b"PK\x03\x04 zip"), ".zip")
    _validate_magic_bytes(_tmp(tmp_path, b"PK\x05\x06 empty zip"), ".zip")  # 空包


def _ooxml(tmp_path: Path, suffix: str, entries: list[str]) -> Path:
    import zipfile
    p = tmp_path / f"staged{suffix}"
    with zipfile.ZipFile(p, "w") as zf:
        for name in entries:
            zf.writestr(name, "<x/>")
    return p


def test_magic_ooxml_deep_check(tmp_path):
    """codex P2：OOXML 深检——真实包结构（[Content_Types].xml + 入口目录）放行，
    任意 zip 冒充 docx/xlsx/pptx 拒绝。"""
    _validate_magic_bytes(
        _ooxml(tmp_path, ".docx", ["[Content_Types].xml", "word/x.xml"]), ".docx")
    _validate_magic_bytes(
        _ooxml(tmp_path, ".xlsx", ["[Content_Types].xml", "xl/x.xml"]), ".xlsx")
    _validate_magic_bytes(
        _ooxml(tmp_path, ".pptx", ["[Content_Types].xml", "ppt/x.xml"]), ".pptx")

    for suffix, entry in ((".docx", "word/"), (".xlsx", "xl/"), (".pptx", "ppt/")):
        # 任意 zip（无 OOXML 结构）冒充 → 拒
        with pytest.raises(ValueError, match="文件内容与扩展名不符"):
            _validate_magic_bytes(_ooxml(tmp_path, suffix, ["random/file.txt"]), suffix)
        # 有入口目录但缺 [Content_Types].xml → 拒
        with pytest.raises(ValueError, match="文件内容与扩展名不符"):
            _validate_magic_bytes(_ooxml(tmp_path, suffix, [entry + "x.xml"]), suffix)


def test_magic_unchecked_suffixes_pass(tmp_path):
    # md/txt/html/json/chm/hdx 无可靠魔数——不校验（挖掘层兜底）
    for suffix in (".md", ".txt", ".html", ".json", ".chm", ".hdx", ".doc", ".xls"):
        _validate_magic_bytes(_tmp(tmp_path, b"\x00\x01garbage"), suffix)
