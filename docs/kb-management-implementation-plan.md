# 知识库管理实现计划（mining 侧：P1–P4 + P6）

> **For agentic workers:** REQUIRED: 用 `superpowers:subagent-driven-development`（若有 subagent）或 `superpowers:executing-plans` 执行本计划。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 在 knowledge_mining 里实现「知识库管理」模块——KB/文档 CRUD、上传/解压、显式触发挖掘、权限——让用户以云端文件管理器的方式管理知识库，文档作为稳定实体归属 KB，挖掘退化为文档上的运行态动作。

**Architecture:** 新 `mining/kb/` package 嵌入 knowledge_mining（FastAPI）。KB 独占 `asset_documents` 写（身份+文件位置）；mining db_write 砍掉 `upsert_document`，对 `asset_documents` 零写；文档状态读时派生（不存 status 列）。详细见 `docs/kb-management-design.md` v0.2。

**Tech Stack:** Python 3 / FastAPI / PostgreSQL(psycopg) / pytest。**测试强绑 PG**（`knowledge_mining/tests/conftest.py` autouse session `_ensure_schema`），跑前确保 `.env` 指向可用 PG；隔离用 `KB_ALLOW_TEST_TRUNCATE=1`。

**范围说明:** 本计划只覆盖 mining 侧（P1–P4 + P6）。serving 侧 P5（resolveActiveScope 加 kb_ids / scope_resolve / SourceRef，Java）是独立 Plan 2，仅依赖本计划 P1 交付的 `asset_documents.kb_id` 列。

**分支:** `feat/kb-management`（已建）。所有提交落此分支，每个 Task 一个 commit。

---

## 文件结构（创建/修改清单）

**新建（DDL）：**
- `databases/kb/schemas/001_kb_users.sql` — 用户表
- `databases/kb/schemas/002_knowledge_bases.sql` — KB 表（含软删）
- `databases/kb/schemas/003_kb_members.sql` — shared 成员表
- `databases/asset_core/schemas/004_kb_isolation.sql` — asset_documents 加列 + UNIQUE 改造

**新建（KB package）：**
- `knowledge_mining/mining/kb/__init__.py`
- `knowledge_mining/mining/kb/db.py` — kb_* 表的查询（Repository）
- `knowledge_mining/mining/kb/auth.py` — X-KB-User 头部注入
- `knowledge_mining/mining/kb/storage.py` — 落盘路径策略
- `knowledge_mining/mining/kb/services/__init__.py`
- `knowledge_mining/mining/kb/services/kb_service.py` — KB CRUD + 可见性 + 软删
- `knowledge_mining/mining/kb/services/document_service.py` — 文件 CRUD + 上传/解压 + 状态派生
- `knowledge_mining/mining/kb/services/mining_trigger.py` — 触发挖掘
- `knowledge_mining/mining/kb/routes/__init__.py`
- `knowledge_mining/mining/kb/routes/kbs.py` — /api/kb CRUD + members
- `knowledge_mining/mining/kb/routes/documents.py` — /api/kb/{id}/documents
- `knowledge_mining/mining/kb/routes/mining.py` — /api/kb/{id}/mine

**修改：**
- `knowledge_mining/mining/infra/pg_schema.py` — 注册 4 个新 DDL 到 `ddl_paths`
- `knowledge_mining/mining/snapshot/__init__.py` — `select_or_create_snapshot` 砍 `upsert_document`
- `knowledge_mining/mining/api/app.py` — 注册 kb 路由
- `knowledge_mining/mining/api/routes/runs.py` — 旧 `/api/runs` 标 deprecated（注释 + 响应头）

**测试（新建）：**
- `knowledge_mining/tests/kb/__init__.py`
- `knowledge_mining/tests/kb/test_pg_schema_kb.py` — DDL 加载
- `knowledge_mining/tests/kb/test_kb_service.py` — KB CRUD + 可见性 + 软删
- `knowledge_mining/tests/kb/test_document_service.py` — 上传/zip/CRUD/状态派生
- `knowledge_mining/tests/kb/test_mining_trigger.py` — 触发 + db_write 不建文档身份
- `knowledge_mining/tests/kb/test_routes_kb.py` — API 端到端

---

## Chunk 1: P1 数据层

### Task 1.1: kb 三张表 DDL

**Files:**
- Create: `databases/kb/schemas/001_kb_users.sql`
- Create: `databases/kb/schemas/002_knowledge_bases.sql`
- Create: `databases/kb/schemas/003_kb_members.sql`

DDL 内容**逐字**取自设计文档 `docs/kb-management-design.md` §1.1（kb_users / knowledge_bases 带 `status('active','deleted')` + `deleted_at` / kb_members）。

- [ ] **Step 1: 建 001_kb_users.sql**（设计 §1.1 第一段）
- [ ] **Step 2: 建 002_knowledge_bases.sql**（含 `status CHECK('active','deleted')`、`deleted_at`、两个索引）
- [ ] **Step 3: 建 003_kb_members.sql**（PK(kb_id,user_id) + idx_kb_members_user）
- [ ] **Step 4: Commit**
```bash
git add databases/kb/
git commit -m "feat(kb): kb_users/knowledge_bases/kb_members DDL"
```

### Task 1.2: asset_documents 加列 + UNIQUE 改造

**Files:**
- Create: `databases/asset_core/schemas/004_kb_isolation.sql`
- 内容**逐字**取自设计 §1.2（`ADD COLUMN kb_id ... ON DELETE RESTRICT` / `storage_path` / `directory_path` / `owner_id`；`DROP CONSTRAINT asset_documents_domain_document_key_key`；`ADD CONSTRAINT uq_asset_documents_kb_key UNIQUE(kb_id, document_key)`；`CREATE INDEX idx_asset_documents_kb_id`）。

- [ ] **Step 1: 建 004_kb_isolation.sql**
- [ ] **Step 2: Commit**
```bash
git add databases/asset_core/schemas/004_kb_isolation.sql
git commit -m "feat(kb): asset_documents 加 kb_id/存储/目录/owner + UNIQUE(kb_id,document_key)"
```

### Task 1.3: pg_schema.py 注册新 DDL

**Files:**
- Modify: `knowledge_mining/mining/infra/pg_schema.py:18-24`（常量）、`:81-92`（ddl_paths 元组 + transactional 集）

- [ ] **Step 1: 写失败测试** — `knowledge_mining/tests/kb/test_pg_schema_kb.py`
```python
"""验证 kb DDL 被 pg_schema 加载（需 PG）。"""
import pytest
from knowledge_mining.mining.infra import pg_schema
from knowledge_mining.mining.infra.db import AssetCoreDB

pytestmark = pytest.mark.skipif(
    True,  # 见 conftest；PG 可用时改 False 或用 marker
    reason="需 PG，按 .env 连接；KB_ALLOW_TEST_TRUNCATE=1 隔离",
)

def test_kb_tables_exist(any_db_conn):  # fixture 见 conftest
    cur = any_db_conn.execute(
        "SELECT to_regclass('kb_users'), to_regclass('knowledge_bases'), to_regclass('kb_members')"
    )
    assert cur.fetchone() == ('kb_users', 'knowledge_bases', 'kb_members')

def test_asset_documents_has_kb_id(any_db_conn):
    cur = any_db_conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='asset_documents' AND column_name IN ('kb_id','storage_path','directory_path','owner_id')"
    )
    names = {r[0] for r in cur.fetchall()}
    assert names == {'kb_id', 'storage_path', 'directory_path', 'owner_id'}
```

- [ ] **Step 2: 跑测试，确认 FAIL**（表不存在）
```bash
python -m pytest knowledge_mining/tests/kb/test_pg_schema_kb.py -v
```

- [ ] **Step 3: 改 pg_schema.py** — 加常量 + 插元组 + transactional
```python
# line 18-24 之后追加
_KB_USERS_DDL     = _REPO_ROOT / "databases" / "kb" / "schemas" / "001_kb_users.sql"
_KB_BASES_DDL     = _REPO_ROOT / "databases" / "kb" / "schemas" / "002_knowledge_bases.sql"
_KB_MEMBERS_DDL   = _REPO_ROOT / "databases" / "kb" / "schemas" / "003_kb_members.sql"
_KB_ISOLATION_DDL = _REPO_ROOT / "databases" / "asset_core" / "schemas" / "004_kb_isolation.sql"
```
```python
# line 81-84 改为（顺序：asset → kb 三表 → kb_isolation → runtime → asset_domain → ontology）
ddl_paths = (
    _ASSET_DDL,
    _KB_USERS_DDL, _KB_BASES_DDL, _KB_MEMBERS_DDL,
    _KB_ISOLATION_DDL,   # ALTER 引用 knowledge_bases，必须在 kb 三表之后
    _RUNTIME_DDL, _RUNTIME_DDL_V3, _RUNTIME_DDL_V4,
    _ASSET_DOMAIN_DDL,
    _ONTOLOGY_DDL,
)
# line 90 transactional 集追加 _KB_ISOLATION_DDL（DROP+ADD CONSTRAINT 需原子）
transactional=ddl_path in (_RUNTIME_DDL_V4, _ASSET_DOMAIN_DDL, _KB_ISOLATION_DDL),
```
> ⚠️ 幂等性：`ADD CONSTRAINT uq_asset_documents_kb_key` 不带 IF NOT EXISTS（PG 不支持），重跑会抛 `DuplicateObject`，已被 `_execute_ddl` 捕获（line 127-132）。若实测异常类型不在捕获集，扩展 except 元组或用 `DO $$ ... $$` 守卫。

- [ ] **Step 4: 重置 schema 验证 + 跑测试 PASS**
```bash
python reset_db.py     # 破坏性重建，确认 DDL 按序加载
python -m pytest knowledge_mining/tests/kb/test_pg_schema_kb.py -v
```

- [ ] **Step 5: Commit**
```bash
git add knowledge_mining/mining/infra/pg_schema.py knowledge_mining/tests/kb/
git commit -m "feat(kb): pg_schema 注册 kb DDL（4 个，含 isolation transactional）"
```

---

## Chunk 2: P2 KB package 骨架

### Task 2.1: package 骨架 + db.py（kb_* Repository）

**Files:**
- Create: `knowledge_mining/mining/kb/__init__.py`（空）
- Create: `knowledge_mining/mining/kb/db.py`

- [ ] **Step 1: 写失败测试** — `tests/kb/test_kb_db.py`
```python
def test_upsert_and_get_user(db_conn):  # fixture 给一个 psycopg conn
    from knowledge_mining.mining.kb.db import KbDB
    kbdb = KbDB(db_conn)
    u = kbdb.upsert_user_by_username("alice", display_name="Alice")
    assert u.username == "alice"
    again = kbdb.upsert_user_by_username("alice")  # 幂等
    assert again.id == u.id
```

- [ ] **Step 2: 跑测试 FAIL**（KbDB 不存在）

- [ ] **Step 3: 实现 `kb/db.py`** — KbDB 类，方法：`upsert_user_by_username` / `create_kb` / `get_kb` / `list_kbs_visible(user_id, domain)` / `update_kb` / `soft_delete_kb` / `add_member` / `list_members` / `is_visible(kb_id, user_id)`。每方法一条 SQL，参数化（防注入），返回 dataclass / dict。参考现有 `knowledge_mining/mining/infra/db.py:AssetCoreDB` 的风格（`@dataclass` 行 + `cursor.execute`）。

```python
from __future__ import annotations
from dataclasses import dataclass
from psycopg import Cursor

@dataclass(frozen=True)
class KbUser:
    id: str; username: str; display_name: str | None; status: str

class KbDB:
    def __init__(self, conn): self._conn = conn  # 复用 AssetCoreDB 的 conn 模式

    def upsert_user_by_username(self, username: str, *, display_name: str | None = None) -> KbUser:
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kb_users (id, username, display_name, created_at)
                   VALUES (%(id)s, %(u)s, %(d)s, now())
                   ON CONFLICT (username) DO UPDATE SET display_name = COALESCE(%(d)s, kb_users.display_name)
                   RETURNING id, username, display_name, status""",
                {"id": _uuid(), "u": username, "d": display_name},
            )
            r = cur.fetchone()
            return KbUser(*r)
    # ... 其余方法同理
```

- [ ] **Step 4: 跑测试 PASS**
- [ ] **Step 5: Commit**
```bash
git add knowledge_mining/mining/kb/__init__.py knowledge_mining/mining/kb/db.py knowledge_mining/tests/kb/test_kb_db.py
git commit -m "feat(kb): KbDB repository（kb_users/knowledge_bases/kb_members）"
```

### Task 2.2: auth.py（X-KB-User 头部注入）

**Files:**
- Create: `knowledge_mining/mining/kb/auth.py`

- [ ] **Step 1: 写失败测试** — `tests/kb/test_auth.py`（用 FastAPI TestClient，注入 header）
```python
def test_header_injects_user(client):  # fixture: FastAPI app with kb router
    r = client.get("/api/kb", headers={"X-KB-User": "alice"})
    assert r.status_code == 200
    # 副作用：alice 在 kb_users 里
```

- [ ] **Step 2: 跑测试 FAIL**

- [ ] **Step 3: 实现 `kb/auth.py`** — FastAPI dependency：从 `request.headers["X-KB-User"]` 取 username，`KbDB.upsert_user_by_username`，返回 `KbUser`；缺失头部 401。
```python
from fastapi import Request, HTTPException
def current_user(request: Request) -> KbUser:
    username = request.headers.get("X-KB-User")
    if not username:
        raise HTTPException(401, "missing X-KB-User header")
    return KbDB(request.app.state.db_conn).upsert_user_by_username(username)
```
> Phase 1 内网白名单前提下可信（设计 §6.2 + §8 安全取舍）。Phase 2 换真实 auth 只改此处。

- [ ] **Step 4: 跑测试 PASS** | **Step 5: Commit**
```bash
git commit -m "feat(kb): auth Phase 1 — X-KB-User 头部注入"
```

### Task 2.3: kb_service.py（KB CRUD + 可见性 + 软删）

**Files:**
- Create: `knowledge_mining/mining/kb/services/kb_service.py`

- [ ] **Step 1: 写失败测试** — `tests/kb/test_kb_service.py`
```python
def test_create_and_list_visible(svc, alice, bob):
    kb_a = svc.create_kb(owner=alice.id, domain="cloud_core_network", name="KB-A", visibility="private")
    svc.create_kb(owner=bob.id, domain="cloud_core_network", name="KB-B", visibility="public")
    visible_to_alice = svc.list_visible(user_id=alice.id, domain="cloud_core_network")
    names = {k.name for k in visible_to_alice}
    assert names == {"KB-A", "KB-B"}  # 自己的 private + 人的 public

def test_private_invisible_to_other(svc, alice, bob):
    svc.create_kb(owner=alice.id, domain="cloud_core_network", name="private", visibility="private")
    assert all(k.name != "private" for k in svc.list_visible(user_id=bob.id, domain="cloud_core_network"))

def test_soft_delete_hides_but_keeps(svc, alice):
    kb = svc.create_kb(owner=alice.id, domain="cloud_core_network", name="K", visibility="private")
    svc.soft_delete(kb.id, actor=alice.id)
    assert kb.id not in {k.id for k in svc.list_visible(user_id=alice.id, domain="cloud_core_network")}
    assert svc.get_kb(kb.id, include_deleted=True).status == "deleted"  # 行还在
```

- [ ] **Step 2: 跑测试 FAIL**

- [ ] **Step 3: 实现 `kb_service.py`** — `KbService(KbDB)`：`create_kb`（校验 domain 在 registry 合法域 + UNIQUE(domain,name) 冲突 409）/ `list_visible`（SQL: owner OR member OR visibility='public'，全部 `status='active'`）/ `get_kb` / `update_kb` / `soft_delete_kb`（`status='deleted', deleted_at=now`）/ `add_member` / `assert_can_write(kb_id, user_id)`。domain 合法集从 `main_control_service/config/domain_registry.yaml` 读（复用现有 domain 校验逻辑，见 `mining/api/routes/uploads.py:23`）。

- [ ] **Step 4: 跑测试 PASS** | **Step 5: Commit**
```bash
git commit -m "feat(kb): KbService — CRUD + 可见性三档 + 软删"
```

### Task 2.4: routes/kbs.py + 注册

**Files:**
- Create: `knowledge_mining/mining/kb/routes/__init__.py`、`kbs.py`
- Modify: `knowledge_mining/mining/api/app.py`（注册 kb 路由）

- [ ] **Step 1: 写失败测试** — `tests/kb/test_routes_kb.py`（TestClient 走 /api/kb 全流程）
- [ ] **Step 2: 跑测试 FAIL**
- [ ] **Step 3: 实现 `routes/kbs.py`** — `APIRouter`，端点见设计 §2.2（POST/GET/GET{id}/PATCH{id}/DELETE{id} + members）。每个端点 `Depends(current_user)`，调 `KbService`。返回 envelope（参考现有 routes 的 response 模式）。
- [ ] **Step 4: 在 `api/app.py` 注册 router**（仿现有 uploads/runs/knowledge 路由的 include 模式）。
- [ ] **Step 5: 跑测试 PASS**（端到端：建 KB / 列 / 改 / 软删 / 加成员）
- [ ] **Step 6: Commit**
```bash
git commit -m "feat(kb): /api/kb 路由（CRUD + members）"
```

---

## Chunk 3: P3 文件管理

### Task 3.1: storage.py（落盘路径策略）

**Files:**
- Create: `knowledge_mining/mining/kb/storage.py`

- [ ] **Step 1: 写失败测试** — 路径生成纯函数，测 `{upload_root}/{kb_id}/{directory_path}/{filename}` 组装 + 目录穿越防护（`..` 被拒）。
- [ ] **Step 2: FAIL** | **Step 3: 实现** `storage_path(upload_root, kb_id, directory_path, filename) -> Path`，拒绝绝对路径 / `..` / 空段。
- [ ] **Step 4: PASS** | **Step 5: Commit**
```bash
git commit -m "feat(kb): storage 路径策略 + 目录穿越防护"
```

### Task 3.2: document_service 上传 + zip 解压

**Files:**
- Create: `knowledge_mining/mining/kb/services/document_service.py`

- [ ] **Step 1: 写失败测试** — `tests/kb/test_document_service.py`
```python
def test_upload_creates_document_and_file(svc, kb, tmp_path):
    doc = svc.upload(kb_id=kb.id, filename="qos.pdf", content=b"...",
                     directory_path="5G规范/AMF", owner=kb.owner_id)
    assert (tmp_path / "5G规范/AMF" / "qos.pdf").exists()
    assert doc.kb_id == kb.id and doc.document_key == "5G规范/AMF/qos.pdf"
    assert doc.status_derived == "uploaded"  # 无 mining_run_document
    # asset_documents 行存在，无 snapshot
    assert svc._asset_db.get_snapshot_by_doc(doc.id) is None

def test_upload_zip_extracts(tmp_path, svc, kb):
    zbytes = make_zip({"sub/a.txt": b"x", "sub/b.txt": b"y"})
    docs = svc.upload_zip(kb_id=kb.id, zip_bytes=zbytes, owner=kb.owner_id)
    assert {d.directory_path for d in docs} == {"sub"}
    assert {d.document_name for d in docs} == {"a.txt", "b.txt"}
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: 实现 `document_service.upload`** — 流程见设计 §2.3：
  1. 落盘（`storage.storage_path`）。
  2. `document_key = f"{directory_path}/{filename}".strip("/")`。
  3. INSERT `asset_documents`（id / domain（从 KB 取）/ kb_id / document_key / document_name / document_type（按 mime/mimetypes 库）/ storage_path / directory_path / owner_id / metadata_json）。**不计算 hash、不建 snapshot**。INSERT 走 `AssetCoreDB` 的连接但用 KB 包的 SQL（或扩展 AssetCoreDB 加 `insert_document_identity` 方法）。
  4. `upload_zip` 复用 `infra/archive_extractor.extract_archive`（uploads.py:137-160 在用），逐文件调 `upload`。

> 写方归属：`asset_documents` 由 KB package 写（设计铁律 1）。建议在 `AssetCoreDB` 加一个 `insert_document_identity(...)` 方法供 KB 包调用，保持 SQL 集中。

- [ ] **Step 4: PASS** | **Step 5: Commit**
```bash
git commit -m "feat(kb): 文档上传 + zip 解压（建 asset_documents 身份，不挖）"
```

### Task 3.3: 文档 CRUD + 下载 + 软撤回

**Files:**
- Modify: `knowledge_mining/mining/kb/services/document_service.py`

- [ ] **Step 1: 写失败测试** — `list_documents`（带 directory 过滤）/ `get_document` / `update_metadata`（PATCH document_name/metadata）/ `download`（返回 storage_path，复用现有 `resolve_managed_file` 的路径遍历防护 `document_lifecycle.py:53`）/ `withdraw`（软撤回，复用现有 `withdrawal.withdraw_document`）。
- [ ] **Step 2: FAIL** | **Step 3: 实现**（软撤回直接复用 `stages/withdrawal.py:withdraw_document`，它已做克隆 build + publish）。
- [ ] **Step 4: PASS** | **Step 5: Commit**
```bash
git commit -m "feat(kb): 文档 CRUD + 下载 + 软撤回"
```

### Task 3.4: 状态派生

**Files:**
- Modify: `knowledge_mining/mining/kb/services/document_service.py`

- [ ] **Step 1: 写失败测试** — 5 个派生状态（uploaded / mining / failed / published / withdrawn），每种造一个场景验证 `derive_status` 返回正确。
```python
def test_derive_published(svc, published_doc):
    assert svc.derive_status(published_doc.id) == "published"
def test_derive_mining(svc, mining_doc):
    assert svc.derive_status(mining_doc.id) == "mining"
# ... uploaded/failed/withdrawn 同理
```

- [ ] **Step 2: FAIL**

- [ ] **Step 3: 实现 `derive_status(doc_id)`** — 一条 JOIN（设计 §3.4）：取最新 `mining_run_documents.status` + 是否在 active release 的 `asset_build_document_snapshots`（selection_status）+ 是否在最新 build 里 selection_status='removed'。返回 5 值之一。

- [ ] **Step 4: PASS** | **Step 5: Commit**
```bash
git commit -m "feat(kb): 文档状态读时派生（5 态，单一真相源）"
```

### Task 3.5: routes/documents.py

**Files:**
- Create: `knowledge_mining/mining/kb/routes/documents.py` + 注册

- [ ] **Step 1-5: TDD** — 端点见设计 §2.2（POST upload / GET list / GET{id} / PATCH{id} / DELETE{id} / GET{id}/download）。`Depends(current_user)` + `assert_can_write`。
- [ ] **Step 6: Commit**
```bash
git commit -m "feat(kb): /api/kb/{id}/documents 路由"
```

---

## Chunk 4: P4 mining 适配

### Task 4.1: select_or_create_snapshot 砍 upsert_document

**Files:**
- Modify: `knowledge_mining/mining/snapshot/__init__.py:17`（`select_or_create_snapshot`）

- [ ] **Step 1: 写失败测试** — `tests/kb/test_mining_trigger.py`
```python
def test_mining_uses_precreated_document(trigger, svc, kb):
    # 1. KB 上传（预建 asset_documents）
    doc = svc.upload(kb_id=kb.id, filename="a.txt", content=b"hello world enough tokens", owner=kb.owner_id)
    doc_id_before = doc.id
    # 2. 触发挖掘
    run_id = trigger.mine(kb_id=kb.id, doc_ids=[doc.id])
    # 3. 等完成（或同步跑 pipeline）
    # 断言：asset_documents.id 不变（没被重建）；snapshot 建出来了
    assert svc.get_document(doc.id).id == doc_id_before
    assert svc._asset_db.get_snapshot_by_doc(doc.id) is not None
```

- [ ] **Step 2: 跑测试 FAIL**（当前 `select_or_create_snapshot` 会 upsert_document）

- [ ] **Step 3: 改 `select_or_create_snapshot`** — 见设计 §3.2。核心：删 `get_document_by_key` + `upsert_document` 两步；改为**按 `mining_run_documents.document_id` find existing document**（必存在，KB 预建），读 `storage_path` → parse → 算 hash → `get_snapshot_by_hash` 复用/建 → `insert_snapshot_link` → 写 segments/units。mining 对 `asset_documents` **零写**。

> 注意兼容：`mining_run_documents.document_id` 在旧 `/api/runs` 路径可能为空（NULL）。本 Task 只处理 KB 触发路径（document_id 非空）；旧路径的兼容在 Task 4.3 标 deprecated，实际仍走旧 upsert 分支（见下）。

- [ ] **Step 4: PASS** | **Step 5: Commit**
```bash
git commit -m "feat(kb): select_or_create_snapshot 改用预建文档（mining 对 asset_documents 零写）"
```

### Task 4.2: mining_trigger.py + /api/kb/{id}/mine

**Files:**
- Create: `knowledge_mining/mining/kb/services/mining_trigger.py`、`routes/mining.py`

- [ ] **Step 1: 写失败测试** — 触发建 `mining_run`（metadata 带 kb_id）+ 每文档 `mining_run_documents`（document_id ← asset_documents.id, action='NEW'）；域级 mutex 生效（并发触发同一 domain 第二次返回 409）。
- [ ] **Step 2: FAIL**
- [ ] **Step 3: 实现** — 复用 `jobs/run.py` 的 `run()` 入口 + `runs.py:90-96` 的 per-domain mutex。trigger 选文档（doc_ids 或 KB 下所有 uploaded）→ 建 run + mining_run_documents → 起后台线程跑 `run()`。路由 `POST /api/kb/{id}/mine`。
- [ ] **Step 4: PASS** | **Step 5: Commit**
```bash
git commit -m "feat(kb): 触发挖掘 /api/kb/{id}/mine（携 kb_id + per-domain mutex）"
```

### Task 4.3: 旧 /api/runs 标 deprecated

**Files:**
- Modify: `knowledge_mining/mining/api/routes/runs.py`

- [ ] **Step 1: 加 deprecation 标记** — `POST /api/runs` 响应头 `Deprecation: true` + `Sunset`（HTTP 7234）；docstring 注明「过渡期仍工作，建 kb_id=NULL 文档；新调用方走 /api/kb/{id}/mine；后续阶段删除」。
- [ ] **Step 2: 回归测试** — 现有 `tests/test_runs*.py` 仍通过（旧行为不变）。
- [ ] **Step 3: Commit**
```bash
git commit -m "chore(kb): 旧 /api/runs 标 deprecated（过渡期 kb_id=NULL 例外）"
```

---

## Chunk 5: P6 权限全链路 + 迁移

### Task 6.1: 可见性 enforcement 全链路

**Files:**
- Modify: `kb/services/kb_service.py`、`document_service.py`、各 route

- [ ] **Step 1: 写端到端权限测试** — `tests/kb/test_permissions.py`
```python
def test_ab_cannot_see_ac_private(svc, client, alice, bob, carol):
    kb_ac = svc.create_kb(owner=carol.id, domain="cloud_core_network", name="AC-private", visibility="private")
    # AB 视角看不到 AC-private
    assert kb_ac.id not in {k.id for k in svc.list_visible(alice.id, "cloud_core_network")}
    # AB 不能下载 AC-private 的文档
    doc = svc.upload(kb_ac.id, "x.txt", b"...", carol.id)
    r = client.get(f"/api/kb/{kb_ac.id}/documents/{doc.id}/download", headers={"X-KB-User":"alice"})
    assert r.status_code in (403, 404)
def test_shared_editor_can_upload(svc, alice, bob):
    kb = svc.create_kb(alice.id, "cloud_core_network", "shared-kb", visibility="shared")
    svc.add_member(kb.id, bob.id, role="editor")
    assert svc.assert_can_write(kb.id, bob.id) is None  # 不抛
```

- [ ] **Step 2: FAIL（如有缺口）** | **Step 3: 补 enforcement** — 每个 document route 调 `assert_can_write(kb_id, user_id)`；download/list 调 `assert_can_read`。private/shared/public 三档逻辑集中在 `KbService.is_visible`。
- [ ] **Step 4: PASS** | **Step 5: Commit**
```bash
git commit -m "feat(kb): 可见性 enforcement 全链路（read/write 分离）"
```

### Task 6.2: 存量 backfill 脚本（可选但推荐）

**Files:**
- Create: `scripts/backfill_default_kb.py`

- [ ] **Step 1: 实现** — 对每个 domain：建系统用户 `system`（若不存在）→ 建「默认 KB」（name=`default`，owner=system，visibility=private）→ `UPDATE asset_documents SET kb_id=<default_kb.id> WHERE kb_id IS NULL AND domain=<domain>`。
- [ ] **Step 2: 手动跑一次验证**（`python scripts/backfill_default_kb.py --dry-run`）
- [ ] **Step 3: Commit**
```bash
git commit -m "feat(kb): 存量文档 backfill 到 per-domain 默认 KB（可选）"
```

---

## 验收（对应需求 §9）

执行完本计划后应满足：
- [ ] US1–US3、US7 全流程：建 KB（含可见性）/ 上传（含 zip）/ 文件管理（CRUD+下载+软撤回）/ 成员管理。
- [ ] US4：`/api/kb/{id}/mine` 显式触发挖掘；mining 对 `asset_documents` 零写（新路径）。
- [ ] 数据归属不变量：KB 写 `asset_documents` + kb_*；mining 写其他 asset_*；mining 对 `asset_documents` 零写（新路径，旧 `/api/runs` 过渡期例外已标 deprecated）。
- [ ] 零重复挖掘：同 hash 文档共享 snapshot（沿用现有 SKIP/RESTORE，测试覆盖）。
- [ ] 向后兼容：存量 `kb_id=NULL` 文档仍可被「不传 kb_ids」检索（P5 serving 侧保证，本计划 P1 已给 NULL 兼容）。
- [ ] 多用户隔离：AB 看不到 AC 的 private KB（Task 6.1 测试）。

---

## 下一份计划（Plan 2: serving 侧 P5）

独立写，依赖本计划 Task 1.2（`asset_documents.kb_id`）。范围：
- `AssetRepository.resolveActiveScope` 加可选 `kb_ids` 参数（设计 §5）。
- `ScopeResolveOperator` 加 `kb_ids` 到 `PARAM_SCHEMA` + 透传（设计 §4.1）。
- `SourceRef` 加 `kb_id`/`kb_name` + hydrate 回溯。
- 范式画布前端（B4）按 `docs/下一阶段-算子化统一规划.md` 主线 B 主导。
