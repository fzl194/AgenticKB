# -*- coding: utf-8 -*-
"""导入编排测试：bootstrap 幂等 / 状态机 / document_key 幂等 / 重复拒绝 / metadata / 失败.

全部 fake（无 PG/MinIO/内网）。client_factory 返回的 fake client 由
fetch_selection 驱动（见 test_fetch 的 FakeFetch 模式，这里最小内联）。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

pytestmark = pytest.mark.asyncio

from knowledge_mining.mining.onenet import import_service as isvc
from knowledge_mining.mining.onenet.fetch import Selection
from knowledge_mining.mining.onenet.import_service import (
    PUBLIC_KB_NAME, OnenetImportError, OnenetImportService, OnenetRepo,
    document_key_for, sanitize_filename,
)

TOKEN_URL = "http://oauth2.test/token"
SEARCH_URL = "http://apigw.test/search?source_type=0"
PKG = "Pkg.hwics"


def _row(part_id, path, **extra):
    r = {"nid": f"n{part_id}", "part_id": part_id, "path": path,
         "title": path.split(" > ")[-1], "content": f"c{part_id}",
         "source_id": "DOC1", "doc_name": "UDG 手册", "parsed_version": "hwics_v1.0",
         "url": "https://support/x", "public_level": "C", "language": "cn",
         "publish_time": "2026-07-25", "product_line": ["云核心网"],
         "pbi": ["运营商 > 云核心网"], "attention": "reserved-value"}
    r.update(extra)
    return r


ROWS = [
    _row(1, f"{PKG} > A > B"),
    _row(2, f"{PKG} > A > C"),
    _row(3, f"{PKG} > D > E"),
]


def _fake_client(rows=None):
    """最小 fake：count/part_range/fetch_source_chunk 直供（绕过 HTTP）."""
    rows = ROWS if rows is None else rows

    class C:
        def count_source(self, source_id):
            return len(rows)

        def part_range(self, source_id):
            if not rows:
                return {"min": None, "max": None}
            return {"min": min(r["part_id"] for r in rows),
                    "max": max(r["part_id"] for r in rows)}

        def fetch_source_chunk(self, source_id, lo, hi, page_size=1000, fields=None):
            return [r for r in rows if lo <= r["part_id"] <= hi]

        def close(self):
            pass

    return C()


# ---------------------------------------------------------------- fakes


class FakeRepo:
    def __init__(self):
        self.imports: dict[str, dict] = {}
        self.public_kbs: dict[str, dict] = {}
        self.toc: dict[tuple, dict] = {}

    async def insert_import(self, **kw):
        row = {"id": "imp1", "document_count": None, "error": None,
               "status": "queued", **kw}
        self.imports[row["id"]] = row
        return dict(row)

    async def get_import(self, import_id):
        row = self.imports.get(import_id)
        return dict(row) if row else None

    async def find_import_by_source(self, domain, source_id):
        for r in self.imports.values():
            if r["domain"] == domain and r["source_id"] == source_id:
                return dict(r)
        return None

    async def list_imports(self, *, domain):
        return [dict(r) for r in self.imports.values() if r["domain"] == domain]

    async def update_import(self, import_id, **fields):
        self.imports[import_id].update(fields)

    async def find_public_kb(self, domain):
        return self.public_kbs.get(domain)

    async def put_toc_cache(self, **kw):
        self.toc[(kw["domain"], kw["source_id"])] = kw

    async def get_toc_cache(self, domain, source_id):
        return self.toc.get((domain, source_id))


class FakeKbService:
    def __init__(self, repo: FakeRepo):
        self._repo = repo
        self.created: list[dict] = []

    async def create_kb(self, **kw):
        self.created.append(kw)
        kb = {"id": f"kb-{kw['domain']}", "name": kw["name"], **kw}
        self._repo.public_kbs[kw["domain"]] = kb
        return kb

    async def create_kb_duplicate(self, **kw):
        raise RuntimeError("unique violation")

    raise_on_create = False

    async def create_kb2(self, **kw):
        if self.raise_on_create:
            self.raise_on_create = False
            raise RuntimeError("unique violation")
        return await self.create_kb(**kw)


class FakeKbDb:
    def __init__(self):
        self.docs: dict[str, dict] = {}          # id -> doc
        self.by_key: dict[tuple, dict] = {}      # (kb, key) -> doc
        self.by_location: dict[tuple, dict] = {}
        self.kbs: dict[str, dict] = {}

    async def get_kb(self, kb_id):
        return self.kbs.get(kb_id) or {"id": kb_id, "domain": "d1"}

    async def find_document_by_key(self, kb_id, key, *, include_deleted=False):
        doc = self.by_key.get((kb_id, key))
        if doc is None:
            return None
        if doc.get("deleted_at") and not include_deleted:
            return None
        return doc

    async def find_document_by_location(self, kb_id, directory, name, *, include_deleted=False):
        doc = self.by_location.get((kb_id, directory, name))
        return doc

    async def insert_document_from_storage(self, **kw):
        doc = {"id": f"doc-{len(self.docs) + 1}", "deleted_at": None, **kw}
        self.docs[doc["id"]] = doc
        self.by_key[(kw["kb_id"], kw["document_key"])] = doc
        self.by_location[(kw["kb_id"], kw.get("directory_path"), kw["document_name"])] = doc
        return doc


class FakeDocService:
    def __init__(self):
        self.stored: list[bytes] = []

    async def store_source_bytes(self, payload, *, mime):
        self.stored.append(payload)
        return SimpleNamespace(
            id=f"obj-{len(self.stored)}", sha256=f"sha{len(self.stored)}",
            size=len(payload))


class FakeFolders:
    def __init__(self):
        self.paths: list[str] = []

    async def ensure_folder_path(self, *, kb_id, path, user_id):
        self.paths.append(path)
        return {"id": f"folder-{path}", "path": path}


class _RepoCursor:
    async def fetchall(self):
        return [{"id": "newest"}, {"id": "older"}]


class _RepoConnection:
    async def execute(self, query, params):
        self.query = query
        self.params = params
        return _RepoCursor()


class _RepoConnectionContext:
    def __init__(self):
        self.conn = _RepoConnection()

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _RepoPool:
    def __init__(self):
        self.context = _RepoConnectionContext()

    def connection(self):
        return self.context


async def test_onenet_repo_list_imports_uses_pool_async_context_manager():
    """连接池直接返回 async context manager，不应额外包成 coroutine。"""
    pool = _RepoPool()

    rows = await OnenetRepo(pool).list_imports(domain="cloud_core_network")

    assert rows == [{"id": "newest"}, {"id": "older"}]
    assert pool.context.conn.params == ["cloud_core_network"]


async def _wait_terminal(repo, import_id, timeout=5.0):
    """轮询后台导入任务到终态（done/failed）——to_thread 调度时序不定."""
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        row = repo.imports.get(import_id) or {}
        if row.get("status") in ("done", "failed"):
            return row
        await asyncio.sleep(0.02)
    return repo.imports.get(import_id) or {}


def _service(tmp_path, *, auto_miner=None, client=None):
    if client is None:
        client = _fake_client()
    repo, kbsvc, kbdb, docsvc, folders = (
        FakeRepo(), None, FakeKbDb(), FakeDocService(), FakeFolders())
    kbsvc = FakeKbService(repo)
    svc = OnenetImportService(
        repo=repo, kbdb=kbdb, kb_service=kbsvc, doc_service=docsvc,
        folder_service=folders, client_factory=lambda: client,
        workspace_root=tmp_path / "ws", auto_miner=auto_miner,
    )
    return svc, repo, kbsvc, kbdb, docsvc, folders


# ---------------------------------------------------------------- utils


def test_document_key_stable_and_path_sensitive():
    k1 = document_key_for("DOC1", "A > B")
    assert k1 == f"onenet:DOC1:" + document_key_for("DOC1", "A > B").split(":")[2]
    assert document_key_for("DOC1", "A > B") != document_key_for("DOC1", "A > C")
    assert document_key_for("DOC1", "X") != document_key_for("DOC2", "X")


def test_sanitize_filename():
    assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_filename("") == "untitled"
    assert len(sanitize_filename("长" * 200)) == 80


# ---------------------------------------------------------------- bootstrap


async def test_ensure_public_kb_creates_once_then_reuses(tmp_path):
    svc, repo, kbsvc, *_ = _service(tmp_path)
    kb1 = await svc.ensure_public_kb(domain="d1", actor_id="admin1")
    kb2 = await svc.ensure_public_kb(domain="d1", actor_id="admin1")
    assert kb1["id"] == kb2["id"]
    assert len(kbsvc.created) == 1
    assert kbsvc.created[0]["visibility"] == "private"
    assert kbsvc.created[0]["metadata"] == {"kind": "onenet"}
    assert kbsvc.created[0]["name"] == PUBLIC_KB_NAME


async def test_ensure_public_kb_concurrent_conflict_recovers(tmp_path):
    svc, repo, kbsvc, *_ = _service(tmp_path)
    # 第一次 create 抛（模拟撞唯一约束），重查已有 → 复用
    original = kbsvc.create_kb

    async def conflict_then_exists(**kw):
        if not kbsvc.created:
            await original(**kw)      # 幂等守卫侧已写入
            raise RuntimeError("unique violation")
        return await original(**kw)

    kbsvc.create_kb = conflict_then_exists
    kb = await svc.ensure_public_kb(domain="d2", actor_id="admin1")
    assert kb["name"] == PUBLIC_KB_NAME


# ---------------------------------------------------------------- start + 状态机


async def test_start_import_runs_to_done(tmp_path):
    auto_calls: list[dict] = []

    async def auto_miner(*, kb, user_id, **kw):
        auto_calls.append({"kb": kb["id"], "user": user_id})
        return {"auto_mined": True}

    svc, repo, _, kbdb, docsvc, folders = _service(tmp_path, auto_miner=auto_miner)
    rec = await svc.start_import(
        domain="d1", source_id="DOC1", selection=Selection(),
        actor_id="admin1", username="管理员")
    final = await _wait_terminal(repo, rec["id"])
    assert final["status"] == "done"
    # β 规则：file A（heading B、C）+ file D（heading E）= 2 个文档
    assert final["document_count"] == 2
    assert auto_calls and auto_calls[0]["kb"] == final["kb_id"]
    assert final["kb_id"].startswith("kb-")
    assert len(docsvc.stored) == len(kbdb.docs) >= 1
    assert final["doc_name"] == "UDG 手册"
    assert final["parsed_version_seen"] == "hwics_v1.0"


async def test_import_document_key_idempotent_on_retry(tmp_path):
    svc, repo, _, kbdb, *_ = _service(tmp_path)
    rec1 = await svc.start_import(
        domain="d1", source_id="DOC1", selection=Selection(),
        actor_id="admin1", username="a")
    await _wait_terminal(repo, rec1["id"])
    n_docs = len(kbdb.docs)
    n_objects = len(svc._doc_service.stored)
    # 模拟重试：状态打回 queued 再跑（同 key 复用，不新建）
    await svc._run_import(rec1["id"])
    assert len(kbdb.docs) == n_docs
    assert len(svc._doc_service.stored) == n_objects
    assert repo.imports[rec1["id"]]["status"] == "done"


async def test_start_import_duplicate_source_rejected(tmp_path):
    svc, *_ = _service(tmp_path)
    await svc.start_import(domain="d1", source_id="DOC1",
                           selection=Selection(), actor_id="a", username="a")
    with pytest.raises(OnenetImportError, match="duplicate"):
        await svc.start_import(domain="d1", source_id="DOC1",
                               selection=Selection(), actor_id="a", username="a")


async def test_import_failure_marks_failed_with_reason(tmp_path):
    class BrokenClient:
        def count_source(self, sid):
            return 0

    svc, repo, *_ = _service(tmp_path, client=BrokenClient())
    rec = await svc.start_import(domain="d1", source_id="BAD",
                                 selection=Selection(), actor_id="a", username="a")
    final = await _wait_terminal(repo, rec["id"])
    assert final["status"] == "failed"
    assert "无切片" in final["error"]


# ---------------------------------------------------------------- 落库细节


async def test_document_metadata_mapping_and_raw(tmp_path):
    svc, repo, _, kbdb, *_ = _service(tmp_path)
    rec = await svc.start_import(domain="d1", source_id="DOC1",
                                 selection=Selection(), actor_id="a", username="a")
    await _wait_terminal(repo, rec["id"])
    doc = next(iter(kbdb.docs.values()))
    meta = doc["metadata"]
    assert meta["source_system"] == "onenet"
    assert meta["logical"] is True
    assert meta["source_id"] == "DOC1"
    assert meta["rule_version"] == "beta-2"
    assert meta["onenet"]["url"] == "https://support/x"
    assert meta["onenet"]["public_level"] == "C"
    assert meta["onenet"]["product_line"] == ["云核心网"]
    # 未映射字段兜底
    assert meta["onenet_raw"]["attention"] == "reserved-value"
    # 大字段不入 metadata
    assert "content" not in meta.get("onenet_raw", {})


async def test_document_mime_and_payload_are_jsonl(tmp_path):
    svc, repo, *_ = _service(tmp_path)
    rec = await svc.start_import(domain="d1", source_id="DOC1",
                                 selection=Selection(), actor_id="a", username="a")
    await _wait_terminal(repo, rec["id"])
    # 落库对象 mime 由 store_source_bytes 调用方指定（ONENET_JSONL_MIME 在
    # _import_file 内硬编码），payload 为切片 JSONL
    from knowledge_mining.mining.parse_adapters.onenet_jsonl import ONENET_JSONL_MIME
    assert ONENET_JSONL_MIME.startswith("application/x-onenet")
    payload = svc._doc_service.stored[0]
    first = json.loads(payload.decode("utf-8").splitlines()[0])
    assert first["nid"] and first["path"] and first["content"]


async def test_folders_created_from_upper_levels(tmp_path):
    svc, repo, *_ = _service(tmp_path)
    rec = await svc.start_import(domain="d1", source_id="DOC1",
                                 selection=Selection(), actor_id="a", username="a")
    await _wait_terminal(repo, rec["id"])
    # V1.3+beta-2 落位：file=Pkg.hwics>A（原样含包段），目录=顶层抽屉+包段
    both = f"UDG 手册 [DOC1]/{PKG}"
    assert svc._folders.paths == [both, both]
    doc_dirs = {d.get("directory_path") for d in svc._kbdb.docs.values()}
    assert doc_dirs == {both}


async def test_placement_top_segment_and_chapter_chain(tmp_path):
    """V1.3：顶层=文档名[source_id]，章节链按 β 目录镜像；doc_name 缺失退化纯 source_id."""
    rows = [_row(1, f"{PKG} > 02 特性配置 > QoS > display命令"),
            _row(2, f"{PKG} > 02 特性配置 > QoS > reset qos命令"),
            _row(3, f"{PKG} > 01 命令参考 > 常用命令 > aaa")]
    svc, repo, *_ = _service(tmp_path, client=_fake_client(rows))
    rec = await svc.start_import(domain="d1", source_id="DOC1",
                                 selection=Selection(), actor_id="a", username="a")
    await _wait_terminal(repo, rec["id"])
    dirs = sorted(d.get("directory_path") for d in svc._kbdb.docs.values())
    assert dirs == [f"UDG 手册 [DOC1]/{PKG}/01 命令参考",
                    f"UDG 手册 [DOC1]/{PKG}/02 特性配置"]
    assert set(svc._folders.paths) == {
        f"UDG 手册 [DOC1]/{PKG}/01 命令参考",
        f"UDG 手册 [DOC1]/{PKG}/02 特性配置"}

    # doc_name 缺失 → 顶层退化为纯 source_id
    rows2 = [{k: v for k, v in r.items() if k != "doc_name"} for r in rows]
    svc2, repo2, *_ = _service(tmp_path, client=_fake_client(rows2))
    rec2 = await svc2.start_import(domain="d2", source_id="DOC1",
                                   selection=Selection(), actor_id="a", username="a")
    await _wait_terminal(repo2, rec2["id"])
    assert f"DOC1/{PKG}/02 特性配置" in {
        d.get("directory_path") for d in svc2._kbdb.docs.values()}


async def test_placement_sanitizes_dangerous_folder_segments(tmp_path):
    """章节名带 / 或反斜杠：不再假层级/炸导入，清洗为 _ 后落位。"""
    rows = [_row(1, f"{PKG} > 备份/恢复 > 场景A\\B > h1"),
            _row(2, f"{PKG} > 备份/恢复 > 场景A\\B > h2")]
    svc, repo, *_ = _service(tmp_path, client=_fake_client(rows))
    rec = await svc.start_import(domain="d1", source_id="DOC1",
                                 selection=Selection(), actor_id="a", username="a")
    final = await _wait_terminal(repo, rec["id"])
    assert final["status"] == "done"
    assert svc._folders.paths == [f"UDG 手册 [DOC1]/{PKG}/备份_恢复"]


async def test_sanitized_collision_deduped_by_part_anchor(tmp_path):
    # β 树内同目录同名即同节点，不会撞；碰撞源是安全化/截断——
    # 两个长标题共享 80 字符前缀（file_title 不同、sanitize 后同名同目录）
    long_a = "规" * 90 + "甲"
    long_b = "规" * 90 + "乙"
    rows = [
        _row(1, f"{PKG} > P > {long_a} > h1"),
        _row(2, f"{PKG} > P > {long_b} > h1"),
    ]
    svc, repo, *_ = _service(tmp_path, client=_fake_client(rows))
    rec = await svc.start_import(domain="d1", source_id="DOC1",
                                 selection=Selection(), actor_id="a", username="a")
    await _wait_terminal(repo, rec["id"])
    names = sorted(d["document_name"] for d in svc._kbdb.docs.values())
    assert len(names) == 2 and len(set(names)) == 2
    assert any("__p" in n for n in names)
