"""批次2b：hdx/chm 与 zip 同构归档上传 + 统一以包名建总文件夹。

决策（2026-08-27）：产品文档压缩包（zip/hdx/chm）上传后自动解压到以
包名新建的文件夹，解压出的每个文件入库为独立文档（全部入库，含图片
等资产——解析失败会如实标注）。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.kb.services.document_service import DocumentService


class _Db:
    def __init__(self):
        self.documents: list[dict] = []

    async def get_kb(self, kb_id):
        return {"id": kb_id, "domain": "generic"}

    async def is_visible(self, *, kb_id, user_id):
        return True

    async def can_write(self, *, kb_id, user_id):
        return True

    async def find_folder_by_path(self, kb_id, path):
        return None

    async def insert_folder(self, **values):
        return {"id": f"folder-{len(values)}", **values}

    async def find_document_by_key(self, kb_id, document_key, *, include_deleted=False):
        return None

    async def revive_document_from_storage(self, document_id, **kw):
        return None

    async def insert_document_from_storage(self, **values):
        doc = {"id": f"doc-{len(self.documents) + 1}", "status": "uploaded",
               "content_revision": 1, **values}
        self.documents.append(doc)
        return doc


def _svc(tmp_path) -> tuple[DocumentService, _Db]:
    db = _Db()
    svc = DocumentService(
        db,  # type: ignore[arg-type]
        object_store=FakeObjectStore(root_path=str(tmp_path / "objects")),
        storage_objects=MemoryStorageObjectRepository(),
        source_bucket="kbs-source",
    )
    return svc, db


def _make_hdx(path: Path, files: dict[str, str]) -> None:
    """HedEx 本质是 zip 包 HTML——造一个改名 .hdx 的 zip。"""
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


@pytest.mark.asyncio
async def test_hdx_extracts_into_archive_named_folder(tmp_path):
    svc, db = _svc(tmp_path)
    hdx = tmp_path / "产品文档.hdx"
    _make_hdx(hdx, {
        "resources/topics/intro.htm": "<html><body>intro</body></html>",
        "resources/topics/config.htm": "<html><body>config</body></html>",
        "resources/images/logo.png": "\x89PNG fake",
        "rootlevel.txt": "root member",
    })
    docs = await svc.upload_archive_path(
        kb_id="kb-1", owner_id="alice", archive_path=hdx, archive_name="产品文档.hdx",
    )
    # 全部入库（含图片资产——决策），且统一进「产品文档/」总文件夹
    places = {(d["directory_path"], d["document_name"]) for d in db.documents}
    assert places == {
        ("产品文档/resources/topics", "intro.htm"),
        ("产品文档/resources/topics", "config.htm"),
        ("产品文档/resources/images", "logo.png"),
        ("产品文档", "rootlevel.txt"),
    }
    assert len(docs) == 4


@pytest.mark.asyncio
async def test_zip_also_gets_archive_named_folder(tmp_path):
    """zip 同样以包名建夹（决策：统一行为，多包不混）。"""
    svc, db = _svc(tmp_path)
    zp = tmp_path / "pack.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("a.txt", "aaa")
        zf.writestr("sub/b.txt", "bbb")
    await svc.upload_archive_path(
        kb_id="kb-1", owner_id="alice", archive_path=zp, archive_name="pack.zip",
    )
    places = {(d["directory_path"], d["document_name"]) for d in db.documents}
    assert places == {("pack", "a.txt"), ("pack/sub", "b.txt")}


@pytest.mark.asyncio
async def test_chm_routes_through_7z_extractor_with_folder_prefix(tmp_path, monkeypatch):
    """CHM 走库存 7z 提取器（此处打桩），成员同样进包名文件夹。"""
    from knowledge_mining.mining.kb.services import document_service as ds_mod

    def fake_extract_chm(src: Path, dst: Path) -> None:
        (dst / "topics").mkdir(parents=True)
        (dst / "topics" / "main.htm").write_text("<html>main</html>")
        (dst / "index.hhc").write_text("toc")

    monkeypatch.setattr(ds_mod, "extract_chm", fake_extract_chm)
    svc, db = _svc(tmp_path)
    chm = tmp_path / "manual.chm"
    chm.write_bytes(b"ITSF fake chm bytes")
    docs = await svc.upload_archive_path(
        kb_id="kb-1", owner_id="alice", archive_path=chm, archive_name="manual.chm",
    )
    places = {(d["directory_path"], d["document_name"]) for d in db.documents}
    assert places == {("manual/topics", "main.htm"), ("manual", "index.hhc")}
    assert len(docs) == 2


@pytest.mark.asyncio
async def test_chm_extraction_failure_raises_value_error(tmp_path, monkeypatch):
    from knowledge_mining.mining.kb.services import document_service as ds_mod

    def boom(src, dst):
        raise RuntimeError("7z failed")

    monkeypatch.setattr(ds_mod, "extract_chm", boom)
    svc, db = _svc(tmp_path)
    chm = tmp_path / "bad.chm"
    chm.write_bytes(b"garbage")
    with pytest.raises(ValueError, match="CHM 解压失败"):
        await svc.upload_archive_path(
            kb_id="kb-1", owner_id="alice", archive_path=chm, archive_name="bad.chm",
        )
    assert db.documents == []


@pytest.mark.asyncio
async def test_chm_member_count_enforced(tmp_path, monkeypatch):
    from knowledge_mining.mining.kb.services import document_service as ds_mod

    def fake_extract_chm(src: Path, dst: Path) -> None:
        dst.mkdir(parents=True)
        for i in range(5):
            (dst / f"f{i}.htm").write_text("x")

    monkeypatch.setattr(ds_mod, "extract_chm", fake_extract_chm)
    svc, db = _svc(tmp_path)
    chm = tmp_path / "big.chm"
    chm.write_bytes(b"x")
    with pytest.raises(ValueError, match="成员数"):
        await svc.upload_archive_path(
            kb_id="kb-1", owner_id="alice", archive_path=chm, archive_name="big.chm",
            max_members=3,
        )


def test_upload_config_accepts_hdx_chm():
    from knowledge_mining.mining.infra import upload_config as uc
    # 直接断言默认值表（无参构造会连控制面，本地不可达）
    assert ".zip .hdx .chm" == uc._DEFAULT_UPLOAD["archive_extensions"]
    assert 2000 == uc._DEFAULT_UPLOAD["max_files_per_request"]


def test_route_archive_detection_covers_new_formats(monkeypatch):
    """_is_archive 语义按扩展名集合判定（模块级 _archive_exts 快照自控制面，
    测试里打桩为新配置集合，验证大小写不敏感与排除项）。"""
    from knowledge_mining.mining.kb.routes import documents as docs_mod
    monkeypatch.setattr(
        docs_mod, "_archive_exts", frozenset({".zip", ".hdx", ".chm"}))
    assert docs_mod._is_archive("a.zip")
    assert docs_mod._is_archive("b.hdx")
    assert docs_mod._is_archive("c.CHM")  # 大小写不敏感
    assert not docs_mod._is_archive("d.pdf")
