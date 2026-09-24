# -*- coding: utf-8 -*-
"""管理面 API 路由测试（TestClient + 依赖覆盖，无 PG/内网）.

覆盖：admin 守卫 / 未配置 503 / search 校验 / toc 缓存 / imports 202+409 /
详情含 documents。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

# kb 测试套件同款：Windows 下 psycopg 需要 Selector loop
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_mining.mining.onenet import routes as onenet_routes
from knowledge_mining.mining.onenet.config import OnenetConfig


ADMIN = {"id": "u-admin", "username": "admin", "site_role": "admin"}
MEMBER = {"id": "u-member", "username": "member", "site_role": "member"}

CFG = OnenetConfig(app_id="a", static_token="s")


class FakeRepo:
    def __init__(self):
        self.imports: dict[str, dict] = {}
        self.deleted: list[str] = []
        self.toc: dict[tuple, dict] = {}

    async def get_toc_cache(self, domain, source_id):
        return self.toc.get((domain, source_id))

    async def put_toc_cache(self, **kw):
        self.toc[(kw["domain"], kw["source_id"])] = {
            "domain": kw["domain"], "source_id": kw["source_id"],
            "parsed_version_seen": kw["parsed_version"],
            "toc_json": kw["toc"], "scanned_at": "2026-09-14T00:00:00Z",
        }

    async def find_import_by_source(self, domain, source_id):
        for r in self.imports.values():
            if r.get("domain") == domain and r.get("source_id") == source_id:
                return dict(r)
        return None

    async def list_imports(self, *, domain):
        return [dict(r) for r in self.imports.values() if r["domain"] == domain]

    async def get_import(self, import_id):
        row = self.imports.get(import_id)
        if row is None:
            return None
        base = {
            "id": import_id, "domain": "cloud_core_network", "source_id": "DOC1",
            "kb_id": "kb-pub", "status": "done", "document_count": 2,
            "selection_json": {"subtrees": [], "max_part_id": None},
            "created_at": "t", "updated_at": "t", "created_by": "u-admin",
            "doc_name": "UDG", "parsed_version_seen": "v1",
            "total_slices": 3, "fetched_max_part_id": 3,
            "folder_root_path": None, "error": None,
        }
        base.update(row)
        return base

    async def insert_import(self, **kw):
        row = {"id": "imp-1", "status": "queued", **kw}
        self.imports[row["id"]] = row
        return dict(row)

    async def delete_import(self, import_id):
        self.imports.pop(import_id, None)
        self.deleted.append(import_id)

    async def update_import(self, import_id, **fields):
        self.imports[import_id].update(fields)

    async def find_public_kb(self, domain):
        return {"id": "kb-pub", "domain": domain, "name": "一张网产品文档"}


class FakeScanClient:
    """scan/probe 走的最小客户端."""

    def count_source(self, sid):
        return 3

    def part_range(self, sid):
        return {"min": 1, "max": 3}

    def probe_source(self, sid):
        return {"source_id": sid, "total_slices": 3, "part_id": {"min": 1, "max": 3},
                "doc_name": "UDG 手册", "file_name": "UDG.hwics",
                "doc_type": ["hwics"], "parsed_version": "hwics_v1.0",
                "publish_time": "2026-07-25", "product_line": ["云核心网"],
                "pbi": ["x"], "sample_slices": []}

    def query_native(self, conditions=None, **kw):
        return {"total": 1, "searchResults": [{
            "source_id": "DOC1", "doc_name": "UDG 手册", "file_name": "UDG.hwics",
            "doc_type": ["hwics"], "parsed_version": "hwics_v1.0",
            "publish_time": "2026-07-25", "product_line": ["云核心网"],
            "pbi": ["x"]}]}

    def fetch_source_chunk(self, sid, lo, hi, page_size=1000, fields=None):
        return [
            {"path": "Pkg > A > B", "title": "B", "part_id": 1,
             "parsed_version": "hwics_v1.0"},
            {"path": "Pkg > A > B", "title": "B", "part_id": 2,
             "parsed_version": "hwics_v1.0"},
            {"path": "Pkg > D > E", "title": "E", "part_id": 3,
             "parsed_version": "hwics_v1.0"},
        ]

    def close(self):
        pass  # 路由层 finally 关闭客户端（安全审查 L-3）


class FakeImportService:
    def __init__(self, repo):
        self._repo = repo

    async def start_import(self, **kw):
        if await self._repo.find_import_by_source(kw["domain"], kw["source_id"]):
            from knowledge_mining.mining.onenet.import_service import (
                OnenetImportError,
            )
            raise OnenetImportError("duplicate: 已有导入记录")
        kw.setdefault("kb_id", "kb-pub")  # 真实服务经 ensure_public_kb 得到
        return await self._repo.insert_import(**kw)


def _client(monkeypatch, *, configured=True):
    app = FastAPI()
    app.include_router(onenet_routes.router)
    repo = FakeRepo()
    state = SimpleNamespace(pg_pool=None)
    app.state.pg_pool = None

    async def fake_admin():
        return ADMIN

    async def fake_current():
        return ADMIN

    app.dependency_overrides[onenet_routes.require_admin] = fake_admin
    app.dependency_overrides[onenet_routes.current_user] = fake_current

    if configured:
        monkeypatch.setattr(onenet_routes, "resolve_config", lambda: CFG)
    else:
        monkeypatch.setattr(onenet_routes, "resolve_config", lambda: None)

    monkeypatch.setattr(
        onenet_routes, "_repo", lambda request: repo)
    if configured:
        monkeypatch.setattr(
            onenet_routes, "_client_factory",
            lambda request: (lambda: FakeScanClient()))
        async def fake_import_service(request):
            return FakeImportService(repo)
        monkeypatch.setattr(
            onenet_routes, "_import_service", fake_import_service)

    # KbDB.list_documents_by_key_prefix 打桩（详情端点用）
    class FakeKbDb:
        async def list_documents_by_key_prefix(self, kb_id, prefix, *, limit=5000):
            return [{"id": "d1", "document_name": "A.jsonl",
                     "directory_path": None, "status": "uploaded", "file_size": 10}]

    import knowledge_mining.mining.onenet.routes as r
    monkeypatch.setattr(r, "KbDB", lambda pool: FakeKbDb())

    return TestClient(app), repo


# ---------------------------------------------------------------- 装配回归


def test_import_service_wiring_awaits_document_service(monkeypatch):
    """内网实测 bug 回归：get_document_service 是 async def，routes 装配时
    必须 await——裸协程注入会让首个 store_source_bytes 炸 AttributeError。"""
    import asyncio
    from types import SimpleNamespace as NS

    import knowledge_mining.mining.kb.deps as deps
    import knowledge_mining.mining.onenet.routes as r

    sentinel = NS(name="real-doc-service")
    async def fake_get_document_service(request):
        return sentinel
    monkeypatch.setattr(deps, "get_document_service", fake_get_document_service)

    async def call():
        request = NS(app=NS(state=NS(pg_pool=None)))
        return await r._import_service(request)

    svc = asyncio.run(call())
    assert svc._doc_service is sentinel          # 未 await 时这里是 coroutine
    assert not asyncio.iscoroutine(svc._doc_service)


# ---------------------------------------------------------------- 守卫/配置


def test_search_requires_filters(monkeypatch):
    c, _ = _client(monkeypatch)
    resp = c.post("/api/onenet/search", json={})
    assert resp.status_code == 422


def test_not_configured_returns_503(monkeypatch):
    c, _ = _client(monkeypatch, configured=False)
    resp = c.post("/api/onenet/search", json={"doc_name": "UDG"})
    assert resp.status_code == 503
    assert "not_configured" in resp.json()["detail"]


# ---------------------------------------------------------------- search/probe/toc


def test_search_triples_and_pagination(monkeypatch):
    c, _ = _client(monkeypatch)
    resp = c.post("/api/onenet/search", json={
        "conditions": [
            {"field": "source_site", "fuzzy": False, "content": "support"},
            {"field": "doc_name", "fuzzy": False, "content": "UDG"},
        ]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["documents"][0]["source_id"] == "DOC1"
    assert body["total_documents"] == 1
    assert "封顶" in body["notice"]
    # 非法字段 422 / 空条件 422
    assert c.post("/api/onenet/search", json={
        "conditions": [{"field": "hack", "fuzzy": False, "content": "x"}]}
    ).status_code == 422
    assert c.post("/api/onenet/search", json={"conditions": []}).status_code == 422


def test_probe_card(monkeypatch):
    c, _ = _client(monkeypatch)
    resp = c.post("/api/onenet/probe", json={"source_id": "DOC1"})
    assert resp.status_code == 200
    assert resp.json()["total_slices"] == 3
    assert "sample_slices" not in resp.json()


def test_toc_scan_then_cache(monkeypatch):
    c, repo = _client(monkeypatch)
    resp = c.post("/api/onenet/toc", json={
        "domain": "cloud_core_network", "source_id": "DOC1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is False
    assert body["total_slices"] == 3
    assert ("cloud_core_network", "DOC1") in repo.toc
    # 第二次：命中缓存
    resp2 = c.post("/api/onenet/toc", json={
        "domain": "cloud_core_network", "source_id": "DOC1"})
    assert resp2.json()["cached"] is True
    # GET 缓存读取
    resp3 = c.get("/api/onenet/toc/cloud_core_network/DOC1")
    assert resp3.status_code == 200
    assert resp3.json()["cached"] is True


def test_toc_returns_both_restore_previews(monkeypatch):
    c, _ = _client(monkeypatch)
    resp = c.post("/api/onenet/toc", json={
        "domain": "cloud_core_network", "source_id": "DOC1",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommended_restore_mode"] == "product_document"
    assert body["restore_previews"]["product_document"]["file_count"] == 2
    assert body["restore_previews"]["file_anchor"]["file_count"] == 0
    assert body["restore_previews"]["file_anchor"]["unassigned"] == 3


def test_toc_stale_rule_version_not_reused(monkeypatch):
    """beta-2 守卫：缓存树是旧还原规则（rule_version 不匹配）时必须重扫，
    旧树不得复活。"""
    c, repo = _client(monkeypatch)
    # 旧规则（beta-1）缓存：toc_json 带过期 rule_version
    repo.toc[("cloud_core_network", "DOC1")] = {
        "source_id": "DOC1", "parsed_version_seen": "v1",
        "toc_json": {"rule_version": "beta-1", "tree": [], "files": []},
        "scanned_at": "2026-09-14T00:00:00Z",
    }
    resp = c.post("/api/onenet/toc", json={
        "domain": "cloud_core_network", "source_id": "DOC1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is False            # 旧规则 → 重扫
    assert body["rule_version"] == "beta-5"   # 新树落新规则并覆盖缓存


def test_toc_missing_source_404(monkeypatch):
    c, _ = _client(monkeypatch)
    monkeypatch.setattr(
        "knowledge_mining.mining.onenet.routes.scan_toc",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("source 无切片: X")))
    resp = c.post("/api/onenet/toc", json={
        "domain": "cloud_core_network", "source_id": "DOC_NONE"})
    assert resp.status_code == 404


# ---------------------------------------------------------------- imports


def test_start_import_202_then_duplicate_409(monkeypatch):
    c, repo = _client(monkeypatch)
    resp = c.post("/api/onenet/imports", json={
        "domain": "cloud_core_network", "source_id": "DOC1"})
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    assert resp.json()["kb_id"] == "kb-pub"

    resp2 = c.post("/api/onenet/imports", json={
        "domain": "cloud_core_network", "source_id": "DOC1"})
    assert resp2.status_code == 409


def test_start_import_rejects_path_hint_that_does_not_match_raw_path(monkeypatch):
    c, _ = _client(monkeypatch)
    resp = c.post("/api/onenet/imports", json={
        "domain": "cloud_core_network", "source_id": "DOC1",
        "selection": {
            "subtrees": ["包 > 告警 > 处理建议"],
            "path_hints": {
                "包 > 告警 > 处理建议": ["包", "错误标题"],
            },
        },
    })
    assert resp.status_code == 422
    assert resp.json()["detail"] == "invalid selection"


def test_start_import_requires_current_toc_for_subtree_selection(monkeypatch):
    c, _ = _client(monkeypatch)
    resp = c.post("/api/onenet/imports", json={
        "domain": "cloud_core_network", "source_id": "DOC1",
        "selection": {"subtrees": ["Pkg > A > B"]},
    })
    assert resp.status_code == 409
    assert resp.json()["detail"] == "current toc scan required before subtree import"


def test_start_import_validates_path_hint_against_current_toc_tree(monkeypatch):
    c, repo = _client(monkeypatch)
    assert c.post("/api/onenet/toc", json={
        "domain": "cloud_core_network", "source_id": "DOC1",
    }).status_code == 200

    raw_path = "Pkg > A > B"
    wrong_but_flatten_equal = ["Pkg > A", "B"]
    resp = c.post("/api/onenet/imports", json={
        "domain": "cloud_core_network", "source_id": "DOC1",
        "selection": {
            "subtrees": [raw_path],
            "path_hints": {raw_path: wrong_but_flatten_equal},
        },
    })
    assert resp.status_code == 422
    assert resp.json()["detail"] == "path_hints do not match current toc"


def test_start_import_generates_authoritative_path_hint_from_toc(monkeypatch):
    c, repo = _client(monkeypatch)
    assert c.post("/api/onenet/toc", json={
        "domain": "cloud_core_network", "source_id": "DOC1",
    }).status_code == 200

    raw_path = "Pkg > A > B"
    resp = c.post("/api/onenet/imports", json={
        "domain": "cloud_core_network", "source_id": "DOC1",
        "selection": {"subtrees": [raw_path]},
    })
    assert resp.status_code == 202
    stored = repo.imports["imp-1"]["selection"]
    assert stored.path_hints_map == {raw_path: ("Pkg", "A", "B")}


def test_list_and_detail_imports(monkeypatch):
    c, repo = _client(monkeypatch)
    c.post("/api/onenet/imports", json={
        "domain": "cloud_core_network", "source_id": "DOC1"})
    resp = c.get("/api/onenet/imports", params={"domain": "cloud_core_network"})
    assert resp.status_code == 200
    assert len(resp.json()["imports"]) == 1

    detail = c.get("/api/onenet/imports/imp-1")
    assert detail.status_code == 200
    body = detail.json()
    assert body["documents"][0]["document_name"] == "A.jsonl"

    assert c.get("/api/onenet/imports/nope").status_code == 404

# ---------------------------------------------------------------- 删除（source 级）


def test_delete_import_source_level(monkeypatch):
    """source 删除走统一硬删管线（2026-09-16）：全量文档 id → PurgeService →
    目录子树 → 记录硬删。"""
    c, repo = _client(monkeypatch)
    repo.imports["imp-1"] = {"id": "imp-1", "domain": "cloud_core_network",
                             "source_id": "DOC1", "kb_id": "kb-pub",
                             "status": "done", "document_count": 2,
                             "doc_name": "UDG"}
    subtree: dict[str, object] = {}
    purged: dict[str, object] = {}

    class FakeKbDb2:
        async def list_document_ids_by_key_prefix(self, kb_id, key_prefix):
            assert key_prefix == "onenet:DOC1:"       # 全量 id，无 LIMIT
            return ["d1", "d2"]

        async def count_docs_under_path(self, *, kb_id, path):
            subtree["counted"] = path
            return 0                       # 硬删后活文档清零

        async def delete_folder_subtree(self, kb_id, path):
            subtree["deleted"] = path
            return 7

    import knowledge_mining.mining.onenet.routes as r
    monkeypatch.setattr(r, "KbDB", lambda pool: FakeKbDb2())

    import knowledge_mining.mining.kb.services.purge_service as ps

    class FakePurge:
        def _summary(self):
            return {"deleted_documents": [], "reclaimed_snapshots": 0,
                    "skipped_shared_snapshots": [], "reclaimed_objects": 0,
                    "skipped_shared_objects": []}

        async def purge_documents(self, kb_id, doc_ids, *, assert_kb=True):
            purged["kb"] = kb_id
            purged["ids"] = list(doc_ids)
            return {"deleted_documents": list(doc_ids),
                    "reclaimed_snapshots": 3, "skipped_shared_snapshots": [],
                    "reclaimed_objects": 2, "skipped_shared_objects": []}

    monkeypatch.setattr(ps, "PurgeService", lambda pool, store: FakePurge())

    resp = c.delete("/api/onenet/imports/imp-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted_documents"] == ["d1", "d2"]
    assert body["reclaimed_snapshots"] == 3
    assert body["reclaimed_objects"] == 2
    assert body["removed_folders"] == 7
    assert purged == {"kb": "kb-pub", "ids": ["d1", "d2"]}
    assert subtree["counted"] == "UDG [DOC1]"      # 顶层文档段
    assert subtree["deleted"] == "UDG [DOC1]"
    assert "imp-1" not in repo.imports             # 记录硬删
    assert repo.deleted == ["imp-1"]


def test_delete_import_busy_rejected(monkeypatch):
    c, repo = _client(monkeypatch)
    repo.imports["imp-1"] = {"id": "imp-1", "status": "fetching"}
    resp = c.delete("/api/onenet/imports/imp-1")
    assert resp.status_code == 409
    assert "import_busy" in resp.json()["detail"]
    assert "imp-1" in repo.imports               # 不动


def test_delete_import_not_found(monkeypatch):
    c, _ = _client(monkeypatch)
    assert c.delete("/api/onenet/imports/nope").status_code == 404
