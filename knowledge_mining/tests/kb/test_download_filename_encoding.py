"""下载路由头编码回归：非 ASCII 文件名不得炸 latin-1（RFC 5987）.

曾因 ``Content-Disposition`` 直接内嵌中文文件名抛 UnicodeEncodeError（500）。
用 duck-typed DocumentService stub 直接驱动路由函数，无需 PG/对象存储。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.routes.documents import download_document


class _StubService:
    def __init__(self, filename: str, mime: str, payload: bytes) -> None:
        self._filename = filename
        self._mime = mime
        self._payload = payload

    async def download_object(self, *, document_id: str, user_id: str):
        async def _stream():
            yield self._payload

        return self._filename, self._mime, _stream()


@pytest.mark.asyncio
async def test_non_ascii_filename_download_headers():
    svc = _StubService(
        "端到端验收手册.md", "text/markdown", "# 中文内容\n".encode("utf-8"),
    )
    response = await download_document(
        "kb-1", "doc-1", user={"id": "alice"}, svc=svc,
    )
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    # RFC 5987：filename* 承载 UTF-8 百分号编码原名；filename 给 ASCII 回落。
    assert "filename*=UTF-8''" in disposition
    assert "%E7%AB%AF%E5%88%B0%E7%AB%AF" in disposition  # 「端到端」
    assert "验收" not in disposition  # 头本身必须纯 ASCII
    assert response.body.decode("utf-8").startswith("# 中文内容")


@pytest.mark.asyncio
async def test_ascii_filename_download_headers_unchanged():
    svc = _StubService("report.txt", "text/plain", b"hello")
    response = await download_document(
        "kb-1", "doc-1", user={"id": "alice"}, svc=svc,
    )
    disposition = response.headers["content-disposition"]
    assert 'filename="report.txt"' in disposition
    assert "filename*=UTF-8''report.txt" in disposition
