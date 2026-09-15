# -*- coding: utf-8 -*-
"""重同步（47 号 §四-7）：三信号探测 → 重放 selection 重拉 → nid diff → 文件级传播.

- 探测信号：parsed_version / total_slices / part_id max（任一变化即 changed）；
- diff：新旧切片集合按 nid 比对（新增/消失/内容变更——content 哈希）；
- 传播：受影响文件重写对象 + Document content_revision 递增 + 入挖掘；
  消失文件的 Document 软删 + **同步清理 kb_document_refs 行**（引用方自动收窄）；
- 失败语义：拉取/校验失败 → import status=failed，已入库文档不动。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any, Callable

from knowledge_mining.mining.onenet.fetch import (
    Selection, fetch_selection, load_slices,
)
from knowledge_mining.mining.onenet.import_service import OnenetRepo
from knowledge_mining.mining.onenet.restore import restore_files

logger = logging.getLogger(__name__)


class ResyncError(RuntimeError):
    """重同步错误。"""


def probe_changes(
    client: Any, import_row: dict[str, Any],
) -> dict[str, Any]:
    """三信号探测（不拉数据）：parsed_version / total / part max."""
    source_id = import_row["source_id"]
    total = client.count_source(source_id)
    pr = client.part_range(source_id)
    profile = client.probe_source(source_id)
    version = profile.get("parsed_version")
    signals = {
        "parsed_version": {
            "seen": import_row.get("parsed_version_seen"),
            "now": version,
            "changed": bool(version) and version != import_row.get("parsed_version_seen"),
        },
        "total_slices": {
            "seen": import_row.get("total_slices"),
            "now": total,
            "changed": (import_row.get("total_slices") is not None
                        and int(import_row["total_slices"] or 0) != int(total or 0)),
        },
        "part_max": {
            "seen": import_row.get("fetched_max_part_id"),
            "now": pr.get("max"),
            "changed": (import_row.get("fetched_max_part_id") is not None
                        and pr.get("max") is not None
                        and int(import_row["fetched_max_part_id"] or 0)
                        != int(pr["max"])),
        },
    }
    return {"changed": any(s["changed"] for s in signals.values()),
            "signals": signals,
            "probe": {"total_slices": total, "part_id": pr,
                      "parsed_version": version}}


def diff_slices(old: list[dict], new: list[dict]) -> dict[str, list]:
    """nid 集合 diff：新增/消失/内容变更（content sha1，含表格标记原文）."""
    def index(rows):
        return {str(r.get("nid")): r for r in rows if r.get("nid")}

    old_by_nid, new_by_nid = index(old), index(new)
    added = sorted(set(new_by_nid) - set(old_by_nid))
    removed = sorted(set(old_by_nid) - set(new_by_nid))
    changed = sorted(
        nid for nid in set(old_by_nid) & set(new_by_nid)
        if _content_hash(old_by_nid[nid]) != _content_hash(new_by_nid[nid])
    )
    return {"added": added, "removed": removed, "changed": changed}


def _content_hash(row: dict) -> str:
    payload = (str(row.get("content") or "")
               + "|" + str(row.get("path") or "")
               + "|" + str(row.get("part_id") or ""))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


async def resync(
    *,
    repo: OnenetRepo,
    kbdb: Any,
    doc_service: Any,
    client: Any,
    import_id: str,
    workspace_root: Path,
    refs_cleanup: Callable[[list[str]], Any] | None = None,
    reminer: Callable[[str], Any] | None = None,
    register_file: Callable[..., Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """重同步入口（async：DB 调用全程 await）.

    返回 {changed, signals, diff, updated_documents, removed_documents}。
    """
    import_row = await repo.get_import(import_id)
    if import_row is None:
        raise ResyncError(f"import_not_found: {import_id}")
    if import_row.get("status") not in ("done", "failed"):
        raise ResyncError(f"import_busy: 当前状态 {import_row.get('status')}")

    # 探测与拉取是同步网络 IO（分钟级）——下放主循环默认执行器（安全审查 H-2），
    # async 回调（DB）仍留在事件循环上，不跨循环复用 async pool。
    # force（审查 H7）：selection 刚被 PATCH 扩大时绕过三信号短路，强制重放。
    probe = await asyncio.to_thread(probe_changes, client, import_row)
    if not probe["changed"] and not force:
        return {"changed": False, "signals": probe["signals"], "diff": None,
                "updated_documents": [], "removed_documents": []}

    source_id = import_row["source_id"]
    kb_id = import_row["kb_id"]
    selection = Selection.from_dict(import_row.get("selection_json") or {})
    workspace = Path(workspace_root) / import_row["domain"] / source_id
    try:
        return await _resync_inner(
            repo=repo, kbdb=kbdb, doc_service=doc_service, client=client,
            import_row=import_row, import_id=import_id, probe=probe,
            selection=selection, workspace=workspace,
            refs_cleanup=refs_cleanup, reminer=reminer,
            register_file=register_file)
    except Exception as e:
        # 审查 M1：失败如实置 failed（docstring 承诺），已入库文档不动
        try:
            await repo.update_import(import_id, status="failed", error=str(e)[:2000])
        except Exception:
            logger.exception("[onenet] resync 失败态回写也失败: %s", import_id)
        raise


async def _resync_inner(
    *, repo, kbdb, doc_service, client, import_row, import_id, probe,
    selection, workspace, refs_cleanup, reminer, register_file,
) -> dict[str, Any]:
    source_id = import_row["source_id"]
    kb_id = import_row["kb_id"]

    # 旧基线必须在 fetch 覆写 slices.jsonl **之前**读：
    # prev（上次同步态）优先，否则取当前批次（首次重同步的原始导入态）
    prev_path = workspace / "slices.prev.jsonl"
    data_path = workspace / "slices.jsonl"
    old_slices = await asyncio.to_thread(
        lambda: (load_slices(prev_path) if prev_path.exists()
                 else (load_slices(data_path) if data_path.exists() else [])))

    # 上游重解析（parsed_version 变）→ 旧段文件内容全失效，必须清段重拉；
    # 仅追加（part_max/total 变）→ 旧段幂等复用，只补新段。
    # prev 基线保留到成功轮转（审查 M1：失败不毒化基线）。
    import shutil
    if probe["signals"]["parsed_version"]["changed"]:
        parts_dir = workspace / "parts"
        if parts_dir.exists():
            shutil.rmtree(parts_dir)

    outcome = await asyncio.to_thread(
        fetch_selection, client, source_id, selection, workspace)
    new_slices = await asyncio.to_thread(load_slices, outcome.slices_path)

    diff = diff_slices(old_slices, new_slices)
    touched_nids = set(diff["added"]) | set(diff["removed"]) | set(diff["changed"])
    new_result = restore_files(new_slices)
    old_result = restore_files(old_slices) if old_slices else None

    updated_documents: list[str] = []
    removed_documents: list[str] = []

    new_files = {f.file_path: f for f in new_result.files}
    old_files = {f.file_path: f for f in (old_result.files if old_result else [])}

    # 消失/不再归属的文件 → 软删 + 清引用
    from knowledge_mining.mining.onenet.import_service import document_key_for
    for file_path, old_file in old_files.items():
        if file_path in new_files:
            continue
        key = document_key_for(source_id, file_path)
        doc = await kbdb.find_document_by_key(kb_id, key)
        if doc is None or doc.get("deleted_at") is not None:
            continue
        await kbdb.soft_delete_document(doc["id"])
        removed_documents.append(str(doc["id"]))
    if removed_documents and refs_cleanup is not None:
        await refs_cleanup(removed_documents)

    # 受影响文件 → 重写对象 + revision 递增
    for file_path, new_file in new_files.items():
        file_nids = set(new_file.slice_nids)
        if not (file_nids & touched_nids):
            continue  # 该文件无任何受影响切片
        key = document_key_for(source_id, file_path)
        doc = await kbdb.find_document_by_key(kb_id, key)
        payload = _jsonl_bytes(new_file.slices)
        if doc is None:
            # 审查 H7：selection 扩大产生的新文件就地登记（幂等 key 同导入），
            # 不再是断头路。register_file 由路由层从导入服务注入。
            if register_file is None:
                raise ResyncError(
                    f"new_file_without_register: {key}——selection 扩大产生新文件"
                    "且未注入登记通道")
            await register_file(
                kb_id=kb_id, domain=import_row["domain"],
                source_id=source_id, restored=new_file,
                actor_id=import_row["created_by"],
                doc_name=((new_slices[0].get("doc_name") if new_slices else None)
                          or import_row.get("doc_name")))
            updated_documents.append(f"new:{key}")
            continue
        storage = await doc_service.store_source_bytes(
            payload, mime="application/x-onenet+jsonl")
        await kbdb.replace_document_object(
            document_id=doc["id"],
            storage_object_id=storage.id,
            source_raw_hash=storage.sha256,
            file_size=storage.size,
        )
        updated_documents.append(str(doc["id"]))

    # 提交：本次批次轮转为 prev 基线 + 记录更新
    if data_path.exists():
        prev_path.write_bytes(data_path.read_bytes())
    await repo.update_import(
        import_id, status="done", error=None,
        parsed_version_seen=probe["probe"].get("parsed_version"),
        total_slices=probe["probe"].get("total_slices"),
        fetched_max_part_id=(probe["probe"].get("part_id") or {}).get("max"),
        document_count=len(new_result.files),
    )
    if updated_documents and reminer is not None:
        await reminer(kb_id)
    return {"changed": True, "signals": probe["signals"], "diff": diff,
            "updated_documents": updated_documents,
            "removed_documents": removed_documents}


def _jsonl_bytes(slices) -> bytes:
    import json
    return ("\n".join(json.dumps(s, ensure_ascii=False) for s in slices)
            + "\n").encode("utf-8")


__all__ = ["ResyncError", "diff_slices", "probe_changes", "resync"]
