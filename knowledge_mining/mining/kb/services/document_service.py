"""Document (asset_documents identity) management.

KB 独占 asset_documents 写（身份 + 文件位置）。mining 读文档产 snapshot+知识（P4）。
状态读时派生（KbDB.derive_document_status），不存 status 列。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.errors import UniqueViolation

from knowledge_mining.mining.infra.archive_extractor import extract_archive
from knowledge_mining.mining.infra.upload_config import UploadConfig
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.kb_service import Forbidden, KbService, NotFound
from knowledge_mining.mining.kb.storage import build_document_key, build_storage_path


def _stat_meta(path: Path) -> tuple[int, str]:
    """返回 (字节数, ISO 修改时间)。文件管理器列表展示用。"""
    try:
        st = path.stat()
        return st.st_size, datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return 0, _utcnow_iso()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DocumentService:
    def __init__(self, db: KbDB, upload_root: Path | None = None) -> None:
        self._db = db
        self._svc = KbService(db)
        # 实例化时读 UploadConfig（OS env UPLOAD_ROOT 覆盖 .env）——测试可指向 tmp
        self._upload_root = Path(upload_root) if upload_root else UploadConfig().upload_root_path

    # ----------------------------------------------------- upload

    async def upload(
        self, *, kb_id: str, owner_id: str, filename: str, content: bytes,
        directory_path: str | None = None, document_type: str | None = None,
    ) -> dict[str, Any]:
        kb = await self._db.get_kb(kb_id)
        if kb is None:
            raise NotFound(kb_id)
        await self._svc._assert_write(kb_id, owner_id)  # IDOR 防护：写权限校验（admin/owner/editor）
        storage_path = build_storage_path(self._upload_root, kb_id, directory_path, filename)
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_bytes(content)
        size, mtime = _stat_meta(storage_path)
        doc = await self._db.insert_document_identity(
            domain=kb["domain"], kb_id=kb_id,
            document_key=build_document_key(directory_path, filename),
            document_name=Path(filename).name, storage_path=str(storage_path),
            directory_path=directory_path or "", document_type=document_type, owner_id=owner_id,
            file_size=size, modified_at=mtime,
        )
        doc["status"] = "uploaded"
        return doc

    async def upload_zip(
        self, *, kb_id: str, owner_id: str, zip_bytes: bytes, filename: str = "upload.zip",
    ) -> list[dict[str, Any]]:
        kb = await self._db.get_kb(kb_id)
        if kb is None:
            raise NotFound(kb_id)
        await self._svc._assert_write(kb_id, owner_id)  # IDOR 防护：写权限校验（admin/owner/editor）
        base = (self._upload_root / kb_id).resolve()
        base.mkdir(parents=True, exist_ok=True)
        zip_path = base / filename
        zip_path.write_bytes(zip_bytes)
        result = await asyncio.to_thread(extract_archive, zip_path, base)
        zip_path.unlink(missing_ok=True)
        if result.error:
            raise ValueError(f"archive extract failed: {result.error}")

        docs: list[dict[str, Any]] = []
        for rel in result.extracted_files:
            full = (base / rel).resolve()
            if not full.is_file():
                continue
            try:
                full.relative_to(base)
            except ValueError:
                continue  # 解压越界，跳过
            parts = Path(rel).parts
            directory_path = "/".join(parts[:-1])
            fn = parts[-1]
            try:
                size, mtime = _stat_meta(full)
                d = await self._db.insert_document_identity(
                    domain=kb["domain"], kb_id=kb_id,
                    document_key=build_document_key(directory_path, fn),
                    document_name=fn, storage_path=str(full),
                    directory_path=directory_path, owner_id=owner_id,
                    file_size=size, modified_at=mtime,
                )
                d["status"] = "uploaded"
                docs.append(d)
            except UniqueViolation:
                continue  # KB 内同名已存在，跳过
        return docs

    # ----------------------------------------------------- read / patch

    async def list_documents(
        self, *, kb_id: str, user_id: str, directory: str | None = None,
    ) -> list[dict[str, Any]]:
        await self._svc._assert_read(kb_id, user_id)
        # 状态由 list_documents_in_kb 内联派生（一条 SQL），不再 N+1。
        docs = await self._db.list_documents_in_kb(kb_id=kb_id, directory=directory)
        for d in docs:
            self._fill_meta(d)  # 旧文件 file_size 为空时本地 stat 补（本地磁盘，非远程查询）
        return docs

    def _fill_meta(self, doc: dict[str, Any]) -> None:
        """file_size 为空（旧文件 / 005 迁移前上传）时，从本地磁盘 stat 补大小+修改时间。

        纯本地 IO（微秒级），不触远程 DB；新上传已带 file_size，直接跳过。
        """
        if doc.get("file_size") is not None or not doc.get("storage_path"):
            return
        try:
            size, mtime = _stat_meta(Path(doc["storage_path"]))
            doc["file_size"] = size
            if not doc.get("modified_at"):
                doc["modified_at"] = mtime
        except OSError:
            pass

    async def get_document(self, *, document_id: str, user_id: str) -> dict[str, Any]:
        doc = await self._db.get_document_identity(document_id)
        if doc is None:
            raise NotFound(document_id)
        await self._svc._assert_read(doc["kb_id"], user_id)
        self._fill_meta(doc)
        return doc

    async def delete(self, *, document_id: str, user_id: str) -> None:
        """删除 KB 文档：删磁盘文件 + 身份行。

        FK CASCADE 清掉该文档的 snapshot_links 与 build_document_snapshots；
        共享 snapshot 保留（别的文档可能引用）。mining_run_documents 按 document_key
        留存为历史（无 FK，不阻塞），下次同 document_key 重传再挖会重新关联。
        """
        doc = await self._db.get_document_identity(document_id)
        if doc is None:
            raise NotFound(document_id)
        kb_id = doc["kb_id"]
        await self._svc._assert_write(kb_id, user_id)
        base = (self._upload_root / kb_id).resolve()
        sp = doc.get("storage_path")
        if sp:
            p = Path(sp)
            try:
                resolved = p.resolve()
                resolved.relative_to(base)  # 越界保护
                if resolved.is_file():
                    resolved.unlink()
            except (ValueError, OSError):
                pass  # 不在库内或已不存在：仍删 DB 行
        await self._db.delete_document_identity(document_id)

    async def patch_document(
        self, *, document_id: str, user_id: str,
        document_name: str | None = None, document_type: str | None = None,
    ) -> dict[str, Any]:
        doc = await self._db.get_document_identity(document_id)
        if doc is None:
            raise NotFound(document_id)
        await self._svc._assert_write(doc["kb_id"], user_id)
        return await self._db.update_document_identity(
            document_id, document_name=document_name, document_type=document_type,
        )

    async def download_path(self, *, document_id: str, user_id: str) -> Path:
        doc = await self._db.get_document_identity(document_id)
        if doc is None:
            raise NotFound(document_id)
        await self._svc._assert_read(doc["kb_id"], user_id)
        p = Path(doc["storage_path"])
        base = (self._upload_root / doc["kb_id"]).resolve()
        try:
            p.resolve().relative_to(base)
        except ValueError as exc:
            raise NotFound(document_id) from exc
        if not p.exists():
            raise NotFound(document_id)
        return p

    async def withdraw(self, *, document_id: str, user_id: str) -> None:
        """软撤回文档（clone build + publish_release，走 release 机制）。

        TODO(P4+): 复用 stages/withdrawal.withdraw_document。涉及 sync AssetCoreDB +
        advisory lock，待单独接（设计 §10 待定）。当前权限已校验，仅返回 NotImplemented。
        """
        doc = await self._db.get_document_identity(document_id)
        if doc is None:
            raise NotFound(document_id)
        await self._svc._assert_write(doc["kb_id"], user_id)
        raise NotImplementedError(
            "document withdraw pending release-machinery wiring (design §10)"
        )
