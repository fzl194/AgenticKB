# -*- coding: utf-8 -*-
"""导入编排（47 号 §四-4/§四-6）：公共库 bootstrap + 导入任务状态机.

状态机：queued → fetching → restoring → importing → mining → done / failed。
幂等：拉取段文件存在即跳过；document_key 已存在即复用（重试不重建行）。
自动挖掘：复用 ``auto_mine.enqueue_auto_mining``（排队语义，失败只降级）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from knowledge_mining.mining.onenet.fetch import Selection, fetch_selection, load_slices
from knowledge_mining.mining.onenet.restore import RULE_VERSION, restore_files
from knowledge_mining.mining.parse_adapters.onenet_jsonl import ONENET_JSONL_MIME

logger = logging.getLogger(__name__)

#: 域公共库固定名（47 号：UNIQUE 域约束即 bootstrap 幂等守卫）。
PUBLIC_KB_NAME = "一张网产品文档"
PUBLIC_KB_KIND = "onenet"

#: 文档元数据映射的一期字段（47 号 §七：导入即用清单）。
_DOC_META_FIELDS = (
    "url", "public_level", "parsed_version", "language", "publish_time",
    "doc_name", "file_name", "product", "product_family", "product_line",
    "product_series", "product_category", "product_version", "pbi",
    "category", "sub_category",
)

#: onenet_raw 兜底排除的大字段（对象/数组资产，不入 metadata_json）。
_RAW_EXCLUDE = frozenset({
    "content", "table", "media", "sample_slices", "path", "title",
    "nid", "id", "part_id", "source_id",
})

_FILENAME_SANITIZE_RE = re.compile(r'[\\/:*?"<>|\r\n\t]')
_MAX_FILENAME_LEN = 80

# 后台任务强引用（事件循环只留弱引用会被 GC——同 archive_tasks 模式）
_bg_tasks: set[asyncio.Task] = set()


class OnenetImportError(RuntimeError):
    """导入编排错误（带机器可读 reason）。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_close(client: Any) -> None:
    """关闭客户端但不让关闭失败掩盖真实错误（fake/测试客户端可无 close）。"""
    try:
        client.close()
    except Exception:  # noqa: BLE001
        logger.debug("[onenet] client close failed", exc_info=True)


def document_key_for(source_id: str, file_path: str) -> str:
    """稳定 document_key：onenet:{source_id}:{path sha1[:16]}（47 号 §四-4）."""
    digest = hashlib.sha1(file_path.encode("utf-8")).hexdigest()[:16]
    return f"onenet:{source_id}:{digest}"


def sanitize_filename(name: str, *, max_len: int = _MAX_FILENAME_LEN) -> str:
    """文件名安全化（保留可读性；超长截断）."""
    cleaned = _FILENAME_SANITIZE_RE.sub("_", (name or "").strip()).strip(". ")
    return (cleaned or "untitled")[:max_len]


class OnenetRepo:
    """onenet_imports / onenet_toc_cache 的仓储（与 KbDB 同池）."""

    def __init__(self, pool: Any):
        self._pool = pool

    async def _conn(self):
        return self._pool.connection()

    # --------------------------------------------------------- imports CRUD

    async def insert_import(
        self, *, domain: str, source_id: str, selection: Selection,
        kb_id: str, created_by: str, doc_name: str | None,
        parsed_version: str | None, total_slices: int | None,
    ) -> dict[str, Any]:
        now = _utcnow()
        row = {
            "id": uuid.uuid4().hex, "domain": domain, "source_id": source_id,
            "doc_name": doc_name, "parsed_version_seen": parsed_version,
            "total_slices": total_slices, "selection_json": selection.to_dict(),
            "status": "queued", "kb_id": kb_id, "document_count": None,
            "error": None, "created_by": created_by,
            "created_at": now, "updated_at": now,
        }
        async with self._conn() as conn:
            cur = await conn.execute(
                """INSERT INTO onenet_imports
                     (id, domain, source_id, doc_name, parsed_version_seen,
                      total_slices, selection_json, status, kb_id, error,
                      created_by, created_at, updated_at)
                   VALUES (%(id)s, %(domain)s, %(source_id)s, %(doc_name)s,
                           %(parsed_version_seen)s, %(total_slices)s,
                           %(selection_json)s::jsonb, %(status)s, %(kb_id)s,
                           %(error)s, %(created_by)s, %(created_at)s, %(updated_at)s)
                   RETURNING *""",
                {**row, "selection_json": json.dumps(
                    row["selection_json"], ensure_ascii=False)},
            )
            return dict(await cur.fetchone())

    async def get_import(self, import_id: str) -> dict[str, Any] | None:
        async with self._conn() as conn:
            cur = await conn.execute(
                "SELECT * FROM onenet_imports WHERE id = %s", [import_id])
            r = await cur.fetchone()
            return dict(r) if r else None

    async def find_import_by_source(
        self, domain: str, source_id: str,
    ) -> dict[str, Any] | None:
        async with self._conn() as conn:
            cur = await conn.execute(
                "SELECT * FROM onenet_imports WHERE domain = %s AND source_id = %s",
                [domain, source_id])
            r = await cur.fetchone()
            return dict(r) if r else None

    async def list_imports(self, *, domain: str) -> list[dict[str, Any]]:
        async with self._conn() as conn:
            cur = await conn.execute(
                "SELECT * FROM onenet_imports WHERE domain = %s ORDER BY created_at DESC",
                [domain])
            return [dict(r) for r in await cur.fetchall()]

    async def update_import(self, import_id: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = %({k})s" for k in fields)
        params = {**fields, "updated_at": _utcnow(), "id": import_id}
        async with self._conn() as conn:
            await conn.execute(
                f"UPDATE onenet_imports SET {sets}, updated_at = %(updated_at)s "
                "WHERE id = %(id)s", params)

    # --------------------------------------------------------- 公共库

    async def find_public_kb(self, domain: str) -> dict[str, Any] | None:
        async with self._conn() as conn:
            cur = await conn.execute(
                """SELECT * FROM knowledge_bases
                   WHERE domain = %s AND name = %s AND status = 'active'""",
                [domain, PUBLIC_KB_NAME])
            row = await cur.fetchone()
            return dict(row) if row else None

    # --------------------------------------------------------- toc cache

    async def get_toc_cache(self, domain: str, source_id: str) -> dict[str, Any] | None:
        async with self._conn() as conn:
            cur = await conn.execute(
                "SELECT * FROM onenet_toc_cache WHERE domain = %s AND source_id = %s",
                [domain, source_id])
            r = await cur.fetchone()
            return dict(r) if r else None

    async def put_toc_cache(
        self, *, domain: str, source_id: str, parsed_version: str | None,
        toc: dict[str, Any],
    ) -> None:
        async with self._conn() as conn:
            await conn.execute(
                """INSERT INTO onenet_toc_cache
                     (domain, source_id, parsed_version_seen, toc_json, scanned_at)
                   VALUES (%s, %s, %s, %s::jsonb, %s)
                   ON CONFLICT (domain, source_id) DO UPDATE SET
                     parsed_version_seen = EXCLUDED.parsed_version_seen,
                     toc_json = EXCLUDED.toc_json,
                     scanned_at = EXCLUDED.scanned_at""",
                [domain, source_id, parsed_version,
                 json.dumps(toc, ensure_ascii=False), _utcnow()])


class OnenetImportService:
    """导入编排服务（依赖全部注入可测）."""

    def __init__(
        self, *,
        repo: OnenetRepo,
        kbdb: Any,
        kb_service: Any,
        doc_service: Any,
        folder_service: Any,
        client_factory: Callable[[], Any],
        workspace_root: Path,
        auto_miner: Callable[..., Any] | None = None,
    ):
        self._repo = repo
        self._kbdb = kbdb
        self._kb_service = kb_service
        self._doc_service = doc_service
        self._folders = folder_service
        self._client_factory = client_factory
        self._workspace_root = Path(workspace_root)
        self._auto_miner = auto_miner

    # ------------------------------------------------------------ bootstrap

    async def ensure_public_kb(self, *, domain: str, actor_id: str) -> dict[str, Any]:
        """域公共库幂等 bootstrap（固定名 + kind=onenet + 默认范式）."""
        existing = await self._repo.find_public_kb(domain)
        if existing is not None:
            return existing
        try:
            return await self._kb_service.create_kb(
                domain=domain, name=PUBLIC_KB_NAME, owner_id=actor_id,
                visibility="private",
                description="知识一张网产品文档公共库（管理员维护，业务库按章节引用）",
                metadata={"kind": PUBLIC_KB_KIND},
            )
        except Exception:
            # 并发首建撞唯一约束 → 重查复用（UNIQUE 守卫兜底）
            existing = await self._repo.find_public_kb(domain)
            if existing is not None:
                return existing
            raise

    # ------------------------------------------------------------ 启动导入

    async def start_import(
        self, *, domain: str, source_id: str, selection: Selection,
        actor_id: str, username: str,
        doc_name: str | None = None, parsed_version: str | None = None,
        total_slices: int | None = None,
    ) -> dict[str, Any]:
        """创建导入记录并后台执行（重复 source → OnenetImportError duplicate）."""
        if await self._repo.find_import_by_source(domain, source_id) is not None:
            raise OnenetImportError(
                f"duplicate: {domain}/{source_id} 已有导入记录，更新请走重同步")
        kb = await self.ensure_public_kb(domain=domain, actor_id=actor_id)
        record = await self._repo.insert_import(
            domain=domain, source_id=source_id, selection=selection,
            kb_id=kb["id"], created_by=actor_id, doc_name=doc_name,
            parsed_version=parsed_version, total_slices=total_slices,
        )
        task = asyncio.create_task(self._run_import(record["id"]))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
        return record

    # ------------------------------------------------------------ 状态机

    async def _run_import(self, import_id: str) -> None:
        record = await self._repo.get_import(import_id)
        if record is None:
            return
        domain = record["domain"]
        source_id = record["source_id"]
        kb_id = record["kb_id"]
        selection = Selection.from_dict(record.get("selection_json") or {})
        workspace = self._workspace_root / domain / source_id
        try:
            # ---- fetching（安全审查 H-2：分钟级网络 IO 下放线程池，事件循环不被占死）
            await self._repo.update_import(import_id, status="fetching", error=None)
            client = self._client_factory()
            try:
                outcome = await asyncio.to_thread(
                    fetch_selection, client, source_id, selection, workspace)
            finally:
                _safe_close(client)
            slices = await asyncio.to_thread(load_slices, outcome.slices_path)
            fetched_max = outcome.manifest.get("fetched_max_part_id")

            # ---- restoring
            await self._repo.update_import(import_id, status="restoring")
            result = await asyncio.to_thread(restore_files, slices)

            # ---- importing
            await self._repo.update_import(import_id, status="importing")
            doc_ids = [
                await self._import_file(
                    kb_id=kb_id, domain=domain, source_id=source_id,
                    restored=f, actor_id=record["created_by"],
                )
                for f in result.files
            ]

            # ---- mining（排队语义；失败只降级为提示，不置 failed）
            auto_note = None
            if self._auto_miner is not None:
                kb = await self._kbdb.get_kb(kb_id)
                auto_note = await self._auto_miner(kb=kb, user_id=record["created_by"])
            await self._repo.update_import(
                import_id, status="done",
                document_count=len(result.files),
                fetched_max_part_id=fetched_max,
                doc_name=(slices[0].get("doc_name") if slices else record.get("doc_name")),
                parsed_version_seen=(
                    slices[0].get("parsed_version") if slices
                    else record.get("parsed_version_seen")),
                error=None,
            )
            logger.info("[onenet] import done: %s docs=%s auto=%s",
                        import_id, doc_ids and len(doc_ids), auto_note)
        except Exception as e:  # noqa: BLE001 - 状态机兜底：真实原因入 error
            logger.exception("[onenet] import failed: %s", import_id)
            try:
                await self._repo.update_import(
                    import_id, status="failed", error=str(e)[:2000])
            except Exception:
                logger.exception("[onenet] 标记失败状态也失败: %s", import_id)

    # ------------------------------------------------------------ 文件落库

    async def _import_file(
        self, *, kb_id: str, domain: str, source_id: str,
        restored: Any, actor_id: str,
    ) -> str:
        """一个还原文件 → 对象 + Document（document_key 幂等）."""
        key = document_key_for(source_id, restored.file_path)
        existing = await self._kbdb.find_document_by_key(
            kb_id, key, include_deleted=True)
        if existing is not None:
            if existing.get("deleted_at") is None:
                return str(existing["id"])  # 幂等复用（重试/重放）
            raise OnenetImportError(
                f"document_key 已被软删文档持有（先恢复或清理再导入）: {key}")

        # 目录链（文件之上层级）
        if restored.folder_path:
            await self._folders.ensure_folder_path(
                kb_id=kb_id, path=restored.folder_path, user_id=actor_id)

        filename = await self._dedupe_filename(
            kb_id=kb_id, directory=restored.folder_path or None,
            base=sanitize_filename(restored.file_title), part_min=restored.part_min)
        payload = _jsonl_bytes(restored.slices)
        storage_object = await self._doc_service.store_source_bytes(
            payload, mime=ONENET_JSONL_MIME)

        meta = _document_metadata(source_id, restored, restored.slices)
        doc = await self._kbdb.insert_document_from_storage(
            domain=domain, kb_id=kb_id, document_key=key,
            document_name=filename,
            storage_object_id=storage_object.id,
            source_raw_hash=storage_object.sha256,
            directory_path=restored.folder_path or None,
            document_type="reference",
            owner_id=actor_id, file_size=storage_object.size,
            modified_at=_utcnow(), metadata=meta,
        )
        return str(doc["id"])

    async def _dedupe_filename(
        self, *, kb_id: str, directory: str | None, base: str, part_min: int,
    ) -> str:
        """同目录同名（不同 file_path 同末段名）→ 追加 part 锚，保持确定性."""
        candidate = f"{base}.jsonl"
        taken = await self._kbdb.find_document_by_location(
            kb_id, directory, candidate, include_deleted=True)
        if taken is None:
            return candidate
        return f"{base}__p{part_min}.jsonl"


def _jsonl_bytes(slices: tuple[dict, ...]) -> bytes:
    return ("\n".join(json.dumps(s, ensure_ascii=False) for s in slices)
            + "\n").encode("utf-8")


def _document_metadata(
    source_id: str, restored: Any, slices: tuple[dict, ...],
) -> dict[str, Any]:
    """文档级元数据（47 号 §七导入即用字段 + 未映射兜底 onenet_raw）."""
    first = slices[0] if slices else {}
    mapped = {k: first.get(k) for k in _DOC_META_FIELDS if first.get(k) is not None}
    raw = {
        k: first.get(k) for k in first
        if k not in _DOC_META_FIELDS and k not in _RAW_EXCLUDE
        and first.get(k) not in (None, [], "")
    }
    return {
        "source_system": PUBLIC_KB_KIND,
        "source_id": source_id,
        "logical": True,
        "file_path": restored.file_path,
        "rule_version": RULE_VERSION,
        "part_min": restored.part_min,
        "part_max": restored.part_max,
        "slice_count": len(slices),
        **({"onenet": mapped} if mapped else {}),
        **({"onenet_raw": raw} if raw else {}),
    }


__all__ = [
    "PUBLIC_KB_KIND", "PUBLIC_KB_NAME", "OnenetImportError", "OnenetImportService",
    "OnenetRepo", "document_key_for", "sanitize_filename",
]
