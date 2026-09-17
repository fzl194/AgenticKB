"""51号批次2（Task 8）：mcp_tools 内部端点钥匙级收口（ASGITransport + PG 真库）。

- list-kbs / list-documents / begin-upload 三端点强校验 body 携带的
  username+key_id（_key_scope → 401 族）。
- 开放集从用户级 get_mcp_access 换成钥匙级 key_open_kb_ids。
- 上传票据绑定 key_id：吊销钥匙后未消费票据立即 404。
"""
from __future__ import annotations

import hashlib
import os
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.deps import get_document_service
from knowledge_mining.mining.kb.routes.mcp_tools import router as mcp_tools_router

BASE = "/api/kb/mcp-tools"
INTERNAL = {"X-Internal-Auth": os.environ.get("KB_TEST_INTERNAL_AUTH", "test-ivs")}


@pytest.fixture
def kbdb(async_pool):
    return KbDB(async_pool)


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


class _FakeDocSvc:
    """直传对照组用：intake_upload 免对象存储，返回最小 file 结果。"""

    async def intake_upload(self, *, kb_id, owner_id, filename, stream,
                            file_max_bytes):
        async for _ in stream:
            pass
        return {"kind": "file",
                "document": {"id": f"doc-{filename}", "document_name": filename}}


async def _client(async_pool, *, fake_doc_svc=True):
    app = FastAPI()
    app.state.pg_pool = async_pool
    app.include_router(mcp_tools_router)
    if fake_doc_svc:
        app.dependency_overrides[get_document_service] = lambda: _FakeDocSvc()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _mk_key(kbdb, user_id: str, *, kb_ids: list[str]) -> str:
    """同域（generic）active 钥匙 + 指定开放集 → key_id。"""
    key_id = uuid.uuid4().hex
    h = hashlib.sha256(f"tk-{key_id}".encode()).hexdigest()
    await kbdb.create_mcp_key(
        user_id=user_id, name=f"k-{key_id[:8]}", domain="generic",
        key_hash=h, key_prefix=h[:8], key_id=key_id,
    )
    await kbdb.replace_mcp_key_open_kbs(key_id=key_id, kb_ids=kb_ids)
    return key_id


async def _setup(kbdb, async_pool):
    """user（generic 域）+ kbA/kbB 两库 + keyA 开 kbA / keyB 开 kbB。"""
    s = _suffix()
    user = await kbdb.create_user(username=f"mtk_user_{s}", site_role="member")
    await kbdb.set_user_domains(user_id=user["id"], domains=["generic"])
    kb_a = await kbdb.create_kb(domain="generic", name=f"mtk-a-{s}",
                                owner_id=user["id"])
    kb_b = await kbdb.create_kb(domain="generic", name=f"mtk-b-{s}",
                                owner_id=user["id"])
    key_a = await _mk_key(kbdb, user["id"], kb_ids=[kb_a["id"]])
    key_b = await _mk_key(kbdb, user["id"], kb_ids=[kb_b["id"]])
    return user, kb_a, kb_b, key_a, key_b


# ------------------------------------------------------------ list-kbs / 开放集

@pytest.mark.asyncio
async def test_list_kbs_returns_only_key_open_set(async_pool, kbdb):
    user, kb_a, kb_b, key_a, key_b = await _setup(kbdb, async_pool)
    async with await _client(async_pool) as c:
        r = await c.post(f"{BASE}/list-kbs",
                         json={"username": user["username"], "key_id": key_a},
                         headers=INTERNAL)
        assert r.status_code == 200, r.text
        ids = [k["id"] for k in r.json()["knowledge_bases"]]
        assert ids == [kb_a["id"]]

        r = await c.post(f"{BASE}/list-kbs",
                         json={"username": user["username"], "key_id": key_b},
                         headers=INTERNAL)
        ids = [k["id"] for k in r.json()["knowledge_bases"]]
        assert ids == [kb_b["id"]]


# ------------------------------------------------------- _key_scope：401 族

@pytest.mark.asyncio
async def test_key_scope_rejects_bad_keys(async_pool, kbdb):
    user, kb_a, _kb_b, key_a, _key_b = await _setup(kbdb, async_pool)
    other = await kbdb.create_user(username=f"mtk_other_{_suffix()}",
                                   site_role="member")
    async with await _client(async_pool, fake_doc_svc=False) as c:
        for body in (
            {"username": user["username"], "key_id": "no_such_key"},   # 不存在
            {"username": user["username"], "key_id": ""},              # 缺失
            {"username": user["username"]},                            # 整体缺字段
            {"username": other["username"], "key_id": key_a},          # 他人钥匙
        ):
            r = await c.post(f"{BASE}/list-kbs", json=body, headers=INTERNAL)
            assert r.status_code == 401, (body, r.text)

        # revoked 钥匙 → 401
        await kbdb.revoke_mcp_key(key_id=key_a)
        r = await c.post(f"{BASE}/list-kbs",
                         json={"username": user["username"], "key_id": key_a},
                         headers=INTERNAL)
        assert r.status_code == 401, r.text
    assert kb_a["id"]  # keep reference


# ------------------------------------------------ begin-upload：越开放集 404

@pytest.mark.asyncio
async def test_begin_upload_outside_key_open_set_404(async_pool, kbdb):
    user, _kb_a, kb_b, key_a, _key_b = await _setup(kbdb, async_pool)
    async with await _client(async_pool) as c:
        # kbB 同域、owner 可见可写——但不在 keyA 开放集 → 404
        r = await c.post(f"{BASE}/begin-upload",
                         json={"username": user["username"], "key_id": key_a,
                               "kb_id": kb_b["id"], "filename": "a.txt"},
                         headers=INTERNAL)
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == f"knowledge base not found: {kb_b['id']}"
        # 在开放集内 → 正常签发票据
        kb_id = (await kbdb.get_kb(_kb_a["id"]))["id"]
        r = await c.post(f"{BASE}/begin-upload",
                         json={"username": user["username"], "key_id": key_a,
                               "kb_id": kb_id, "filename": "a.txt"},
                         headers=INTERNAL)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ticket"].startswith("up_")
        assert body["upload_path"].endswith(body["ticket"])
        assert body["max_bytes"] > 0 and body["expires_in"] > 0


# --------------------------------------------------------- list-documents

@pytest.mark.asyncio
async def test_list_documents_open_set_gate(async_pool, kbdb):
    user, kb_a, kb_b, key_a, _key_b = await _setup(kbdb, async_pool)
    async with await _client(async_pool, fake_doc_svc=False) as c:
        # keyB 不开 kbA → 404（同文案防探测）
        r = await c.post(f"{BASE}/list-documents",
                         json={"username": user["username"],
                               "key_id": _key_b, "kb_id": kb_a["id"]},
                         headers=INTERNAL)
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == f"knowledge base not found: {kb_a['id']}"
        # keyA 开 kbA → 200
        r = await c.post(f"{BASE}/list-documents",
                         json={"username": user["username"],
                               "key_id": key_a, "kb_id": kb_a["id"]},
                         headers=INTERNAL)
        assert r.status_code == 200, r.text
        assert "documents" in r.json()
    assert kb_b["id"]


# ------------------------------------------------- 票据绑钥匙：吊销即失效

@pytest.mark.asyncio
async def test_upload_ticket_dies_with_revoked_key(async_pool, kbdb):
    user, kb_a, _kb_b, key_a, _key_b = await _setup(kbdb, async_pool)
    async with await _client(async_pool) as c:
        r = await c.post(f"{BASE}/begin-upload",
                         json={"username": user["username"], "key_id": key_a,
                               "kb_id": kb_a["id"], "filename": "f.txt"},
                         headers=INTERNAL)
        assert r.status_code == 200, r.text
        ticket = r.json()["ticket"]
        # 吊销钥匙 → 未消费票据立即失效（与无效票据同语义 404）
        assert await kbdb.revoke_mcp_key(key_id=key_a) is True
        r = await c.put(f"{BASE}/upload-direct/{ticket}", content=b"data",
                        headers=INTERNAL)
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "upload ticket invalid or expired"


@pytest.mark.asyncio
async def test_upload_direct_with_active_key_still_works(async_pool, kbdb):
    """对照组：钥匙存活时直传照旧可用（intake 以 fake service 注入）。"""
    user, kb_a, _kb_b, key_a, _key_b = await _setup(kbdb, async_pool)
    async with await _client(async_pool) as c:
        r = await c.post(f"{BASE}/begin-upload",
                         json={"username": user["username"], "key_id": key_a,
                               "kb_id": kb_a["id"], "filename": "ok.txt"},
                         headers=INTERNAL)
        assert r.status_code == 200, r.text
        ticket = r.json()["ticket"]
        r = await c.put(f"{BASE}/upload-direct/{ticket}",
                        content=b"hello world", headers=INTERNAL)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "file"
        assert body["document_name"] == "ok.txt"
