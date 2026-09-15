# -*- coding: utf-8 -*-
"""重同步：三信号探测 / nid diff / 文件级传播 / 引用清理 / 段清空策略 / selection 合并."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.onenet.fetch import Selection
from knowledge_mining.mining.onenet.resync import (
    ResyncError, diff_slices, probe_changes, resync,
)

pytestmark = pytest.mark.asyncio

PKG = "Pkg.hwics"


def _row(part_id, path, content="c", nid=None):
    return {"nid": nid or f"n{part_id}", "part_id": part_id, "path": path,
            "title": path.split(" > ")[-1], "content": content,
            "source_id": "DOC1", "parsed_version": "v1"}


# ---------------------------------------------------------------- diff_slices


def test_diff_slices_added_removed_changed():
    old = [_row(1, f"{PKG} > A > B"), _row(2, f"{PKG} > A > C"),
           _row(3, f"{PKG} > D > E")]
    new = [_row(1, f"{PKG} > A > B"),                 # 不变
           _row(2, f"{PKG} > A > C", content="改了"),   # 内容变更
           _row(4, f"{PKG} > F > G")]                  # 新增
    d = diff_slices(old, new)
    assert d["added"] == ["n4"]
    assert d["removed"] == ["n3"]
    assert d["changed"] == ["n2"]


def test_diff_slices_identical():
    rows = [_row(1, f"{PKG} > A > B")]
    d = diff_slices(rows, [dict(rows[0])])
    assert d == {"added": [], "removed": [], "changed": []}


# ---------------------------------------------------------------- probe_changes


class FakeProbeClient:
    def __init__(self, *, total, part_max, version):
        self._total, self._part_max, self._version = total, part_max, version

    def count_source(self, sid):
        return self._total

    def part_range(self, sid):
        return {"min": 1, "max": self._part_max}

    def probe_source(self, sid):
        return {"parsed_version": self._version}


def _import_row(**kw):
    base = {"source_id": "DOC1", "kb_id": "kb-pub", "domain": "d1",
            "parsed_version_seen": "v1", "total_slices": 3,
            "fetched_max_part_id": 3, "status": "done",
            "selection_json": {"subtrees": [], "max_part_id": None}}
    base.update(kw)
    return base


def test_probe_changes_no_change():
    p = probe_changes(FakeProbeClient(total=3, part_max=3, version="v1"),
                      _import_row())
    assert p["changed"] is False


def test_probe_changes_version_signal():
    p = probe_changes(FakeProbeClient(total=3, part_max=3, version="v2"),
                      _import_row())
    assert p["changed"] is True
    assert p["signals"]["parsed_version"]["changed"] is True
    assert p["signals"]["total_slices"]["changed"] is False


def test_probe_changes_append_signal():
    p = probe_changes(FakeProbeClient(total=5, part_max=5, version="v1"),
                      _import_row())
    assert p["changed"] is True
    assert p["signals"]["part_max"]["changed"] is True


# ---------------------------------------------------------------- resync 端到端


class FakeFetchClient:
    """可变行集 fake：count/part_range/chunk 三件套."""

    def __init__(self, rows, total, part_max):
        self.rows = rows
        self.total = total
        self.part_max = part_max
        self.chunk_calls = 0

    def count_source(self, sid):
        return self.total

    def part_range(self, sid):
        return {"min": 1, "max": self.part_max}

    def fetch_source_chunk(self, sid, lo, hi, page_size=1000, fields=None):
        self.chunk_calls += 1
        return [r for r in self.rows if lo <= r["part_id"] <= hi]

    def probe_source(self, sid):
        return {"parsed_version": "v1"}


class FakeRepo2:
    def __init__(self, row):
        self.row = row
        self.updates: list[tuple] = []

    async def get_import(self, import_id):
        return dict(self.row)

    async def update_import(self, import_id, **fields):
        self.updates.append((import_id, fields))
        self.row.update(fields)


class FakeKbDb2:
    def __init__(self):
        self.docs: dict[str, dict] = {}         # document_key -> {id, deleted_at}
        self.deleted: list[str] = []
        self.replaced: list[tuple] = []

    async def find_document_by_key(self, kb_id, key, *, include_deleted=False):
        return self.docs.get(key)

    async def soft_delete_document(self, document_id):
        self.deleted.append(document_id)
        for d in self.docs.values():
            if d["id"] == document_id:
                d["deleted_at"] = "t"

    async def replace_document_object(self, *, document_id, storage_object_id,
                                      source_raw_hash, file_size):
        self.replaced.append(document_id)
        return {"id": document_id, "content_revision": 2}


class FakeDocSvc2:
    def __init__(self):
        self.stored: list[bytes] = []

    async def store_source_bytes(self, payload, *, mime):
        self.stored.append(payload)
        return SimpleNamespace(id=f"obj-{len(self.stored)}",
                               sha256=f"h{len(self.stored)}", size=len(payload))


async def _bootstrap_workspace(ws: Path, rows):
    from knowledge_mining.mining.onenet.fetch import fetch_selection, load_slices
    out = fetch_selection(FakeFetchClient(rows, len(rows),
                                          max(r["part_id"] for r in rows)),
                          "DOC1", Selection(), ws, throttle_seconds=0)
    assert out.slice_count == len(rows)


async def test_resync_no_change_short_circuits(tmp_path):
    rows = [_row(1, f"{PKG} > A > B"), _row(2, f"{PKG} > A > C")]
    ws = tmp_path / "d1" / "DOC1"
    await _bootstrap_workspace(ws, rows)
    repo = FakeRepo2(_import_row(total_slices=2, fetched_max_part_id=2))
    kbdb, docsvc = FakeKbDb2(), FakeDocSvc2()
    client = FakeFetchClient(rows, total=2, part_max=2)  # 与 seen 一致
    out = await resync(repo=repo, kbdb=kbdb, doc_service=docsvc, client=client,
                       import_id="i1", workspace_root=tmp_path)
    assert out["changed"] is False
    assert out["diff"] is None


async def test_resync_content_change_updates_affected_file(tmp_path):
    old_rows = [_row(1, f"{PKG} > A > B"), _row(2, f"{PKG} > A > C"),
                _row(3, f"{PKG} > D > E")]
    ws = tmp_path / "d1" / "DOC1"
    await _bootstrap_workspace(ws, old_rows)
    from knowledge_mining.mining.onenet.import_service import document_key_for
    kbdb = FakeKbDb2()
    # 两个还原文件（β：A 含 B/C 标题，D 含 E；file_path 原样含包段，beta-2）
    for fpath, doc_id in ((f"{PKG} > A", "doc-A"), (f"{PKG} > D", "doc-D")):
        kbdb.docs[document_key_for("DOC1", fpath)] = {
            "id": doc_id, "deleted_at": None}

    # 上游 v2 重解析：n2 内容变更（同版本下段复用会掩蔽原地改——设计上内容
    # 变更只随 parsed_version 升级同步，47 号 §四-7）
    new_rows = [_row(1, f"{PKG} > A > B"),
                _row(2, f"{PKG} > A > C", content="更新后的 C"),  # n2 变
                _row(3, f"{PKG} > D > E")]
    client = FakeFetchClient(new_rows, total=4, part_max=4)
    client.probe_source = lambda sid: {"parsed_version": "v2"}  # 版本触发清段
    repo = FakeRepo2(_import_row())
    docsvc = FakeDocSvc2()
    out = await resync(repo=repo, kbdb=kbdb, doc_service=docsvc, client=client,
                       import_id="i1", workspace_root=tmp_path)
    assert out["changed"] is True
    assert out["diff"]["changed"] == ["n2"]
    # 只有 A 文件被重写（n2 属 A）；D 文件不受影响
    assert kbdb.replaced == ["doc-A"]
    assert len(docsvc.stored) == 1
    # prev 基线轮转
    assert (ws / "slices.prev.jsonl").exists()


async def test_resync_removed_file_soft_deletes_and_cleans_refs(tmp_path):
    old_rows = [_row(1, f"{PKG} > A > B"), _row(2, f"{PKG} > D > E")]
    ws = tmp_path / "d1" / "DOC1"
    await _bootstrap_workspace(ws, old_rows)
    from knowledge_mining.mining.onenet.import_service import document_key_for
    kbdb = FakeKbDb2()
    kbdb.docs[document_key_for("DOC1", f"{PKG} > A")] = {"id": "doc-A", "deleted_at": None}
    kbdb.docs[document_key_for("DOC1", f"{PKG} > D")] = {"id": "doc-D", "deleted_at": None}

    new_rows = [_row(1, f"{PKG} > A > B")]  # D 子树消失（v2 重解析）
    client = FakeFetchClient(new_rows, total=1, part_max=1)
    client.probe_source = lambda sid: {"parsed_version": "v2"}
    repo = FakeRepo2(_import_row())
    cleaned: list[list[str]] = []

    async def refs_cleanup(ids):
        cleaned.append(list(ids))

    out = await resync(repo=repo, kbdb=kbdb, doc_service=FakeDocSvc2(),
                       client=client, import_id="i1",
                       workspace_root=tmp_path, refs_cleanup=refs_cleanup)
    assert out["removed_documents"] == ["doc-D"]
    assert kbdb.deleted == ["doc-D"]
    assert cleaned == [["doc-D"]]   # 引用行同步清理 → 引用方自动收窄


async def test_resync_busy_rejected(tmp_path):
    repo = FakeRepo2(_import_row(status="fetching"))
    with pytest.raises(ResyncError, match="import_busy"):
        await resync(repo=repo, kbdb=FakeKbDb2(), doc_service=FakeDocSvc2(),
                     client=FakeFetchClient([], 0, 0), import_id="i1",
                     workspace_root=tmp_path)


async def test_resync_version_change_clears_stale_segments(tmp_path):
    rows_v1 = [_row(1, f"{PKG} > A > B", content="v1 内容")]
    ws = tmp_path / "d1" / "DOC1"
    await _bootstrap_workspace(ws, rows_v1)
    seg_before = list((ws / "parts").iterdir())
    assert seg_before  # 段已存在

    # 上游重解析：同 part_id 全新内容 + version v2
    rows_v2 = [_row(1, f"{PKG} > A > B", content="v2 重解析内容")]
    client = FakeFetchClient(rows_v2, total=1, part_max=1)
    client.probe_source = lambda sid: {"parsed_version": "v2"}
    from knowledge_mining.mining.onenet.import_service import document_key_for
    kbdb = FakeKbDb2()
    kbdb.docs[document_key_for("DOC1", f"{PKG} > A")] = {"id": "doc-A", "deleted_at": None}
    repo = FakeRepo2(_import_row())
    docsvc = FakeDocSvc2()
    out = await resync(repo=repo, kbdb=kbdb, doc_service=docsvc, client=client,
                       import_id="i1", workspace_root=tmp_path)
    assert out["changed"] is True
    # 段被清空重拉（不是复用旧段）→ 重写对象内容为新版
    assert "v2 重解析内容".encode("utf-8") in docsvc.stored[0]
    assert repo.row["parsed_version_seen"] == "v2"


# ---------------------------------------------------------------- selection merge


def test_selection_merge_union_and_wider():
    a = Selection(subtrees=("A",), max_part_id=2000)
    b = Selection(subtrees=("A", "D"), max_part_id=3000)
    m = a.merge(b)
    assert m.subtrees == ("A", "D")
    assert m.max_part_id == 2000  # 取更窄（更保守）——见 fetch 的 min 语义
