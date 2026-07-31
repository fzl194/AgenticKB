"""KB 文件夹管理 —— 一等文件夹（kb_folders）的磁盘 + DB 协调。

kb_folders 是文件夹结构的唯一真相源；磁盘目录与之镜像（建→mkdir，删空→rmdir）。
权限复用 KbService._assert_write/_assert_read。移动/改名同步 document_key 为新磁盘相对路径
（mining 按磁盘相对路径派生 key，否则状态派生 LATERAL 对不上 → 永远 uploaded）；id 不变。
mining 的 rglob 会自然 walk 出层级。
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from knowledge_mining.mining.infra.upload_config import UploadConfig
from knowledge_mining.mining.kb.storage import build_document_key
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.kb_service import Duplicate, KbService, NotFound
from knowledge_mining.mining.kb.storage import (
    build_folder_dir, join_path, normalize_folder_name,
)


def _parent_of(path: str) -> str:
    """'5G/AMF' → '5G'；'5G' → ''（顶层）。"""
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _is_within(child_path: str, ancestor_path: str) -> bool:
    """child_path 是否就是 ancestor_path 或位于其下。用于移动环检测。"""
    return child_path == ancestor_path or child_path.startswith(ancestor_path + "/")


class FolderService:
    def __init__(self, db: KbDB, upload_root: Path | None = None) -> None:
        self._db = db
        self._svc = KbService(db)
        self._upload_root = Path(upload_root) if upload_root else UploadConfig().upload_root_path

    async def list_folders(self, *, kb_id: str, user_id: str) -> list[dict[str, Any]]:
        await self._svc._assert_read(kb_id, user_id)
        return await self._db.list_folders(kb_id)

    async def get_folder(self, *, folder_id: str, user_id: str) -> dict[str, Any]:
        folder = await self._db.get_folder(folder_id)
        if folder is None:
            raise NotFound(folder_id)
        await self._svc._assert_read(folder["kb_id"], user_id)
        return folder

    async def create_folder(
        self, *, kb_id: str, parent_id: str | None, name: str, user_id: str,
    ) -> dict[str, Any]:
        kb = await self._db.get_kb(kb_id)
        if kb is None:
            raise NotFound(kb_id)
        await self._svc._assert_write(kb_id, user_id)
        name = normalize_folder_name(name)
        parent_path = ""
        if parent_id is not None:
            parent = await self._db.get_folder(parent_id)
            if parent is None or parent["kb_id"] != kb_id:
                raise NotFound(parent_id)
            parent_path = parent["path"]
        if await self._db.find_folder_by_parent(kb_id=kb_id, parent_id=parent_id, name=name):
            raise Duplicate(f"{kb_id}/{join_path(parent_path, name)}")
        path = join_path(parent_path, name)
        build_folder_dir(self._upload_root, kb_id, path).mkdir(parents=True, exist_ok=True)
        return await self._db.insert_folder(
            folder_id=uuid.uuid4().hex, kb_id=kb_id, parent_id=parent_id, name=name,
            path=path, created_by=user_id,
        )

    async def delete_folder(self, *, folder_id: str, user_id: str) -> None:
        """仅删空文件夹（无子文件夹、无文档）。非空 → ValueError（路由映射 409）。"""
        folder = await self._db.get_folder(folder_id)
        if folder is None:
            raise NotFound(folder_id)
        kb_id = folder["kb_id"]
        await self._svc._assert_write(kb_id, user_id)
        children = await self._db.count_child_folders(kb_id=kb_id, parent_id=folder_id)
        docs = await self._db.count_docs_under_path(kb_id=kb_id, path=folder["path"])
        if children or docs:
            raise ValueError(f"folder not empty: {children} subfolder(s), {docs} doc(s)")
        d = build_folder_dir(self._upload_root, kb_id, folder["path"])
        if d.is_dir():
            d.rmdir()  # 仅空目录可删；非空会 OSError（双保险）
        await self._db.delete_folder_row(folder_id)

    # ----------------------------------------------- move / rename (G3)

    def _kb_base(self, kb_id: str) -> Path:
        return (self._upload_root / kb_id).resolve()

    def _doc_storage(self, kb_id: str, directory_path: str, document_name: str) -> str:
        base = self._kb_base(kb_id)
        return str(base / directory_path / document_name) if directory_path else str(base / document_name)

    async def _relocate_docs(self, *, kb_id: str, old_prefix: str, new_prefix: str) -> None:
        """把身处 old_prefix（含子文件夹）下的文档 directory_path/storage_path/document_key 前缀替换。

        document_key 同步为新磁盘相对路径（mining 按磁盘相对路径派生 key）；id 不动。
        """
        for d in await self._db.list_docs_under_prefix(kb_id=kb_id, prefix=old_prefix):
            old_dir = d["directory_path"] or ""
            suffix = old_dir[len(old_prefix):]  # "" 或 "/sub"
            new_dir = new_prefix + suffix
            new_storage = self._doc_storage(kb_id, new_dir, d["document_name"])
            await self._db.update_doc_location(
                d["id"], directory_path=new_dir, storage_path=new_storage,
                document_key=build_document_key(new_dir, d["document_name"]),
            )

    async def rename_folder(self, *, folder_id: str, name: str, user_id: str) -> dict[str, Any]:
        folder = await self._db.get_folder(folder_id)
        if folder is None:
            raise NotFound(folder_id)
        kb_id = folder["kb_id"]
        await self._svc._assert_write(kb_id, user_id)
        name = normalize_folder_name(name)
        if name == folder["name"]:
            return folder
        if await self._db.find_folder_by_parent(kb_id=kb_id, parent_id=folder["parent_id"], name=name):
            raise Duplicate(f"{kb_id}/{join_path(_parent_of(folder['path']), name)}")
        old_path = folder["path"]
        new_path = join_path(_parent_of(old_path), name)
        base = self._kb_base(kb_id)
        shutil.move(str(base / old_path), str(base / new_path))
        try:
            await self._db.update_folder_name(folder_id, name)
            await self._db.rewrite_folder_subtree_paths(
                kb_id=kb_id, old_prefix=old_path, new_prefix=new_path)
            await self._relocate_docs(kb_id=kb_id, old_prefix=old_path, new_prefix=new_path)
        except Exception:
            shutil.move(str(base / new_path), str(base / old_path))  # 补偿回滚磁盘
            raise
        return await self._db.get_folder(folder_id)

    async def move_folder(
        self, *, folder_id: str, target_parent_id: str | None, user_id: str,
    ) -> dict[str, Any]:
        folder = await self._db.get_folder(folder_id)
        if folder is None:
            raise NotFound(folder_id)
        kb_id = folder["kb_id"]
        await self._svc._assert_write(kb_id, user_id)
        new_parent_path = ""
        if target_parent_id is not None:
            if target_parent_id == folder_id:
                raise ValueError("cannot move folder into itself")
            target = await self._db.get_folder(target_parent_id)
            if target is None or target["kb_id"] != kb_id:
                raise NotFound(target_parent_id)
            if _is_within(target["path"], folder["path"]):
                raise ValueError("cannot move folder into its own subtree")
            new_parent_path = target["path"]
        old_path = folder["path"]
        new_path = join_path(new_parent_path, folder["name"])
        if new_path == old_path:
            return folder
        if await self._db.find_folder_by_parent(
            kb_id=kb_id, parent_id=target_parent_id, name=folder["name"]
        ):
            raise Duplicate(f"{kb_id}/{new_path}")
        base = self._kb_base(kb_id)
        if new_parent_path:
            (base / new_parent_path).mkdir(parents=True, exist_ok=True)
        shutil.move(str(base / old_path), str(base / new_path))
        try:
            await self._db.set_folder_parent(folder_id, target_parent_id)
            await self._db.rewrite_folder_subtree_paths(
                kb_id=kb_id, old_prefix=old_path, new_prefix=new_path)
            await self._relocate_docs(kb_id=kb_id, old_prefix=old_path, new_prefix=new_path)
        except Exception:
            shutil.move(str(base / new_path), str(base / old_path))
            raise
        return await self._db.get_folder(folder_id)

    async def move_document(
        self, *, document_id: str, target_folder_id: str | None, user_id: str,
    ) -> dict[str, Any]:
        doc = await self._db.get_document_identity(document_id)
        if doc is None:
            raise NotFound(document_id)
        kb_id = doc["kb_id"]
        await self._svc._assert_write(kb_id, user_id)
        new_dir = ""
        if target_folder_id is not None:
            folder = await self._db.get_folder(target_folder_id)
            if folder is None or folder["kb_id"] != kb_id:
                raise NotFound(target_folder_id)
            new_dir = folder["path"]
        old_dir = doc["directory_path"] or ""
        if old_dir == new_dir:
            return doc
        old_storage = doc["storage_path"]
        new_storage = self._doc_storage(kb_id, new_dir, doc["document_name"])
        new_key = build_document_key(new_dir, doc["document_name"])
        base = self._kb_base(kb_id)
        if new_dir:
            (base / new_dir).mkdir(parents=True, exist_ok=True)
        shutil.move(old_storage, new_storage)
        try:
            await self._db.update_doc_location(
                document_id, directory_path=new_dir, storage_path=new_storage, document_key=new_key)
        except Exception:
            shutil.move(new_storage, old_storage)
            raise
        return await self._db.get_document_identity(document_id)
