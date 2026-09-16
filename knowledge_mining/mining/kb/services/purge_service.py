# -*- coding: utf-8 -*-
"""统一硬删管线（2026-09-16 删除体系定稿：软删退役）.

三入口（整库 / 文件夹级联 / 文档批量）共用 ``purge_documents`` 一条管线；
一张网 source 删除 = N 个文档批量走同一管线。共享资产（快照、MinIO 对象）
先引用清点、归零才动——隔壁库一根毫毛不少。

依赖序（FK 反向）：
  引用行(kb_document_refs) → 挖掘历史轨(run_documents/parse_runs/attempts)
  → 上传会话/审计 → 文档行(CASCADE 带走 links+build 选片)
  → 快照回收(逐个查剩余 links+build 引用；独占才删：v2 八族手动按
    snapshot_id 删，v1/段落/本体证据随快照行 CASCADE)
  → MinIO 对象回收(软列清点归零才删字节+登记行)

快照废弃轨道：``deprecate_superseded_snapshots``（非当前 serving → DEPRECATED）
→ ``reclaim_deprecated_snapshots``（废弃满 N 天物理回收）。指纹命中复活
READY 见 snapshot_store 侧（切回范式不必重挖）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: v2 投影族（无 FK，必须按 snapshot_id 手动删；final + staging 孪生）。
_V2_SNAPSHOT_TABLES = (
    "asset_retrieval_units_v2", "asset_retrieval_units_v2_staging",
    "asset_retrieval_embeddings_v2", "asset_retrieval_embeddings_v2_staging",
    "asset_structure_nodes", "asset_structure_nodes_staging",
    "asset_structure_edges", "asset_structure_edges_staging",
    "asset_structured_assets", "asset_structured_assets_staging",
    "asset_table_cells", "asset_table_cells_staging",
    "asset_snapshot_readiness", "asset_snapshot_readiness_staging",
    "asset_source_locators", "asset_source_locators_staging",
)

#: 当前 serving 快照集（与 kb/db.get_current_serving_snapshot 及 Java
#: AssetBuildDocumentSnapshotMapper 逐语义对齐——三处修改必须同步）：
#: 先 DISTINCT ON 取每文档最新 validated/published Build 行（**不看**
#: selection_status——被后续 build 标 removed 的文档不得回退旧 active 行），
#: 再滤 active。b.id DESC 为同刻 created_at 的确定性决胜。
_SERVING_CTE = """
    serving AS (
        SELECT snapshot_id FROM (
            SELECT DISTINCT ON (bs.document_id)
                   bs.document_snapshot_id AS snapshot_id,
                   bs.selection_status
            FROM asset_build_document_snapshots bs
            JOIN asset_builds b ON b.id = bs.build_id
            JOIN asset_documents d ON d.id = bs.document_id
            WHERE b.status IN ('validated', 'published')
              AND b.kb_id = d.kb_id
              AND d.deleted_at IS NULL
            ORDER BY bs.document_id, b.created_at DESC, b.id DESC
        ) latest
        WHERE latest.selection_status = 'active'
    )
"""

#: MinIO 对象的「活引用」持有列（storage_operations/审计属操作史不计）。
_OBJECT_REF_SQL = """
    SELECT
        (SELECT COUNT(*) FROM asset_documents
          WHERE storage_object_id = %(oid)s) AS docs,
        (SELECT COUNT(*) FROM asset_document_snapshots
          WHERE parse_ir_storage_object_id = %(oid)s) AS snapshots,
        (SELECT COUNT(*) FROM asset_document_snapshot_links
          WHERE source_storage_object_id = %(oid)s) AS links,
        (SELECT COUNT(*) FROM asset_parse_runs
          WHERE source_storage_object_id = %(oid)s
             OR parse_ir_storage_object_id = %(oid)s) AS parse_runs,
        (SELECT COUNT(*) FROM asset_upload_sessions
          WHERE committed_storage_object_id = %(oid)s) AS sessions
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PurgeError(RuntimeError):
    """硬删管线错误（带机器可读 reason 前缀）。"""


#: 活跃挖掘 Run 状态口径（db.py 同款；purge 前置门禁用）.
_ACTIVE_RUN_STATES = ("queued", "running")


class PurgeService:
    """统一硬删管线（依赖注入 pool + object_store，可测）."""

    def __init__(self, pool: Any, object_store: Any = None):
        self._pool = pool
        self._object_store = object_store

    def _conn(self):
        return self._pool.connection()

    @classmethod
    def empty_summary(cls) -> dict[str, Any]:
        """公开空摘要（空集删除路径用——外部不该摸私有 _summary）."""
        return {
            "deleted_documents": [],
            "reclaimed_snapshots": 0,
            "skipped_shared_snapshots": [],
            "reclaimed_objects": 0,
            "skipped_shared_objects": [],
        }

    async def assert_kb_idle(self, kb_id: str) -> None:
        """在途挖掘 Run 门禁（审查 M-2）：queued/running 命中即拒绝——
        先删 Run 行会让 dispatcher 对空行写入、Run 崩在中途不可恢复."""
        async with self._conn() as conn:
            cur = await conn.execute(
                """SELECT id FROM mining_runs
                   WHERE kb_id = %s AND status = ANY(%s) LIMIT 1""",
                [kb_id, list(_ACTIVE_RUN_STATES)])
            if await cur.fetchone() is not None:
                raise PurgeError(
                    f"kb_busy: {kb_id} 有排队/执行中的挖掘任务，"
                    "请等其完成或取消后再删除")

    # ------------------------------------------------------------ 文档批量

    async def purge_documents(
        self, kb_id: str, document_ids: list[str], *,
        assert_kb: bool = True,
    ) -> dict[str, Any]:
        """硬删一批文档及其全部衍生数据（含软删态文档——清理存量）.

        ``assert_kb=False`` 供 purge_kb 内部复用（文档集已按库取好）。
        返回 {deleted_documents, reclaimed_snapshots, skipped_shared_snapshots,
        reclaimed_objects, skipped_shared_objects}。
        """
        doc_ids = [str(d) for d in document_ids if str(d).strip()]
        if not doc_ids:
            return self.empty_summary()
        if not kb_id:
            raise PurgeError("kb_required: kb_id 不能为空")
        if assert_kb:
            await self.assert_kb_idle(kb_id)

        async with self._conn() as conn:
            # 归属校验（软删文档也允许清——存量清理路径）
            cur = await conn.execute(
                """SELECT id, storage_object_id FROM asset_documents
                   WHERE id = ANY(%s) AND kb_id = %s""",
                [doc_ids, kb_id],
            )
            rows = [dict(r) for r in await cur.fetchall()]
            if assert_kb and len(rows) != len(set(doc_ids)):
                found = {r["id"] for r in rows}
                missing = [d for d in doc_ids if d not in found]
                raise PurgeError(
                    f"document_not_in_kb: {missing[:3]}（共 {len(missing)} 个）")
            doc_ids = [r["id"] for r in rows]
            source_objects = {r["storage_object_id"] for r in rows
                              if r.get("storage_object_id")}

            # 快照集合（删文档行前取——links 会随文档 CASCADE 消失）
            cur = await conn.execute(
                """SELECT DISTINCT document_snapshot_id
                   FROM asset_document_snapshot_links WHERE document_id = ANY(%s)""",
                [doc_ids],
            )
            snapshot_ids = [r["document_snapshot_id"] for r in await cur.fetchall()]

            # 1) 跨库引用行
            await conn.execute(
                "DELETE FROM kb_document_refs WHERE document_id = ANY(%s)", [doc_ids])
            # 2) 挖掘历史轨（无 FK，手动）
            await conn.execute(
                "DELETE FROM mining_run_documents WHERE document_id = ANY(%s)",
                [doc_ids])
            await conn.execute(
                """DELETE FROM asset_parse_run_attempts WHERE parse_run_id IN
                     (SELECT id FROM asset_parse_runs WHERE document_id = ANY(%s))""",
                [doc_ids])
            await conn.execute(
                "DELETE FROM asset_parse_runs WHERE document_id = ANY(%s)", [doc_ids])
            # 3) 上传会话 / 文件审计（随文档陪葬——硬删语义）
            await conn.execute(
                "DELETE FROM asset_upload_sessions WHERE committed_document_id = ANY(%s)",
                [doc_ids])
            await conn.execute(
                "DELETE FROM asset_file_audit_events WHERE document_id = ANY(%s)",
                [doc_ids])
            # 4) 文档行（CASCADE 带走 snapshot_links + build 选片行）
            await conn.execute(
                "DELETE FROM asset_documents WHERE id = ANY(%s) AND kb_id = %s",
                [doc_ids, kb_id])

        # 5) 快照回收（出连接后逐个查剩余引用；独占才删）
        snap_summary = await self._reclaim_snapshots_if_orphaned(snapshot_ids)
        # 6) MinIO 对象回收（源对象 + 被回收快照的 IR 对象）
        objects = source_objects | snap_summary.pop("_ir_objects")
        obj_summary = await self._reclaim_objects_if_orphaned(objects)

        summary = self._summary()
        summary["deleted_documents"] = doc_ids
        summary.update(snap_summary)
        summary.update(obj_summary)
        logger.info("[purge] documents=%s snapshots=%s objects=%s",
                    len(doc_ids), summary["reclaimed_snapshots"],
                    summary["reclaimed_objects"])
        return summary

    # ------------------------------------------------------------ 整库

    async def purge_kb(self, kb_id: str) -> dict[str, Any]:
        """整库硬删：全套文档管线 + 库级历史轨 + 库行.

        要求 KB 存在（active 或软删态——后者即内网存量清理入口）。
        """
        async with self._conn() as conn:
            cur = await conn.execute(
                "SELECT id FROM knowledge_bases WHERE id = %s", [kb_id])
            if await cur.fetchone() is None:
                raise PurgeError(f"kb_not_found: {kb_id}")
        await self.assert_kb_idle(kb_id)
        async with self._conn() as conn:
            # KB 自有 Build 先删（KB Build 不进域级 release——publishing 恒拒，
            # 故无 RESTRICT 风险）；选片行随 CASCADE。先删 Build 让快照回收
            # 的「历史引用」检查只剩其他库的 Build。
            await conn.execute(
                "DELETE FROM asset_builds WHERE kb_id = %s", [kb_id])
            await conn.execute(
                "DELETE FROM mining_runs WHERE kb_id = %s", [kb_id])
            cur = await conn.execute(
                "SELECT id FROM asset_documents WHERE kb_id = %s", [kb_id])
            doc_ids = [r["id"] for r in await cur.fetchall()]

        summary = await self.purge_documents(
            kb_id, doc_ids, assert_kb=False)

        async with self._conn() as conn:
            # 库级残留（无 FK 软引用）+ CASCADE 族（成员/文件夹/开放库随库行走）
            await conn.execute(
                "DELETE FROM kb_document_refs WHERE kb_id = %s", [kb_id])
            await conn.execute(
                "DELETE FROM asset_storage_quotas WHERE kb_id = %s", [kb_id])
            await conn.execute(
                "DELETE FROM asset_upload_sessions WHERE kb_id = %s", [kb_id])
            await conn.execute(
                "DELETE FROM onenet_imports WHERE kb_id = %s", [kb_id])
            await conn.execute(
                """DELETE FROM asset_file_audit_events
                   WHERE kb_id = %s AND document_id IS NULL""", [kb_id])
            cur = await conn.execute(
                "DELETE FROM knowledge_bases WHERE id = %s RETURNING id", [kb_id])
            if await cur.fetchone() is None:
                raise PurgeError(f"kb_delete_failed: {kb_id}")
        summary["purged_kb"] = kb_id
        logger.info("[purge] kb=%s documents=%s", kb_id, len(doc_ids))
        return summary

    # ------------------------------------------------------------ 文件夹级联

    async def purge_folder(self, kb_id: str, path: str) -> dict[str, Any]:
        """文件夹级联硬删：树下全部文档走统一管线 + 目录子树删除."""
        await self.assert_kb_idle(kb_id)
        async with self._conn() as conn:
            cur = await conn.execute(
                "SELECT id FROM kb_folders WHERE kb_id = %s AND path = %s",
                [kb_id, path])
            if await cur.fetchone() is None:
                raise PurgeError(f"folder_not_found: {path!r}")
            cur = await conn.execute(
                """SELECT id FROM asset_documents WHERE kb_id = %s
                     AND (directory_path = %s OR starts_with(directory_path, %s))""",
                [kb_id, path, path + "/"])
            doc_ids = [r["id"] for r in await cur.fetchall()]

        summary = await self.purge_documents(kb_id, doc_ids, assert_kb=False)
        async with self._conn() as conn:
            cur = await conn.execute(
                """DELETE FROM kb_folders WHERE kb_id = %s
                     AND (path = %s OR starts_with(path, %s)) RETURNING id""",
                [kb_id, path, path + "/"])
            summary["removed_folders"] = len(await cur.fetchall())
        logger.info("[purge] folder=%r docs=%s", path, len(doc_ids))
        return summary

    async def folder_delete_preview(self, kb_id: str, path: str) -> dict[str, int]:
        """级联删除预览（确认框计数）."""
        async with self._conn() as conn:
            cur = await conn.execute(
                """SELECT
                     (SELECT COUNT(*) FROM kb_folders WHERE kb_id = %s
                        AND (path = %s OR starts_with(path, %s))) AS folders,
                     (SELECT COUNT(*) FROM asset_documents WHERE kb_id = %s
                        AND (directory_path = %s
                             OR starts_with(directory_path, %s))) AS documents""",
                [kb_id, path, path + "/", kb_id, path, path + "/"])
            row = dict(await cur.fetchone())
            return {"folders": int(row["folders"]),
                    "documents": int(row["documents"])}

    # ------------------------------------------------------------ 快照回收

    async def _reclaim_snapshots_if_orphaned(
        self, snapshot_ids: list[str],
    ) -> dict[str, Any]:
        """逐个检查快照剩余引用（links + build 选片），独占才物理回收."""
        out: dict[str, Any] = {
            "reclaimed_snapshots": 0, "skipped_shared_snapshots": [],
            "_ir_objects": set(),
        }
        for sid in snapshot_ids:
            async with self._conn() as conn:
                cur = await conn.execute(
                    """SELECT
                         (SELECT COUNT(*) FROM asset_document_snapshot_links
                           WHERE document_snapshot_id = %s) AS links,
                         (SELECT COUNT(*) FROM asset_build_document_snapshots
                           WHERE document_snapshot_id = %s) AS builds,
                         parse_ir_storage_object_id
                       FROM asset_document_snapshots WHERE id = %s""",
                    [sid, sid, sid])
                row = await cur.fetchone()
                if row is None:
                    continue
                row = dict(row)
                if row["links"] or row["builds"]:
                    out["skipped_shared_snapshots"].append(sid)
                    continue
                if row.get("parse_ir_storage_object_id"):
                    out["_ir_objects"].add(row["parse_ir_storage_object_id"])
                if not await self._delete_snapshot_rows(conn, sid):
                    out["skipped_shared_snapshots"].append(sid)
                    if row.get("parse_ir_storage_object_id"):
                        out["_ir_objects"].discard(row["parse_ir_storage_object_id"])
                    continue
            out["reclaimed_snapshots"] += 1
        return out

    @staticmethod
    async def _delete_snapshot_rows(conn: Any, snapshot_id: str) -> bool:
        """v2 八族手动删 + 快照行条件删（v1/段落/本体证据随 CASCADE）.

        行删除带 NOT EXISTS 引用守卫（并发新引用的窄竞态收口）：命中新引用
        时 DELETE 影响 0 行，返回 False 由调用方按共享跳过。
        """
        for table in _V2_SNAPSHOT_TABLES:
            await conn.execute(
                f"DELETE FROM {table} WHERE snapshot_id = %s", [snapshot_id])
        cur = await conn.execute(
            """DELETE FROM asset_document_snapshots s WHERE s.id = %s
                 AND NOT EXISTS (
                     SELECT 1 FROM asset_document_snapshot_links
                      WHERE document_snapshot_id = s.id)
                 AND NOT EXISTS (
                     SELECT 1 FROM asset_build_document_snapshots
                      WHERE document_snapshot_id = s.id)
                 RETURNING s.id""",
            [snapshot_id])
        return await cur.fetchone() is not None

    # ------------------------------------------------------------ 对象回收

    async def _reclaim_objects_if_orphaned(
        self, object_ids: set[str],
    ) -> dict[str, Any]:
        out = {"reclaimed_objects": 0, "skipped_shared_objects": []}
        for oid in object_ids:
            if not oid:
                continue
            async with self._conn() as conn:
                cur = await conn.execute(_OBJECT_REF_SQL, {"oid": oid})
                refs = dict(await cur.fetchone())
                if any(refs.values()):
                    out["skipped_shared_objects"].append(oid)
                    continue
                cur = await conn.execute(
                    """SELECT provider, bucket, object_key, object_version_id
                       FROM asset_storage_objects WHERE id = %s""", [oid])
                row = await cur.fetchone()
                if row is None:
                    continue
                loc = dict(row)
            # 先删字节后删行（审查 L-2）：字节失败时登记行仍在、后续可重试；
            # 行先删则失败即成无行孤儿，永无清理方。
            bytes_deleted = True
            if self._object_store is not None:
                try:
                    from knowledge_mining.mining.contracts.storage.types import (
                        ObjectLocation,
                    )
                    await asyncio.to_thread(
                        self._object_store.delete,
                        ObjectLocation(
                            bucket=loc["bucket"], object_key=loc["object_key"],
                            version_id=loc.get("object_version_id"),
                        ),
                    )
                except Exception:  # noqa: BLE001 - 字节回收失败不阻断管线
                    bytes_deleted = False
                    logger.warning("[purge] object bytes delete failed: %s",
                                   loc.get("object_key"), exc_info=True)
            if bytes_deleted:
                async with self._conn() as conn:
                    await conn.execute(
                        "DELETE FROM asset_storage_objects WHERE id = %s", [oid])
                out["reclaimed_objects"] += 1
            else:
                out["skipped_shared_objects"].append(oid)
        return out

    # ------------------------------------------------------------ GC 轨道

    async def deprecate_superseded_snapshots(self) -> dict[str, int]:
        """把不再是任何活文档当前 serving 版本的 READY 快照标 DEPRECATED.

        serving 规则与 kb/db.get_current_serving_snapshot 及 Java
        AssetBuildDocumentSnapshotMapper 逐语义对齐（修改必须三处同步）：
        最新 validated/published KB Build 的 active 选片（Build 属文档所在库）。
        created_at 早于 1 小时才标——避开在途 Run。
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        async with self._conn() as conn:
            cur = await conn.execute(
                f"""
                WITH {_SERVING_CTE}
                UPDATE asset_document_snapshots s
                   SET lifecycle_status = 'DEPRECATED', deprecated_at = %s
                 WHERE s.lifecycle_status = 'READY'
                   AND s.created_at < %s
                   AND NOT EXISTS (
                       SELECT 1 FROM serving WHERE serving.snapshot_id = s.id)
                RETURNING s.id
                """,
                [_now(), cutoff],
            )
            marked = len(await cur.fetchall())
        logger.info("[gc] deprecated %s superseded snapshots", marked)
        return {"deprecated": marked}

    async def reclaim_deprecated_snapshots(
        self, *, older_than_days: int = 7,
    ) -> dict[str, int]:
        """物理回收废弃满 N 天的快照（7 天缓冲 = 切回范式不必重挖）.

        links/build 选片行是引用记录——快照已决定回收，引用随之销账。
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=older_than_days)
        ).isoformat()
        async with self._conn() as conn:
            cur = await conn.execute(
                """SELECT id, parse_ir_storage_object_id
                   FROM asset_document_snapshots
                   WHERE lifecycle_status = 'DEPRECATED' AND deprecated_at < %s""",
                [cutoff])
            stale = [dict(r) for r in await cur.fetchall()]
        ir_objects = set()
        reclaimed = 0
        skipped_serving = 0
        for row in stale:
            sid = row["id"]
            # 满期复核（审查 HIGH-2）：标记后可能又成为 serving（长 Run 补验、
            # 切回范式复活失败路径等）——serving 命中即跳过并复活 READY，
            # 绝不删正在服务的知识。注意**不做**「活文档 link」检查：links 是
            # append-only 血缘（文档历史上用过的快照永远挂着 link），按它判
            # 一切历史快照均不可回收；「还有用」的判定交给 serving 集——切回
            # 范式走指纹命中复活，不依赖旧行存在。
            async with self._conn() as conn:
                cur = await conn.execute(
                    f"""
                    WITH {_SERVING_CTE}
                    SELECT EXISTS (SELECT 1 FROM serving
                                    WHERE serving.snapshot_id = %s) AS is_serving
                    """,
                    [sid])
                guard = dict(await cur.fetchone())
                if guard["is_serving"]:
                    skipped_serving += 1
                    await conn.execute(
                        """UPDATE asset_document_snapshots
                              SET lifecycle_status = 'READY', deprecated_at = NULL
                            WHERE id = %s""", [sid])
                    continue
                await conn.execute(
                    """DELETE FROM asset_document_snapshot_links
                       WHERE document_snapshot_id = %s""", [sid])
                await conn.execute(
                    """DELETE FROM asset_build_document_snapshots
                       WHERE document_snapshot_id = %s""", [sid])
                await self._delete_snapshot_rows(conn, sid)
            if row.get("parse_ir_storage_object_id"):
                ir_objects.add(row["parse_ir_storage_object_id"])
            reclaimed += 1
        objects = await self._reclaim_objects_if_orphaned(ir_objects)
        logger.info("[gc] reclaimed %s deprecated snapshots (%s objects)",
                    reclaimed, objects["reclaimed_objects"])
        return {"reclaimed": reclaimed, "skipped_serving": skipped_serving,
                "reclaimed_objects": objects["reclaimed_objects"]}

    # ------------------------------------------------------------ misc

    def _summary(self) -> dict[str, Any]:
        return self.empty_summary()


__all__ = ["PurgeError", "PurgeService"]
