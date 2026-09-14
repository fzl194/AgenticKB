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
        monkeypatch.setattr(
            onenet_routes, "_import_service",
            lambda request: FakeImportService(repo))

    # KbDB.list_documents_by_key_prefix 打桩（详情端点用）
    class FakeKbDb:
        async def list_documents_by_key_prefix(self, kb_id, prefix, *, limit=5000):
            return [{"id": "d1", "document_name": "A.jsonl",
                     "directory_path": None, "status": "uploaded", "file_size": 10}]

    import knowledge_mining.mining.onenet.routes as r
    monkeypatch.setattr(r, "KbDB", lambda pool: FakeKbDb())

    return TestClient(app), repo


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


def test_search_returns_documents_with_capped_notice(monkeypatch):
    c, _ = _client(monkeypatch)
    resp = c.post("/api/onenet/search", json={"doc_name": "UDG"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["documents"][0]["source_id"] == "DOC1"
    assert "封顶" in body["notice"]


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
        "domain": "cloud_core_network", "source_id": "DOC1",
        "selection": {"subtrees": ["A"]}})
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    assert resp.json()["kb_id"] == "kb-pub"

    resp2 = c.post("/api/onenet/imports", json={
        "domain": "cloud_core_network", "source_id": "DOC1"})
    assert resp2.status_code == 409


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
