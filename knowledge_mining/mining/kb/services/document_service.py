"""Document (asset_documents identity) management.

KB 独占 asset_documents 写（身份 + 文件位置）。mining 读文档产 snapshot+知识（P4）。
状态读时派生（KbDB.derive_document_status），不存 status 列。
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from psycopg.errors import UniqueViolation

from knowledge_mining.mining.infra.archive_extractor import extract_archive, extract_zip
from knowledge_mining.mining.infra.upload_config import UploadConfig
from knowledge_mining.mining.contracts.file_management import (
    StorageObjectRecord,
    StorageObjectRepository,
)
from knowledge_mining.mining.contracts.storage.port import ObjectStorePort
from knowledge_mining.mining.contracts.storage.types import ObjectLocation, PutOptions
from knowledge_mining.mining.infra.object_store.keys import build_object_key
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.folder_service import FolderService
from knowledge_mining.mining.kb.services.kb_service import Forbidden, KbService, NotFound
from knowledge_mining.mining.kb.storage import build_document_key, build_storage_path


def _stat_meta(path: Path) -> tuple[int, str]:
    """返回 (字节数, ISO 修改时间)。文件管理器列表展示用。"""
    try:
        st = path.stat()
        return st.st_size, datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return 0, _utcnow_iso()


#: 扩展名 → MIME 回落表。浏览器对 .md 常给 application/octet-stream（或空），
#: 盲信 multipart content_type 会把 mime 记错导致解析链拒收（BUG-2，批次1）。
_EXTENSION_MIME: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".csv": "text/csv",
    ".json": "application/json",
}

_GENERIC_MIME = {"", "application/octet-stream"}


def resolve_upload_mime(filename: str, declared: str | None) -> str:
    """声明 MIME 缺失或为泛型时按扩展名回落；具体声明（含非匹配扩展名）以声明为准。"""
    declared = (declared or "").strip().lower()
    if declared not in _GENERIC_MIME:
        return declared
    return _EXTENSION_MIME.get(Path(filename).suffix.lower(), "application/octet-stream")


class UploadTooLarge(Exception):
    """上传超过大小上限（路由映射 413）。"""

    def __init__(self, message: str, *, limit_bytes: int):
        super().__init__(message)
        self.limit_bytes = limit_bytes


_SPILL_CHUNK = 256 * 1024


def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    """从磁盘文件分块读出（异步生成器）。"""
    async def _gen() -> AsyncIterator[bytes]:
        with path.open("rb") as fh:
            while True:
                block = fh.read(_SPILL_CHUNK)
                if not block:
                    break
                yield block
    return _gen()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_directory(directory_path: str | None) -> str:
    """Normalize a user directory path after the traversal guard has run."""
    return "/".join(
        part for part in (directory_path or "").split("/") if part not in ("", ".")
    )


async def _bytes_stream(content: bytes) -> AsyncIterator[bytes]:
    yield content


class DocumentService:
    def __init__(
        self,
        db: KbDB,
        upload_root: Path | None = None,
        *,
        object_store: ObjectStorePort | None = None,
        storage_objects: StorageObjectRepository | None = None,
        source_bucket: str | None = None,
    ) -> None:
        self._db = db
        self._svc = KbService(db)
        # 实例化时读 UploadConfig（OS env UPLOAD_ROOT 覆盖 .env）——测试可指向 tmp
        self._upload_root = Path(upload_root) if upload_root else UploadConfig().upload_root_path
        self._folders = FolderService(db, self._upload_root)
        self._object_store = object_store
        self._storage_objects = storage_objects
        self._source_bucket = source_bucket

    # ----------------------------------------------------- upload

    async def upload(
        self, *, kb_id: str, owner_id: str, filename: str, content: bytes,
        directory_path: str | None = None, document_type: str | None = None,
        mime: str | None = None,
    ) -> dict[str, Any]:
        kb = await self._db.get_kb(kb_id)
        if kb is None:
            raise NotFound(kb_id)
        await self._svc._assert_write(kb_id, owner_id)  # IDOR 防护：写权限校验（admin/owner/editor）
        # Preserve the existing traversal validation without writing bytes to
        # the legacy upload root.  Object keys are content-addressed and never
        # derive from user-provided names or directory paths.
        build_storage_path(self._upload_root, kb_id, directory_path, filename)
        normalized_directory = _normalize_directory(directory_path)
        normalized_filename = Path(filename).name
        storage_object = await self._store_source(
            content=content, mime=resolve_upload_mime(filename, mime),
        )
        document_key = build_document_key(normalized_directory, normalized_filename)
        # P08-S1：软删行仍占 uq_asset_documents_kb_key——同名重传复活身份行
        #（指针/哈希/revision 前移），而不是 409 或插入第二行。
        soft_deleted = await self._db.find_document_by_key(
            kb_id, document_key, include_deleted=True,
        )
        if soft_deleted is not None and soft_deleted.get("deleted_at") is not None:
            revived = await self._db.revive_document_from_storage(
                soft_deleted["id"],
                storage_object_id=storage_object.id,
                source_raw_hash=storage_object.sha256,
                file_size=storage_object.size, modified_at=_utcnow_iso(),
            )
            if revived is not None:
                revived["status"] = "uploaded"
                return revived
        doc = await self._db.insert_document_from_storage(
            domain=kb["domain"], kb_id=kb_id,
            document_key=document_key,
            document_name=normalized_filename,
            storage_object_id=storage_object.id,
            source_raw_hash=storage_object.sha256,
            directory_path=normalized_directory, document_type=document_type,
            owner_id=owner_id, file_size=storage_object.size, modified_at=_utcnow_iso(),
        )
        doc["status"] = "uploaded"
        return doc

    async def upload_stream(
        self, *, kb_id: str, owner_id: str, filename: str,
        stream: AsyncIterator[bytes],
        mime: str | None = None, directory_path: str | None = None,
        document_type: str | None = None, max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """流式上传（P01-S1）：路由分块喂入，进程内存与文件大小无关。

        ``max_bytes`` 上限在落盘暂存阶段强制（超限 UploadTooLarge → 路由 413），
        不会触碰对象存储或文档表。
        """
        kb = await self._db.get_kb(kb_id)
        if kb is None:
            raise NotFound(kb_id)
        await self._svc._assert_write(kb_id, owner_id)
        build_storage_path(self._upload_root, kb_id, directory_path, filename)
        normalized_directory = _normalize_directory(directory_path)
        normalized_filename = Path(filename).name
        storage_object = await self._store_source_stream(
            stream, mime=resolve_upload_mime(filename, mime), max_bytes=max_bytes,
        )
        return await self._register_uploaded_document(
            kb=kb, kb_id=kb_id, owner_id=owner_id,
            normalized_directory=normalized_directory,
            normalized_filename=normalized_filename,
            storage_object=storage_object, document_type=document_type,
        )

    async def _register_uploaded_document(
        self, *, kb: dict[str, Any], kb_id: str, owner_id: str,
        normalized_directory: str, normalized_filename: str,
        storage_object: StorageObjectRecord, document_type: str | None,
    ) -> dict[str, Any]:
        """内容入库后的文档登记（含软删同名复活，P08-S1）。单文件/zip 共用。"""
        document_key = build_document_key(normalized_directory, normalized_filename)
        soft_deleted = await self._db.find_document_by_key(
            kb_id, document_key, include_deleted=True,
        )
        if soft_deleted is not None and soft_deleted.get("deleted_at") is not None:
            revived = await self._db.revive_document_from_storage(
                soft_deleted["id"],
                storage_object_id=storage_object.id,
                source_raw_hash=storage_object.sha256,
                file_size=storage_object.size, modified_at=_utcnow_iso(),
            )
            if revived is not None:
                revived["status"] = "uploaded"
                return revived
        doc = await self._db.insert_document_from_storage(
            domain=kb["domain"], kb_id=kb_id,
            document_key=document_key,
            document_name=normalized_filename,
            storage_object_id=storage_object.id,
            source_raw_hash=storage_object.sha256,
            directory_path=normalized_directory, document_type=document_type,
            owner_id=owner_id, file_size=storage_object.size, modified_at=_utcnow_iso(),
        )
        doc["status"] = "uploaded"
        return doc

    async def _store_source_stream(
        self, stream: AsyncIterator[bytes], *, mime: str | None,
        max_bytes: int | None = None,
    ) -> StorageObjectRecord:
        """流式落 source 对象：分块暂存 + 增量 sha256 → 去重检查 → 流式上传。

        内容寻址要求先有哈希才知道 object_key——单遍暂存（磁盘）+ 第二遍
        从暂存文件流式 put_stream，全程不在内存持有整包。
        """
        if self._object_store is None or self._storage_objects is None or not self._source_bucket:
            raise RuntimeError("KB object storage is not configured")
        sha = hashlib.sha256()
        size = 0
        with TemporaryDirectory(prefix="agentickb-upload-") as tmp:
            staging = Path(tmp) / "staged.bin"
            with staging.open("wb") as fh:
                async for chunk in stream:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise UploadTooLarge(
                            f"upload exceeds limit: {size} > {max_bytes} bytes",
                            limit_bytes=max_bytes,
                        )
                    fh.write(chunk)
                    sha.update(chunk)
            digest = sha.hexdigest()
            object_key = build_object_key("source", digest)
            existing = await self._storage_objects.find_by_location(
                self._source_bucket, object_key, None,
            )
            if existing is not None:
                if (existing.sha256 != digest or existing.size != size
                        or existing.state != "AVAILABLE"):
                    raise ValueError("registered storage object does not match upload content")
                return existing
            put_result = await self._object_store.put_stream(
                ObjectLocation(bucket=self._source_bucket, object_key=object_key),
                _file_chunks(staging),
                PutOptions(
                    artifact_class="source", mime=mime, expected_sha256=digest,
                    content_length=size,
                ),
            )
            if put_result.sha256 != digest or put_result.size != size:
                raise ValueError("object storage verification failed")
        return await self._register_source_object(
            object_key=object_key, sha256=digest, size=size,
            mime=mime, etag=put_result.etag,
        )

    async def _register_source_object(
        self, *, object_key: str, sha256: str, size: int,
        mime: str | None, etag: str,
    ) -> StorageObjectRecord:
        registered = await self._storage_objects.register(
            StorageObjectRecord(
                id=f"obj_{uuid.uuid4().hex}",
                provider=getattr(self._object_store, "provider", "unknown"),
                bucket=self._source_bucket, object_key=object_key,
                object_version_id=None, sha256=sha256,
                size=size, mime=mime, etag=etag,
                artifact_class="source", state="AVAILABLE", created_at=_utcnow_iso(),
                last_verified_at=_utcnow_iso(),
            )
        )
        if (registered.sha256 != sha256 or registered.size != size
                or registered.state != "AVAILABLE"):
            raise ValueError("registered storage object does not match upload content")
        return registered

    async def _store_source(
        self, *, content: bytes, mime: str | None,
    ) -> StorageObjectRecord:
        """Put bytes at their immutable content-addressed location and register them.

        The object record is only created after ``put_stream`` has verified the
        expected checksum.  A duplicate upload reuses the existing registry
        record, so no document can point at an unregistered local file.
        """
        if self._object_store is None or self._storage_objects is None or not self._source_bucket:
            raise RuntimeError("KB object storage is not configured")
        sha256 = hashlib.sha256(content).hexdigest()
        object_key = build_object_key("source", sha256)
        existing = await self._storage_objects.find_by_location(
            self._source_bucket, object_key, None,
        )
        if existing is not None:
            if (
                existing.sha256 != sha256
                or existing.size != len(content)
                or existing.state != "AVAILABLE"
            ):
                raise ValueError("registered storage object does not match upload content")
            return existing
        put_result = await self._object_store.put_stream(
            ObjectLocation(bucket=self._source_bucket, object_key=object_key),
            _bytes_stream(content),
            PutOptions(
                artifact_class="source", mime=mime, expected_sha256=sha256,
                content_length=len(content),
            ),
        )
        if put_result.sha256 != sha256 or put_result.size != len(content):
            raise ValueError("object storage verification failed")
        registered = await self._storage_objects.register(
            StorageObjectRecord(
                id=f"obj_{uuid.uuid4().hex}",
                provider=getattr(self._object_store, "provider", "unknown"),
                bucket=self._source_bucket, object_key=object_key,
                # The content-addressed source location is the immutable
                # identity; do not make dedup depend on optional S3 versioning.
                object_version_id=None, sha256=sha256,
                size=put_result.size, mime=mime, etag=put_result.etag,
                artifact_class="source", state="AVAILABLE", created_at=_utcnow_iso(),
                last_verified_at=_utcnow_iso(),
            )
        )
        if (
            registered.sha256 != sha256
            or registered.size != len(content)
            or registered.state != "AVAILABLE"
        ):
            raise ValueError("registered storage object does not match upload content")
        return registered

    async def upload_zip(
        self, *, kb_id: str, owner_id: str, zip_bytes: bytes, filename: str = "upload.zip",
    ) -> list[dict[str, Any]]:
        """旧签名（整包 bytes）薄包装——生产路由已改走 upload_zip_path。"""
        with TemporaryDirectory(prefix="agentickb-kb-upload-") as temporary_dir:
            zip_path = Path(temporary_dir) / Path(filename).name
            zip_path.write_bytes(zip_bytes)
            return await self.upload_zip_path(
                kb_id=kb_id, owner_id=owner_id, zip_path=zip_path,
            )

    async def upload_zip_path(
        self, *, kb_id: str, owner_id: str, zip_path: Path,
        max_archive_bytes: int | None = None,
        max_member_bytes: int | None = None,
        max_members: int | None = None,
    ) -> list[dict[str, Any]]:
        """zip 上传（P01-S1 流式）：磁盘 zip → 带额度解压 → 成员流式入库。

        解压三重额度（成员数/单成员/总量，zip-bomb 防护）；默认上限来自
        UploadConfig（archive 500MB / file 100MB / 100 成员）。
        """
        cfg = UploadConfig()
        if max_archive_bytes is None:
            max_archive_bytes = cfg.upload_max_archive_size
        if max_member_bytes is None:
            max_member_bytes = cfg.upload_max_file_size
        if max_members is None:
            max_members = cfg.upload_max_files_per_request

        kb = await self._db.get_kb(kb_id)
        if kb is None:
            raise NotFound(kb_id)
        await self._svc._assert_write(kb_id, owner_id)  # IDOR 防护：写权限校验（admin/owner/editor）
        # Archive extraction needs a filesystem, but it is only a transient
        # scratch space.  No extracted path is persisted in asset_documents.
        with TemporaryDirectory(prefix="agentickb-kb-upload-") as temporary_dir:
            base = Path(temporary_dir)
            result = await asyncio.to_thread(
                extract_zip, zip_path, base,
                max_members=max_members,
                max_member_bytes=max_member_bytes,
                max_total_bytes=max_archive_bytes,
            )
            if result.error:
                raise ValueError(result.error)

            # 先幂等建齐 zip 内子目录对应的 kb_folders（否则 UI 按 folder.path 过滤 → 列表空）
            dir_paths = {
                "/".join(Path(rel).parts[:-1])
                for rel in result.extracted_files
                if len(Path(rel).parts) > 1
            }
            for dp in sorted(dir_paths, key=lambda p: p.count("/")):
                await self._folders.ensure_folder_path(kb_id=kb_id, path=dp, user_id=owner_id)

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
                try:
                    docs.append(await self.upload_stream(
                        kb_id=kb_id, owner_id=owner_id, filename=parts[-1],
                        stream=_file_chunks(full),
                        directory_path="/".join(parts[:-1]),
                        max_bytes=max_member_bytes,
                    ))
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
        """软删 KB 文档（P08-S1）：盖 deleted_at，不触 FK CASCADE。

        硬删会借 CASCADE 清掉 asset_document_snapshot_links 与
        asset_build_document_snapshots——**改写历史 Build**（P0 事故面）。
        软删后：读面/挖掘取数/Java 检索按 deleted_at IS NULL 过滤退出；
        restore 可恢复；同 document_key 重传会复活该身份行。
        legacy 磁盘分支（storage_path 非 NULL 的老文档）仍清磁盘文件。
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
                pass  # 不在库内或已不存在：仍软删 DB 行
        await self._db.soft_delete_document(document_id)

    async def restore(self, *, document_id: str, user_id: str) -> dict[str, Any]:
        """恢复软删文档：身份行与对象指针原样回来。"""
        doc = await self._db.get_document_identity(document_id, include_deleted=True)
        if doc is None:
            raise NotFound(document_id)
        await self._svc._assert_write(doc["kb_id"], user_id)
        if doc.get("deleted_at") is None:
            return doc  # 幂等：本来就没删
        await self._db.clear_document_deleted(document_id)
        restored = await self._db.get_document_identity(document_id)
        assert restored is not None
        return restored

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

    async def download_object(
        self, *, document_id: str, user_id: str
    ) -> tuple[str, str | None, Any] | None:
        """对象存储文档下载：返回 (文件名, mime, 字节流迭代器)。

        无 ``storage_object_id``（legacy 本地文档）返回 None，调用方回落
        ``download_path`` 的本地文件路径。
        """
        doc = await self._db.get_document_identity(document_id)
        if doc is None:
            raise NotFound(document_id)
        await self._svc._assert_read(doc["kb_id"], user_id)
        if not doc.get("storage_object_id"):
            return None
        if self._object_store is None or self._storage_objects is None:
            raise RuntimeError("KB object storage is not configured")
        record = await self._storage_objects.get(doc["storage_object_id"])
        if record is None or record.state != "AVAILABLE":
            raise NotFound(document_id)
        from knowledge_mining.mining.contracts.storage.types import (
            ObjectLocation,
        )

        stream = self._object_store.get_stream(ObjectLocation(
            bucket=record.bucket, object_key=record.object_key,
            version_id=record.object_version_id,
        ))
        return doc.get("document_name") or "document", record.mime, stream

    async def presign_document(
        self, *, document_id: str, user_id: str, expires_in: int = 600
    ) -> tuple[str, str | None, str] | None:
        """对象文档预签名 URL：浏览器直连 MinIO 按需（Range）加载。

        大文件（如十几 MB 的 PDF）在线预览若经"后端全量转发→前端 Blob"
        两跳全量下载，首屏极卡；预签名 URL 让 iframe/img 直接指向对象
        存储浏览器自带的分页按需加载（首屏只拉第一页字节）。鉴权在本
        方法（KB 成员可见性），URL 本身短时效（默认 10 分钟）自失效。
        无 ``storage_object_id``（legacy 本地文档）返回 None 走回落。
        """
        doc = await self._db.get_document_identity(document_id)
        if doc is None:
            raise NotFound(document_id)
        await self._svc._assert_read(doc["kb_id"], user_id)
        if not doc.get("storage_object_id"):
            return None
        if self._object_store is None or self._storage_objects is None:
            raise RuntimeError("KB object storage is not configured")
        record = await self._storage_objects.get(doc["storage_object_id"])
        if record is None or record.state != "AVAILABLE":
            raise NotFound(document_id)
        from knowledge_mining.mining.contracts.storage.types import (
            ObjectLocation,
        )

        access = await self._object_store.presign_get(
            ObjectLocation(
                bucket=record.bucket, object_key=record.object_key,
                version_id=record.object_version_id,
            ),
            expires_in_seconds=expires_in,
        )
        name = doc.get("document_name") or "document"
        return name, record.mime, access.url

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
