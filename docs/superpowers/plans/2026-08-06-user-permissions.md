# 用户权限管理（Phase 2 真实登录）Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 端到端用户权限管理 —— 真实登录（用户名+密码）、站点级角色 admin/member、按角色分流的前端、admin 用户管理 UI；并把 KB 级权限模型通电。

**Architecture:** 方案 A —— main_control 保持纯 YAML 网关（发 JWT-HS256 + AuthMiddleware 强制 + 反代注入 X-KB-User/X-KB-Role/X-Internal-Auth）；mining 持用户库（`kb_users` 加 `password_hash` + `site_role`，`current_user` 校验 X-Internal-Auth 堵伪造，`require_admin` 现查 site_role）。零新 pip 包（PBKDF2 + 手写 HS256，全 stdlib）。前端：登录页 + auth store + 路由守卫 + 侧边栏按角色过滤 + Header 账户菜单 + 用户管理 Tab。

**Tech Stack:** Python 3 / FastAPI / psycopg（mining、main_control）；Vue 3 / Pinia / Vue Router / Element Plus / axios / vitest（kb-ui）。PostgreSQL（kb_db_test）。

**Spec:** `docs/superpowers/specs/2026-08-06-user-permissions-design.md`

---

## 全局约定（每个任务都必须遵守）

1. **Python 运行**：Windows 下必须 `python -m <module>`。建议 `/c/ProgramData/anaconda3/python.exe`（PATH 含 `Library/bin` 给 libpq）。
2. **DB 测试硬约束**：只在 `kb_db_test` 上跑；环境变量 `KB_RUN_POSTGRES_ACCEPTANCE=1 KB_ALLOW_TEST_TRUNCATE=1 PG_DBNAME=kb_db_test` + 其余 `PG_*`。绝不碰 `kb_db`。
3. **配置源**：所有运行期配置来自 `main_control_service/config/`，不读 `.env`（mining 仅用 `PG_*` 连库，与现状一致）。
4. **提交**：约定式提交，中文描述，每个任务末尾提交。`kb-ui/components.d.ts` 出现幻影改动时 `git restore` 掉再提交。
5. **不装新包**：密码用 stdlib `hashlib.pbkdf2_hmac`；JWT 用 stdlib 手写 HS256。不得引入 bcrypt/passlib/PyJWT/python-jose。
6. **前端测试**：`cd kb-ui && npx vitest run <spec>`；构建 `npm run build`。EP stub 在 `src/test/setup.ts` 全局（ElInput 裸 `<input>` 无 v-model 代理、ElButton click 不可靠 → 组件 `defineExpose` 暴露方法，测试调 `wrapper.vm.method()`）。

---

## Chunk 1: 后端 mining —— 身份基础（迁移 / 密码 / current_user / bootstrap）

### Task 1.1: DB 迁移 —— kb_users 加 password_hash + site_role

**Files:**
- Create: `databases/kb/schemas/006_kb_users_auth.sql`
- Test: `knowledge_mining/tests/kb/test_auth_schema.py`

- [ ] **Step 1: 写迁移 SQL**

`databases/kb/schemas/006_kb_users_auth.sql`:
```sql
-- Phase 2 用户权限管理：kb_users 增加登录凭证与站点级角色。
-- password_hash 为 NULL 表示不可登录（Phase 1 仅被 X-KB-User upsert 出来的行）。
-- site_role 现有行默认 'member'；首 admin 由 mining 启动期 bootstrap 播种提权。
ALTER TABLE kb_users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE kb_users ADD COLUMN IF NOT EXISTS site_role TEXT NOT NULL DEFAULT 'member'
                  CHECK (site_role IN ('admin','member'));
```

- [ ] **Step 2: 确认 schema 序号无冲突**

Run: `ls databases/kb/schemas/`
Expected: `001`–`005` 已存在，`006` 新建无冲突。

- [ ] **Step 3: 写列存在性冒烟测试**

`knowledge_mining/tests/kb/test_auth_schema.py`:
```python
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_kb_users_has_auth_columns(async_pool):
    """迁移 006 后 kb_users 应含 password_hash + site_role，且 site_role 默认 member。"""
    async with async_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT username, password_hash, site_role FROM kb_users LIMIT 0"
        )
        cur.fetchall()  # 不报错即列存在
        # 默认值：插一行不带 site_role，应为 'member'
        cur = await conn.execute(
            "INSERT INTO kb_users (id, username, status, created_at) "
            "VALUES ('t1','schema_probe','active','2026-01-01T00:00:00+00:00') "
            "RETURNING site_role, password_hash"
        )
        row = await cur.fetchone()
    assert row["site_role"] == "member"
    assert row["password_hash"] is None
    # CHECK 约束：非法 site_role 应被拒
    async with async_pool.connection() as conn:
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO kb_users (id, username, status, created_at, site_role) "
                "VALUES ('t2','bad_role','active','2026-01-01T00:00:00+00:00','superuser')"
            )
```

- [ ] **Step 4: 跑测试，确认迁移已应用且通过**

Run:
```bash
KB_RUN_POSTGRES_ACCEPTANCE=1 KB_ALLOW_TEST_TRUNCATE=1 \
PG_HOST=... PG_PORT=5432 PG_DBNAME=kb_db_test PG_USER=... PG_PASSWORD=... \
python -m pytest knowledge_mining/tests/kb/test_auth_schema.py -v
```
Expected: PASS。若报 `column "site_role" does not exist` → 迁移未应用，跑 `python reset_db.py`（**仅测试库**）或手动 `psql kb_db_test -f databases/kb/schemas/006_kb_users_auth.sql`。

- [ ] **Step 5: 提交**

```bash
git add databases/kb/schemas/006_kb_users_auth.sql knowledge_mining/tests/kb/test_auth_schema.py
git commit -m "feat(kb): kb_users 加 password_hash + site_role（迁移 006）"
```

---

### Task 1.2: 密码哈希 security.py（PBKDF2-HMAC-SHA256，stdlib）

**Files:**
- Create: `knowledge_mining/mining/kb/security.py`
- Test: `knowledge_mining/tests/kb/test_security.py`

- [ ] **Step 1: 写失败测试**

`knowledge_mining/tests/kb/test_security.py`:
```python
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.security import hash_password, verify_password


def test_hash_password_format():
    h = hash_password("hunter2")
    parts = h.split("$")
    assert parts[0] == "pbkdf2_sha256"
    assert parts[1].isdigit() and int(parts[1]) >= 100_000
    assert parts[2]  # salt
    assert parts[3]  # hash


def test_verify_password_correct():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True


def test_verify_password_wrong():
    h = hash_password("right")
    assert verify_password("wrong", h) is False


def test_verify_password_garbled_format_returns_false():
    assert verify_password("x", "not-a-valid-format") is False
    assert verify_password("x", "pbkdf2_sha256$abc$no$no") is False


def test_hash_password_unique_salt():
    """两次哈希同密码应得不同结果（随机盐），但都能验过。"""
    a, b = hash_password("same"), hash_password("same")
    assert a != b
    assert verify_password("same", a) and verify_password("same", b)
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest knowledge_mining/tests/kb/test_security.py -v`
Expected: FAIL（`ModuleNotFoundError: knowledge_mining.mining.kb.security`）。注：这些是纯函数测试，但因 conftest autouse session fixture 连 PG，仍需 DB 环境变量（见约定 2）。

- [ ] **Step 3: 实现**

`knowledge_mining/mining/kb/security.py`:
```python
"""密码哈希 —— PBKDF2-HMAC-SHA256（stdlib，零依赖）。

格式：``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``。
验签用 ``hmac.compare_digest`` 恒定时间比较，防时序侧信道。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_ITERATIONS = 200_000  # OWASP 2023 量级
_ALGO = "pbkdf2_sha256"
_DIGEST = "sha256"


def hash_password(plain: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(_DIGEST, plain.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(derived).decode()}"


def verify_password(plain: str, stored: str) -> bool:
    if not isinstance(stored, str):
        return False
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != _ALGO:
        return False
    try:
        iterations = int(parts[1])
        salt = base64.b64decode(parts[2])
        expected = base64.b64decode(parts[3])
    except (ValueError, base64.binascii.Error):
        return False
    if iterations < 1 or not salt or not expected:
        return False
    derived = hashlib.pbkdf2_hmac(_DIGEST, plain.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest knowledge_mining/tests/kb/test_security.py -v`
Expected: 5 passed。

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/mining/kb/security.py knowledge_mining/tests/kb/test_security.py
git commit -m "feat(kb): PBKDF2 密码哈希（security.py，stdlib 零依赖）"
```

---

### Task 1.3: KbDB —— upsert 不变量 + 用户管理方法

**Files:**
- Modify: `knowledge_mining/mining/kb/db.py`（`upsert_user_by_username` RETURNING 扩列 + 冲突不变量；新增 user 管理方法）
- Test: `knowledge_mining/tests/kb/test_kb_db.py`（追加用例）

- [ ] **Step 1: 写失败测试（追加到 test_kb_db.py 末尾）**

```python
import pytest

from knowledge_mining.mining.kb.db import KbDB


@pytest.mark.asyncio
async def test_upsert_user_returns_site_role(async_pool):
    db = KbDB(async_pool)
    u = await db.upsert_user_by_username("alice")
    assert u["username"] == "alice"
    assert u["site_role"] == "member"  # 新列在返回里
    assert "id" in u and "status" in u


@pytest.mark.asyncio
async def test_upsert_does_not_overwrite_admin_role_or_password(async_pool):
    """§5.3 不变量：对已存在 admin 用户名重复 upsert，site_role/password_hash 不被清。"""
    db = KbDB(async_pool)
    # 先建一个 admin 且带密码
    await db.create_user(username="admin", password_hash="$algo$1$AA$BB", site_role="admin")
    # 日常 KB 流量再次 upsert 同名（display_name 不同）
    await db.upsert_user_by_username("admin", display_name="日常名")
    async with async_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT site_role, password_hash, display_name FROM kb_users WHERE username='admin'"
        )
        row = await cur.fetchone()
    assert row["site_role"] == "admin"          # 未被降级
    assert row["password_hash"] == "$algo$1$AA$BB"  # 未被清空
    assert row["display_name"] == "日常名"       # display_name 仍可更新


@pytest.mark.asyncio
async def test_user_crud(async_pool):
    db = KbDB(async_pool)
    u = await db.create_user(username="bob", password_hash="h1", site_role="member", display_name="Bob")
    assert u["site_role"] == "member"
    users = await db.list_users()
    assert any(x["username"] == "bob" for x in users)
    await db.update_user(u["id"], site_role="admin")
    got = await db.get_user_by_username("bob")
    assert got["site_role"] == "admin"
    await db.update_user(u["id"], status="disabled")
    assert (await db.get_user_by_username("bob"))["status"] == "disabled"
    await db.set_password_hash(u["id"], "h2")
    async with async_pool.connection() as conn:
        cur = await conn.execute("SELECT password_hash FROM kb_users WHERE id=%s", [u["id"]])
        assert (await cur.fetchone())["password_hash"] == "h2"
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest knowledge_mining/tests/kb/test_kb_db.py::test_upsert_user_returns_site_role -v`
Expected: FAIL（`KeyError: 'site_role'` —— 当前 RETURNING 不含 site_role）。

- [ ] **Step 3: 修改 db.py**

(a) `upsert_user_by_username` RETURNING 加 `site_role`（冲突 SET 只动 display_name，已满足不变量，无需改 SET 子句）：

```python
async def upsert_user_by_username(
    self, username: str, *, display_name: str | None = None
) -> dict[str, Any]:
    """Idempotent user upsert by username. 冲突时只更新 display_name，
    绝不动 site_role/password_hash（§5.3 不变量）。"""
    async with self._pool.connection() as conn:
        cur = await conn.execute(
            """INSERT INTO kb_users (id, username, display_name, status, created_at)
               VALUES (%(id)s, %(u)s, %(d)s, 'active', %(t)s)
               ON CONFLICT (username) DO UPDATE
                 SET display_name = COALESCE(%(d)s, kb_users.display_name)
               RETURNING id, username, display_name, status, site_role""",
            {"id": _new_id(), "u": username, "d": display_name, "t": _utcnow()},
        )
        row = await cur.fetchone()
        return dict(row)  # type: ignore[arg-type]
```

(b) 在 `upsert_user_by_username` 之后新增用户管理方法：

```python
    # ---------------------------------------------------- user management (Phase 2)

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, username, display_name, status, site_role, password_hash, created_at
                   FROM kb_users WHERE username = %s""",
                [username],
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, username, display_name, status, site_role, password_hash, created_at
                   FROM kb_users WHERE id = %s""",
                [user_id],
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_users(self) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, username, display_name, status, site_role,
                          (password_hash IS NOT NULL) AS has_password, created_at
                   FROM kb_users ORDER BY created_at""",
            )
            return [dict(r) for r in await cur.fetchall()]

    async def create_user(
        self, *, username: str, password_hash: str | None = None,
        site_role: str = "member", display_name: str | None = None,
    ) -> dict[str, Any]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO kb_users (id, username, display_name, status, created_at,
                                         password_hash, site_role)
                   VALUES (%(id)s, %(u)s, %(d)s, 'active', %(t)s, %(ph)s, %(sr)s)
                   RETURNING id, username, display_name, status, site_role, password_hash, created_at""",
                {"id": _new_id(), "u": username, "d": display_name, "t": _utcnow(),
                 "ph": password_hash, "sr": site_role},
            )
            return dict(await cur.fetchone())  # type: ignore[arg-type]

    async def update_user(
        self, user_id: str, *,
        display_name: str | None = None, site_role: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        """PATCH 风格：只更新提供的字段。显式 None 视为不传（与 update_kb 一致，这里字段少不做 SET NULL）。"""
        sets: list[str] = []
        params: dict[str, Any] = {"id": user_id}
        if display_name is not None:
            params["d"] = display_name
            sets.append("display_name = %(d)s")
        if site_role is not None:
            params["sr"] = site_role
            sets.append("site_role = %(sr)s")
        if status is not None:
            params["st"] = status
            sets.append("status = %(st)s")
        if not sets:
            return await self.get_user(user_id)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE kb_users SET " + ", ".join(sets) + " WHERE id = %(id)s "
                "RETURNING id, username, display_name, status, site_role, created_at",
                params,
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_password_hash(self, user_id: str, password_hash: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE kb_users SET password_hash = %s WHERE id = %s",
                [password_hash, user_id],
            )

    async def has_admin(self) -> bool:
        """是否存在可登录的 admin（site_role='admin' AND password_hash IS NOT NULL）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM kb_users WHERE site_role='admin' AND password_hash IS NOT NULL LIMIT 1"
            )
            return (await cur.fetchone()) is not None

    async def count_admins(self) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM kb_users WHERE site_role='admin'"
            )
            return int((await cur.fetchone())["n"])
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest knowledge_mining/tests/kb/test_kb_db.py -v`
Expected: 全部 PASS（含新 4 例 + 原有用例不被破坏）。

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/mining/kb/db.py knowledge_mining/tests/kb/test_kb_db.py
git commit -m "feat(kb): KbDB upsert 返回 site_role + 用户管理方法（§5.3 不变量）"
```

---

### Task 1.4: control_plane.fetch_auth_config + 内部 secret 模块态

**Files:**
- Modify: `knowledge_mining/mining/infra/control_plane.py`
- Test: `knowledge_mining/tests/kb/test_control_plane_auth.py`

- [ ] **Step 1: 写失败测试**

`knowledge_mining/tests/kb/test_control_plane_auth.py`:
```python
from __future__ import annotations

from knowledge_mining.mining.infra import control_plane


def test_auth_config_cache_set_get():
    control_plane.set_auth_config({
        "jwt_secret": "s",
        "internal_verify_secret": "ivs-test",
        "token_ttl_seconds": 3600,
        "bootstrap": {"admin_password": "x"},
    })
    cfg = control_plane.get_auth_config()
    assert cfg["internal_verify_secret"] == "ivs-test"


def test_internal_verify_secret_defaults_none():
    control_plane.set_auth_config({})
    assert control_plane.get_internal_verify_secret() is None
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest knowledge_mining/tests/kb/test_control_plane_auth.py -v`
Expected: FAIL（`set_auth_config` 不存在）。

- [ ] **Step 3: 实现（追加到 control_plane.py）**

```python
_auth_config_cache: dict[str, Any] | None = None


def fetch_auth_config(*, force: bool = False) -> dict[str, Any]:
    """拉取并缓存 auth.yaml。best-effort：控制面不可达抛 RuntimeError 由调用方兜底。"""
    global _auth_config_cache
    if _auth_config_cache is None or force:
        _auth_config_cache = _get_raw("auth")
    return _auth_config_cache


def get_auth_config() -> dict[str, Any]:
    if _auth_config_cache is None:
        return fetch_auth_config()
    return _auth_config_cache


def set_auth_config(cfg: dict[str, Any]) -> None:
    """预填 auth 配置缓存（启动 / 测试用）。"""
    global _auth_config_cache
    _auth_config_cache = cfg


def get_internal_verify_secret() -> str | None:
    """current_user 校验 X-KB-Auth 用的内部凭证。缓存未就绪 → None（current_user 一律 401）。"""
    if _auth_config_cache is None:
        return None
    return _auth_config_cache.get("internal_verify_secret")
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest knowledge_mining/tests/kb/test_control_plane_auth.py -v`
Expected: 2 passed。

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/mining/infra/control_plane.py knowledge_mining/tests/kb/test_control_plane_auth.py
git commit -m "feat(mining): control_plane 拉 auth.yaml + internal_verify_secret 访问器"
```

---

### Task 1.5: current_user 校验 X-Internal-Auth + require_admin

**Files:**
- Modify: `knowledge_mining/mining/kb/auth.py`
- Test: `knowledge_mining/tests/kb/test_current_user.py`

> **设计要点**：`current_user` 同时要 `X-KB-User` + `X-Internal-Auth`（= `internal_verify_secret`）。直连 8901 的伪造者拿不到 secret → 401。`require_admin` 在 `current_user` 基础上现查 `site_role`。

- [ ] **Step 1: 写失败测试**

`knowledge_mining/tests/kb/test_current_user.py`:
```python
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.kb import auth as auth_mod
from knowledge_mining.mining.kb.auth import current_user, require_admin
from knowledge_mining.mining.infra import control_plane


def _app(pg_pool):
    app = FastAPI()
    app.state.pg_pool = pg_pool

    @app.get("/who")
    async def who(user=pytest.MonkeyPatch().context if False else None):  # placeholder
        ...

    @app.get("/me")
    async def me(user=...) :
        ...
    return app


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    control_plane.set_auth_config({"internal_verify_secret": "ivs-test"})
    yield
    control_plane.set_auth_config({})


async def _client(pg_pool):
    from fastapi import Depends
    app = FastAPI()
    app.state.pg_pool = pg_pool

    @app.get("/who")
    async def who(user=Depends(current_user)):
        return user

    @app.get("/admin")
    async def admin(user=Depends(require_admin)):
        return user

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_current_user_missing_header_401(async_pool):
    async with await _client(async_pool) as c:
        r = await c.get("/who")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_current_user_missing_internal_auth_401(async_pool):
    """核心安全断言：有 X-KB-User 但缺 X-Internal-Auth（直连伪造）→ 401。"""
    async with await _client(async_pool) as c:
        r = await c.get("/who", headers={"X-KB-User": "alice"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_current_user_ok(async_pool):
    async with await _client(async_pool) as c:
        r = await c.get("/who", headers={"X-KB-User": "alice", "X-Internal-Auth": "ivs-test"})
        assert r.status_code == 200
        body = r.json()
        assert body["username"] == "alice"
        assert body["site_role"] == "member"


@pytest.mark.asyncio
async def test_current_user_wrong_internal_auth_401(async_pool):
    async with await _client(async_pool) as c:
        r = await c.get("/who", headers={"X-KB-User": "alice", "X-Internal-Auth": "wrong"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_require_admin_member_403(async_pool):
    async with await _client(async_pool) as c:
        h = {"X-KB-User": "alice", "X-Internal-Auth": "ivs-test"}
        await c.get("/who", headers=h)  # upsert alice as member
        r = await c.get("/admin", headers=h)
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_require_admin_admin_ok(async_pool):
    from knowledge_mining.mining.kb.db import KbDB
    db = KbDB(async_pool)
    await db.create_user(username="root", password_hash="h", site_role="admin")
    async with await _client(async_pool) as c:
        r = await c.get("/admin", headers={"X-KB-User": "root", "X-Internal-Auth": "ivs-test"})
        assert r.status_code == 200
        assert r.json()["site_role"] == "admin"
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest knowledge_mining/tests/kb/test_current_user.py -v`
Expected: 多数 FAIL（current_user 不校验 X-Internal-Auth；require_admin 不存在）。

- [ ] **Step 3: 重写 auth.py**

`knowledge_mining/mining/kb/auth.py`:
```python
"""身份解析 —— Phase 2：X-KB-User + X-Internal-Auth 双校验。

信任模型：mining:8901 被 publish 到宿主机，单凭 X-KB-User 可被直连伪造。
故 current_user 额外要求 X-Internal-Auth == auth.yaml.internal_verify_secret
（只有已鉴权网关能产出该头）。这堵死全部 /api/kb/* 的身份伪造。
site_role 由库现查（require_admin），不靠 X-KB-Role 头。
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from knowledge_mining.mining.infra.control_plane import get_internal_verify_secret
from knowledge_mining.mining.kb.db import KbDB


async def current_user(request: Request) -> dict[str, Any]:
    """Resolve caller from X-KB-User + X-Internal-Auth; upsert kb_users row."""
    username = request.headers.get("X-KB-User", "").strip()
    if not username:
        raise HTTPException(401, "missing X-KB-User header")
    secret = get_internal_verify_secret()
    if not secret:
        # auth.yaml 未就绪（启动期控制面不可达）—— 一律拒，避免无 secret 时放行
        raise HTTPException(401, "auth not initialized")
    if request.headers.get("X-Internal-Auth", "") != secret:
        raise HTTPException(401, "unauthenticated")
    db = KbDB(request.app.state.pg_pool)
    return await db.upsert_user_by_username(username)


async def require_admin(request: Request) -> dict[str, Any]:
    """current_user + site_role 必须为 admin（现查库，纵深防御）。"""
    user = await current_user(request)
    if user.get("site_role") != "admin":
        raise HTTPException(403, "admin required")
    return user
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest knowledge_mining/tests/kb/test_current_user.py -v`
Expected: 6 passed。

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/mining/kb/auth.py knowledge_mining/tests/kb/test_current_user.py
git commit -m "feat(kb): current_user 校验 X-Internal-Auth 堵伪造 + require_admin"
```

---

### Task 1.6: bootstrap 播种首 admin（幂等）

**Files:**
- Create: `knowledge_mining/mining/kb/bootstrap.py`
- Modify: `knowledge_mining/mining/api/app.py`（lifespan 调用）
- Test: `knowledge_mining/tests/kb/test_bootstrap.py`

- [ ] **Step 1: 写失败测试**

`knowledge_mining/tests/kb/test_bootstrap.py`:
```python
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb import bootstrap
from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.security import verify_password


@pytest.mark.asyncio
async def test_bootstrap_seeds_admin_when_none(async_pool):
    await bootstrap.seed_initial_admin(async_pool, admin_password="init-pass")
    db = KbDB(async_pool)
    admin = await db.get_user_by_username("admin")
    assert admin is not None
    assert admin["site_role"] == "admin"
    assert verify_password("init-pass", admin["password_hash"])


@pytest.mark.asyncio
async def test_bootstrap_idempotent_when_admin_exists(async_pool):
    """已有可登录 admin → 二次启动不改其 password_hash/site_role。"""
    await bootstrap.seed_initial_admin(async_pool, admin_password="first")
    db = KbDB(async_pool)
    admin = await db.get_user_by_username("admin")
    original_hash = admin["password_hash"]
    # 二次播种，用不同密码
    await bootstrap.seed_initial_admin(async_pool, admin_password="different")
    admin2 = await db.get_user_by_username("admin")
    assert admin2["password_hash"] == original_hash  # 未被覆盖
    assert admin2["site_role"] == "admin"
    assert verify_password("first", admin2["password_hash"])  # 原密码仍有效


@pytest.mark.asyncio
async def test_bootstrap_seeds_when_only_members_exist(async_pool):
    """表非空但无 admin（只有 member）→ 仍应播种 admin。"""
    db = KbDB(async_pool)
    await db.create_user(username="member1", password_hash="h", site_role="member")
    await bootstrap.seed_initial_admin(async_pool, admin_password="p")
    admin = await db.get_user_by_username("admin")
    assert admin is not None and admin["site_role"] == "admin"
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest knowledge_mining/tests/kb/test_bootstrap.py -v`
Expected: FAIL（`bootstrap` 模块不存在）。

- [ ] **Step 3: 实现 bootstrap.py**

`knowledge_mining/mining/kb/bootstrap.py`:
```python
"""启动期播种首个可登录 admin（破鸡生蛋）。

幂等：仅当 kb_users 中不存在任何 site_role='admin' AND password_hash IS NOT NULL
的行时，才把 admin 用户（不存在则建）提权为 admin 并设密码。
"""
from __future__ import annotations

import logging
from typing import Any

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.security import hash_password

logger = logging.getLogger(__name__)


async def seed_initial_admin(pool: Any, *, admin_password: str) -> None:
    """若无可登录 admin，播种 admin/admin_password。幂等。"""
    if not admin_password:
        logger.warning("bootstrap.admin_password 为空，跳过播种首 admin")
        return
    db = KbDB(pool)
    if await db.has_admin():
        logger.info("bootstrap: 已有可登录 admin，跳过播种")
        return
    hashed = hash_password(admin_password)
    existing = await db.get_user_by_username("admin")
    if existing is None:
        await db.create_user(username="admin", password_hash=hashed, site_role="admin",
                             display_name="Administrator")
    else:
        await db.update_user(existing["id"], site_role="admin")
        await db.set_password_hash(existing["id"], hashed)
    logger.warning(
        "bootstrap: 已播种首 admin（用户名 admin）—— 请尽快登录改密并从 auth.yaml 移除 bootstrap.admin_password"
    )
```

- [ ] **Step 4: 接入 lifespan（修改 app.py）**

在 `knowledge_mining/mining/api/app.py` 的 `lifespan` 内，`fetch_database_config(force=True)` 之后、`ensure_primary_schema` 之后（schema 必须先就位），追加：
```python
    # Phase 2：拉 auth.yaml 并播种首 admin（best-effort，不阻断启动）
    from knowledge_mining.mining.infra.control_plane import fetch_auth_config
    from knowledge_mining.mining.kb.bootstrap import seed_initial_admin
    try:
        auth_cfg = fetch_auth_config(force=True)
        admin_pw = (auth_cfg.get("bootstrap") or {}).get("admin_password") or ""
        await seed_initial_admin(pool, admin_password=admin_pw)
    except Exception as exc:  # noqa: BLE001 — 控制面不可达不应阻断 mining 启动
        logger.warning("auth bootstrap skipped: %s", exc)
```
（`pool` 在 ensure_primary_schema 之后已 `await pool.open()`。把上述代码块插在 `app.state.domain_pools = DomainPoolManager(cfg)` 之前即可。）

- [ ] **Step 5: 跑测试，确认通过**

Run: `python -m pytest knowledge_mining/tests/kb/test_bootstrap.py -v`
Expected: 3 passed。

- [ ] **Step 6: 提交**

```bash
git add knowledge_mining/mining/kb/bootstrap.py knowledge_mining/mining/api/app.py knowledge_mining/tests/kb/test_bootstrap.py
git commit -m "feat(kb): 启动期幂等播种首 admin（bootstrap）"
```

---

### Task 1.7: conftest auth 辅助 + 既有 KB 路由测试回填 X-Internal-Auth

**Files:**
- Modify: `knowledge_mining/tests/conftest.py`（预填 auth 缓存 + 暴露 `kb_headers`）
- Modify: `knowledge_mining/tests/kb/test_mining.py`（`{"X-KB-User": "x"}` → `kb_headers("x")`）
- Modify: `knowledge_mining/tests/kb/test_kb_db.py`、`test_workflow_document_prep.py`（同上回填）

> current_user 现在要 X-Internal-Auth，既有路由测试会全部 401。用一个共享 helper 机械替换。

- [ ] **Step 1: 在 conftest.py 暴露测试 secret + kb_headers**

在 `conftest.py` 的 `_prefill_control_plane_caches_from_env()` 函数体内末尾追加：
```python
    from knowledge_mining.mining.infra.control_plane import set_auth_config
    set_auth_config({"internal_verify_secret": os.environ.get("KB_TEST_INTERNAL_AUTH", "test-ivs")})
```
并在 `conftest.py` 模块级（函数外）新增：
```python
def kb_headers(username: str = "tester") -> dict[str, str]:
    """测试用 KB 请求头：X-KB-User + X-Internal-Auth（secret 与 prefill 一致）。"""
    return {
        "X-KB-User": username,
        "X-Internal-Auth": os.environ.get("KB_TEST_INTERNAL_AUTH", "test-ivs"),
    }
```

- [ ] **Step 2: 回填 test_mining.py**

把所有 `headers={"X-KB-User": "alice"}` → `headers=kb_headers("alice")`，`{"X-KB-User": "bob"}` → `kb_headers("bob")`，`{"X-KB-User": "alice"}` 内联字面量同理。在文件顶部 `from knowledge_mining.tests.conftest import kb_headers`。涉及行（按当前文件）：72、78、80、82、86、90、96、114、117、119、153、155、179、193（逐个替换）。

- [ ] **Step 3: 回填 test_kb_db.py / test_workflow_document_prep.py**

同样：凡构造 `{"X-KB-User": ...}` 作 headers 的，改用 `kb_headers(...)`。若这些测试是直接调 db 方法（不经 HTTP），则无需改（它们不经 current_user）—— 只改经 HTTP client 打路由的。

- [ ] **Step 4: 跑全部 kb 测试套件，确认回填完整、无 401 残留**

Run: `python -m pytest knowledge_mining/tests/kb/ -v`
Expected: 全 PASS（含 Task 1.1–1.6 新增 + 既有 test_mining 等）。若某测试 401 → 漏了某处 headers 未回填。

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/tests/conftest.py knowledge_mining/tests/kb/
git commit -m "test(kb): conftest 暴露 kb_headers + 既有路由测试回填 X-Internal-Auth"
```

---

## Chunk 2: 后端 mining —— 用户管理端点（verify + /api/kb/users + me/password）

### Task 2.1: 用户管理 service（user_service.py）

**Files:**
- Create: `knowledge_mining/mining/kb/services/user_service.py`
- Test: `knowledge_mining/tests/kb/test_user_service.py`

- [ ] **Step 1: 写失败测试**

`knowledge_mining/tests/kb/test_user_service.py`:
```python
from __future__ import annotations

import pytest

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.services.user_service import (
    DuplicateUser, InvalidRole, UserService,
)
from knowledge_mining.mining.kb.security import verify_password


@pytest.fixture
def svc(async_pool):
    return UserService(KbDB(async_pool))


@pytest.mark.asyncio
async def test_create_user_hashes_password(svc):
    u = await svc.create_user(username="alice", password="pw1", site_role="member", display_name="Alice")
    assert u["site_role"] == "member"
    assert verify_password("pw1", u["password_hash"])


@pytest.mark.asyncio
async def test_create_user_duplicate(svc):
    await svc.create_user(username="alice", password="pw", site_role="member")
    with pytest.raises(DuplicateUser):
        await svc.create_user(username="alice", password="pw2", site_role="member")


@pytest.mark.asyncio
async def test_create_user_bad_role(svc):
    with pytest.raises(InvalidRole):
        await svc.create_user(username="x", password="p", site_role="superuser")


@pytest.mark.asyncio
async def test_reset_password(svc):
    u = await svc.create_user(username="bob", password="old", site_role="member")
    await svc.reset_password(u["id"], "new")
    db_row = await svc._db.get_user(u["id"])
    assert verify_password("new", db_row["password_hash"])
    assert not verify_password("old", db_row["password_hash"])


@pytest.mark.asyncio
async def test_change_own_password_verifies_old(svc):
    u = await svc.create_user(username="carol", password="old", site_role="member")
    with pytest.raises(Exception):
        await svc.change_own_password(user_id=u["id"], old="wrong", new="new")
    await svc.change_own_password(user_id=u["id"], old="old", new="new")
    assert verify_password("new", (await svc._db.get_user(u["id"]))["password_hash"])
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest knowledge_mining/tests/kb/test_user_service.py -v`
Expected: FAIL（`UserService` 不存在）。

- [ ] **Step 3: 实现**

`knowledge_mining/mining/kb/services/user_service.py`:
```python
"""用户管理业务逻辑（admin 操作 + 改自己密码）。"""
from __future__ import annotations

from typing import Any

from psycopg.errors import UniqueViolation

from knowledge_mining.mining.kb.db import KbDB
from knowledge_mining.mining.kb.security import hash_password, verify_password


class UserError(Exception):
    pass


class DuplicateUser(UserError):
    pass


class InvalidRole(UserError):
    pass


class UserNotFound(UserError):
    pass


class WrongPassword(UserError):
    pass


_VALID_ROLES = {"admin", "member"}


class UserService:
    def __init__(self, db: KbDB) -> None:
        self._db = db

    async def list_users(self) -> list[dict[str, Any]]:
        return await self._db.list_users()

    async def create_user(
        self, *, username: str, password: str, site_role: str = "member",
        display_name: str | None = None,
    ) -> dict[str, Any]:
        if site_role not in _VALID_ROLES:
            raise InvalidRole(site_role)
        if not username.strip():
            raise UserError("username required")
        if len(password) < 8:
            raise UserError("password too short (<8)")
        try:
            return await self._db.create_user(
                username=username.strip(), password_hash=hash_password(password),
                site_role=site_role, display_name=display_name,
            )
        except UniqueViolation as exc:
            raise DuplicateUser(username) from exc

    async def update_user(
        self, *, user_id: str, display_name: str | None = None,
        site_role: str | None = None, status: str | None = None,
    ) -> dict[str, Any]:
        if site_role is not None and site_role not in _VALID_ROLES:
            raise InvalidRole(site_role)
        updated = await self._db.update_user(
            user_id, display_name=display_name, site_role=site_role, status=status,
        )
        if updated is None:
            raise UserNotFound(user_id)
        return updated

    async def reset_password(self, user_id: str, new_password: str) -> None:
        if len(new_password) < 8:
            raise UserError("password too short (<8)")
        if await self._db.get_user(user_id) is None:
            raise UserNotFound(user_id)
        await self._db.set_password_hash(user_id, hash_password(new_password))

    async def change_own_password(
        self, *, user_id: str, old: str, new: str,
    ) -> None:
        if len(new) < 8:
            raise UserError("password too short (<8)")
        user = await self._db.get_user(user_id)
        if user is None or not user.get("password_hash"):
            raise UserNotFound(user_id)
        if not verify_password(old, user["password_hash"]):
            raise WrongPassword("old password mismatch")
        await self._db.set_password_hash(user_id, hash_password(new))

    async def verify_credentials(self, *, username: str, password: str) -> dict[str, Any] | None:
        """登录校验：返回 user（含 site_role/display_name）或 None。"""
        user = await self._db.get_user_by_username(username)
        if user is None or not user.get("password_hash"):
            return None
        if user.get("status") == "disabled":
            return None
        if not verify_password(password, user["password_hash"]):
            return None
        return user
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest knowledge_mining/tests/kb/test_user_service.py -v`
Expected: 5 passed。

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/mining/kb/services/user_service.py knowledge_mining/tests/kb/test_user_service.py
git commit -m "feat(kb): UserService 用户管理业务（建/改/重置/改密/校验凭证）"
```

---

### Task 2.2: routes/auth.py —— verify + 用户 CRUD + me/password

**Files:**
- Create: `knowledge_mining/mining/kb/routes/auth.py`
- Modify: `knowledge_mining/mining/kb/deps.py`（加 `get_user_service` 依赖）
- Modify: `knowledge_mining/mining/api/app.py`（挂 router）
- Test: `knowledge_mining/tests/kb/test_auth_routes.py`

> 先看 `deps.py` 现有 `get_kb_db`/`get_kb_service` 模式，照抄 `get_user_service`。

- [ ] **Step 1: 写失败测试（核心：verify 鉴内部头、用户 CRUD admin-only）**

`knowledge_mining/tests/kb/test_auth_routes.py`:
```python
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from knowledge_mining.mining.kb.routes.auth import router as auth_router
from knowledge_mining.tests.conftest import kb_headers


def _client(async_pool):
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig
    app = FastAPI()
    app.state.pg_pool = async_pool
    app.state.db_config = MiningDbConfig()
    app.include_router(auth_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_verify_wrong_internal_secret_401(async_pool):
    async with await _client(async_pool) as c:
        r = await c.post("/api/kb/auth/verify", json={"username": "x", "password": "y"},
                         headers={"X-Internal-Auth": "wrong"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_verify_creates_then_authenticates(async_pool):
    async with await _client(async_pool) as c:
        # 先用 admin 建 alice（绕过：直接建 admin 种子）
        h = kb_headers("root")
        # 直接经 service 建一个 admin 与一个 member
        async with async_pool.connection() as conn:
            pass
        # 走 create 端点建 admin root（库空 → 先手动建 root 为 admin）
        from knowledge_mining.mining.kb.db import KbDB
        db = KbDB(async_pool)
        await db.create_user(username="root", password_hash="x", site_role="admin")
        # root 建 alice
        r = await c.post("/api/kb/users", json={"username": "alice", "password": "alicepw12", "site_role": "member"},
                         headers=kb_headers("root"))
        assert r.status_code == 201, r.text
        # verify alice
        r = await c.post("/api/kb/auth/verify", json={"username": "alice", "password": "alicepw12"},
                         headers=kb_headers("ignored"))  # X-Internal-Auth 由生产里 main_control 注入；测试里 kb_headers 给
        assert r.status_code == 200, r.text
        assert r.json()["user"]["username"] == "alice"
        assert r.json()["user"]["site_role"] == "member"


@pytest.mark.asyncio
async def test_verify_bad_password_401(async_pool):
    from knowledge_mining.mining.kb.db import KbDB
    db = KbDB(async_pool)
    await db.create_user(username="alice", password_hash="x", site_role="member")
    async with await _client(async_pool) as c:
        r = await c.post("/api/kb/auth/verify", json={"username": "alice", "password": "wrong"},
                         headers=kb_headers("i"))
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_users_list_admin_only(async_pool):
    from knowledge_mining.mining.kb.db import KbDB
    db = KbDB(async_pool)
    await db.create_user(username="root", password_hash="x", site_role="admin")
    await db.create_user(username="alice", password_hash="x", site_role="member")
    async with await _client(async_pool) as c:
        assert (await c.get("/api/kb/users", headers=kb_headers("root"))).status_code == 200
        assert (await c.get("/api/kb/users", headers=kb_headers("alice"))).status_code == 403


@pytest.mark.asyncio
async def test_change_my_password(async_pool):
    from knowledge_mining.mining.kb.db import KbDB
    from knowledge_mining.mining.kb.security import hash_password, verify_password
    db = KbDB(async_pool)
    u = await db.create_user(username="alice", password_hash=hash_password("oldpw12"), site_role="member")
    async with await _client(async_pool) as c:
        r = await c.post("/api/kb/users/me/password",
                         json={"old": "oldpw12", "new": "newpw34"},
                         headers=kb_headers("alice"))
        assert r.status_code == 200, r.text
    row = await db.get_user(u["id"])
    assert verify_password("newpw34", row["password_hash"])
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest knowledge_mining/tests/kb/test_auth_routes.py -v`
Expected: FAIL（路由不存在）。

- [ ] **Step 3: 加依赖 `get_user_service`（deps.py）**

参照 `get_kb_service`，在 `deps.py` 追加：
```python
def get_user_service(request: Request) -> UserService:
    return UserService(KbDB(request.app.state.pg_pool))
```
（顶部 import 补 `UserService`、`KbDB`。）

- [ ] **Step 4: 实现 routes/auth.py**

`knowledge_mining/mining/kb/routes/auth.py`:
```python
"""认证与用户管理路由。

- /api/kb/auth/verify：内部端点（main_control 调，X-Internal-Auth 校验），验密码。
- /api/kb/users*：admin 用户管理（require_admin）。
- /api/kb/users/me/password：任一登录用户改自己密码。

所有路由经 current_user（校验 X-KB-User + X-Internal-Auth）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from knowledge_mining.mining.infra.control_plane import get_internal_verify_secret
from knowledge_mining.mining.kb.auth import current_user, require_admin
from knowledge_mining.mining.kb.services.user_service import (
    DuplicateUser, InvalidRole, UserError, UserNotFound, UserService, WrongPassword,
)

router = APIRouter(prefix="/api/kb", tags=["kb-auth"])


# ---------------------------------------------------------------- models

class VerifyReq(BaseModel):
    username: str
    password: str


class CreateUserReq(BaseModel):
    username: str
    password: str
    site_role: str = "member"
    display_name: str | None = None


class UpdateUserReq(BaseModel):
    display_name: str | None = None
    site_role: str | None = None
    status: str | None = None


class ResetPasswordReq(BaseModel):
    password: str


class ChangeMyPasswordReq(BaseModel):
    old: str
    new: str = Field(min_length=8)


# ---------------------------------------------------------------- verify (internal)

@router.post("/auth/verify")
async def verify_credentials(
    body: VerifyReq,
    svc: UserService = Depends(lambda: None),  # placeholder, replaced below
):
    # X-Internal-Auth 校验（main_control 注入；直连伪造无此头 → 401）
    secret = get_internal_verify_secret()
    if not secret:
        raise HTTPException(401, "auth not initialized")
    # 这里用 request 头校验 —— 因 Depends(current_user) 也会校验内部头，
    # 但 verify 是「登录」语义，不该要求已登录用户；故独立校验内部头。
    ...


# 正确实现：verify 不挂 current_user，自己校验 X-Internal-Auth
@router.post("/auth/verify")
async def verify_credentials_v2(
    body: VerifyReq,
    request: Request,  # noqa: F821 — 仅为示意，正式实现见下
):
    ...
```

> **注意（实现者必读）**：上面 `verify` 的占位段落不要照抄。`/api/kb/auth/verify` 必须独立校验 `X-Internal-Auth`（不能用 `Depends(current_user)`，因为 current_user 要求 `X-KB-User`，而 verify 是 main_control 的服务端调用，没有「当前用户」概念）。正确实现如下 —— 替换上面的占位：

`knowledge_mining/mining/kb/routes/auth.py`（**完整、最终**，删掉上面占位段）：
```python
"""认证与用户管理路由。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from knowledge_mining.mining.infra.control_plane import get_internal_verify_secret
from knowledge_mining.mining.kb.auth import current_user, require_admin
from knowledge_mining.mining.kb.services.user_service import (
    DuplicateUser, InvalidRole, UserError, UserNotFound, UserService, WrongPassword,
)

router = APIRouter(prefix="/api/kb", tags=["kb-auth"])


class VerifyReq(BaseModel):
    username: str
    password: str


class CreateUserReq(BaseModel):
    username: str
    password: str
    site_role: str = "member"
    display_name: str | None = None


class UpdateUserReq(BaseModel):
    display_name: str | None = None
    site_role: str | None = None
    status: str | None = None


class ResetPasswordReq(BaseModel):
    password: str = Field(min_length=8)


class ChangeMyPasswordReq(BaseModel):
    old: str
    new: str = Field(min_length=8)


def _require_internal(request: Request) -> None:
    secret = get_internal_verify_secret()
    if not secret:
        raise HTTPException(401, "auth not initialized")
    if request.headers.get("X-Internal-Auth", "") != secret:
        raise HTTPException(401, "unauthenticated")


def _map_user_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (UserNotFound,)):
        return HTTPException(404, str(exc) or "user not found")
    if isinstance(exc, DuplicateUser):
        return HTTPException(409, str(exc) or "duplicate user")
    if isinstance(exc, (InvalidRole, WrongPassword, UserError)):
        return HTTPException(400, str(exc))
    return HTTPException(500, str(exc))


@router.post("/auth/verify")
async def verify_credentials(body: VerifyReq, request: Request,
                             svc: UserService = Depends(...)) -> dict[str, Any]:
    """内部端点：main_control 调，验密码返回用户。X-Internal-Auth 必须匹配。"""
    _require_internal(request)
    # ↓ UserService 通过 Depends 取（见 deps.get_user_service）
    ...


# 用户管理（admin）
@router.get("/users")
async def list_users(user: dict = Depends(require_admin),
                     svc: UserService = Depends(...)) -> list[dict[str, Any]]:
    return await svc.list_users()
```

> **实现者注意**：上面用 `Depends(...)` 占位处都要替换为 `Depends(get_user_service)`（从 `knowledge_mining.mining.kb.deps` 导入）。把 verify 的 `...` 也换成实际逻辑：

```python
from knowledge_mining.mining.kb.deps import get_user_service

@router.post("/auth/verify")
async def verify_credentials(body: VerifyReq, request: Request,
                             svc: UserService = Depends(get_user_service)) -> dict[str, Any]:
    _require_internal(request)
    user = await svc.verify_credentials(username=body.username, password=body.password)
    if user is None:
        raise HTTPException(401, "invalid credentials")
    return {"ok": True, "user": {
        "username": user["username"],
        "display_name": user.get("display_name"),
        "site_role": user["site_role"],
    }}


@router.get("/users")
async def list_users(user: dict = Depends(require_admin),
                     svc: UserService = Depends(get_user_service)) -> list[dict[str, Any]]:
    return await svc.list_users()


@router.post("/users", status_code=201)
async def create_user(body: CreateUserReq, user: dict = Depends(require_admin),
                      svc: UserService = Depends(get_user_service)) -> dict[str, Any]:
    try:
        return await svc.create_user(username=body.username, password=body.password,
                                     site_role=body.site_role, display_name=body.display_name)
    except (DuplicateUser, InvalidRole, UserError) as exc:
        raise _map_user_error(exc) from None


@router.patch("/users/{user_id}")
async def update_user(user_id: str, body: UpdateUserReq,
                      user: dict = Depends(require_admin),
                      svc: UserService = Depends(get_user_service)) -> dict[str, Any]:
    try:
        return await svc.update_user(user_id=user_id, display_name=body.display_name,
                                     site_role=body.site_role, status=body.status)
    except (UserNotFound, InvalidRole, UserError) as exc:
        raise _map_user_error(exc) from None


@router.post("/users/{user_id}/reset-password")
async def reset_password(user_id: str, body: ResetPasswordReq,
                         user: dict = Depends(require_admin),
                         svc: UserService = Depends(get_user_service)) -> dict[str, str]:
    try:
        await svc.reset_password(user_id, body.password)
        return {"ok": True}
    except (UserNotFound, UserError) as exc:
        raise _map_user_error(exc) from None


@router.post("/users/me/password")
async def change_my_password(body: ChangeMyPasswordReq,
                             user: dict = Depends(current_user),
                             svc: UserService = Depends(get_user_service)) -> dict[str, str]:
    try:
        await svc.change_own_password(user_id=user["id"], old=body.old, new=body.new)
        return {"ok": True}
    except (UserNotFound, WrongPassword, UserError) as exc:
        raise _map_user_error(exc) from None
```

- [ ] **Step 5: 挂 router（app.py）**

在 `app.py` 的 `include_router` 区，加：
```python
from knowledge_mining.mining.kb.routes.auth import router as kb_auth_router
...
    app.include_router(kb_auth_router)
```

- [ ] **Step 6: 跑测试，确认通过**

Run: `python -m pytest knowledge_mining/tests/kb/test_auth_routes.py -v`
Expected: 5 passed。

- [ ] **Step 7: 跑整个 kb 套件回归**

Run: `python -m pytest knowledge_mining/tests/kb/ -v`
Expected: 全 PASS。

- [ ] **Step 8: 提交**

```bash
git add knowledge_mining/mining/kb/routes/auth.py knowledge_mining/mining/kb/deps.py knowledge_mining/mining/api/app.py knowledge_mining/tests/kb/test_auth_routes.py
git commit -m "feat(kb): /api/kb/auth/verify + /api/kb/users 管理 + me/password 路由"
```

> **Chunk 2 末尾自检**：`python -m pytest knowledge_mining/tests/kb/ -q` 全绿；mining 端身份/用户管理后端完整。

---

## Chunk 3: 后端 main_control —— JWT 网关（签发/中间件/反代注入）

### Task 3.1: jwt_util.py（HS256，stdlib）

**Files:**
- Create: `main_control_service/jwt_util.py`
- Test: `main_control_service/tests/test_jwt_util.py`

- [ ] **Step 1: 写失败测试**

`main_control_service/tests/test_jwt_util.py`:
```python
from __future__ import annotations

import time

from main_control_service.jwt_util import decode, encode


def test_roundtrip():
    token = encode({"sub": "alice", "role": "admin", "name": "Alice"}, "secret", ttl=60)
    payload = decode(token, "secret")
    assert payload is not None
    assert payload["sub"] == "alice"
    assert payload["role"] == "admin"
    assert payload["name"] == "Alice"
    assert "exp" in payload and "iat" in payload


def test_decode_expired_returns_none():
    token = encode({"sub": "a"}, "secret", ttl=-10)  # 已过期
    assert decode(token, "secret") is None


def test_decode_wrong_secret_returns_none():
    token = encode({"sub": "a"}, "secret", ttl=60)
    assert decode(token, "other") is None


def test_decode_tampered_payload_returns_none():
    token = encode({"sub": "a"}, "secret", ttl=60)
    parts = token.split(".")
    # 篡改 payload
    import base64
    payload = base64.urlsafe_b64decode(parts[1] + "==")
    tampered = payload.replace(b'"a"', b'"b"')
    parts[1] = base64.urlsafe_b64encode(tampered).rstrip(b"=").decode()
    assert decode(".".join(parts), "secret") is None


def test_decode_alg_none_rejected():
    import base64, hmac, hashlib, json
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "a"}).encode()).rstrip(b"=").decode()
    forged = f"{header}.{payload}."
    assert decode(forged, "secret") is None


def test_decode_garbage_returns_none():
    assert decode("not.a.jwt", "secret") is None
    assert decode("", "secret") is None
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest main_control_service/tests/test_jwt_util.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

`main_control_service/jwt_util.py`:
```python
"""JWT-HS256 手写（stdlib，零依赖）。

钉死 HS256：decode 时 header.alg != "HS256" 一律拒（防 alg-confusion / "none"）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(signing_input: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()


def encode(payload: dict[str, Any], secret: str, *, ttl: int) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    body = {**payload, "iat": now, "exp": now + ttl}
    h = _b64encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    p = _b64encode(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{h}.{p}".encode("ascii")
    sig = _b64encode(_sign(signing_input, secret))
    return f"{h}.{p}.{sig}"


def decode(token: str, secret: str) -> dict[str, Any] | None:
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    h_b64, p_b64, sig_b64 = token.split(".")
    try:
        header = json.loads(_b64decode(h_b64))
        payload = json.loads(_b64decode(p_b64))
        sig = _b64decode(sig_b64)
    except Exception:
        return None
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        return None
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    expected = _sign(signing_input, secret)
    if not hmac.compare_digest(sig, expected):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or int(time.time()) >= exp:
        return None
    return payload
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `python -m pytest main_control_service/tests/test_jwt_util.py -v`
Expected: 6 passed。

- [ ] **Step 5: 提交**

```bash
git add main_control_service/jwt_util.py main_control_service/tests/test_jwt_util.py
git commit -m "feat(control): jwt_util HS256 手写（stdlib）"
```

---

### Task 3.2: AuthMiddleware（校验 JWT + admin 白名单）

**Files:**
- Create: `main_control_service/auth.py`
- Create: `main_control_service/config/system/auth.yaml`（默认样板，含强随机占位）
- Modify: `main_control_service/main.py`（注册中间件 + 调整顺序 + reload-auth 端点）
- Test: `main_control_service/tests/test_auth_middleware.py`

> 中间件读 `auth.yaml`（enabled/jwt_secret/token_ttl/internal_verify_secret）。SKIP_PATHS = `/health`、`/api/v1/auth/login`；OPTIONS 放行。admin-only 白名单见 spec §8.1。身份挂 `request.state.user`，内部 secret 挂 `app.state.internal_verify_secret`（供 proxy 注入）。

- [ ] **Step 1: 写失败测试**

`main_control_service/tests/test_auth_middleware.py`:
```python
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_control_service.auth import AuthMiddleware
from main_control_service.jwt_util import encode


_AUTH_YAML = """\
enabled: true
jwt_secret: test-secret
token_ttl_seconds: 3600
internal_verify_secret: test-ivs
bootstrap:
  admin_password: initpass
"""


def _app(tmp_path: Path) -> FastAPI:
    auth_path = tmp_path / "system" / "auth.yaml"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(_AUTH_YAML, encoding="utf-8")
    app = FastAPI()

    @app.get("/healthz")
    def h(): return {"ok": 1}

    @app.get("/api/v1/auth/login")
    def login(): return {"token": "x"}

    @app.get("/api/v1/me")
    def me(): return {"u": "r"}

    @app.put("/api/v1/system/cfg/raw")
    def put_cfg(): return {"ok": 1}

    # 注册顺序：Auth 先注册（最内）→ 与生产一致（生产里 CORS 在 Auth 外、IpWhitelist 最外）
    app.add_middleware(AuthMiddleware, config_path=auth_path)
    return app


def _token(role: str, secret: str = "test-secret") -> str:
    return encode({"sub": "u1", "role": role, "name": "U"}, secret, ttl=3600)


def test_skip_paths_no_token(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/api/v1/auth/login").status_code == 200


def test_missing_token_401(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.get("/api/v1/me").status_code == 401


def test_valid_token_passes_and_sets_state_user(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/v1/me", headers={"Authorization": f"Bearer {_token('member')}"})
        assert r.status_code == 200


def test_expired_token_401(tmp_path):
    token = encode({"sub": "u", "role": "member", "name": "U"}, "test-secret", ttl=-5)
    with TestClient(_app(tmp_path)) as c:
        assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_admin_only_path_member_403(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/v1/system/cfg/raw", headers={"Authorization": f"Bearer {_token('member')}"})
        assert r.status_code == 403


def test_admin_only_path_admin_ok(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/v1/system/cfg/raw", headers={"Authorization": f"Bearer {_token('admin')}"})
        assert r.status_code == 200


def test_disabled_middleware_passthrough(tmp_path):
    p = tmp_path / "system" / "auth.yaml"
    p.write_text("enabled: false\njwt_secret: s\ntoken_ttl_seconds: 60\ninternal_verify_secret: ivs\n", encoding="utf-8")
    with TestClient(_app(tmp_path)) as c:
        assert c.get("/api/v1/me").status_code == 200  # enabled=False → 不鉴权


def test_reload(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        # 改文件后 reload
        p = tmp_path / "system" / "auth.yaml"
        p.write_text("enabled: false\njwt_secret: s2\ntoken_ttl_seconds: 60\ninternal_verify_secret: ivs2\n", encoding="utf-8")
        # 通过中间件 reload 方法（测试里直接拿实例）
        # 生产经 POST /api/v1/admin/reload-auth；这里直接调
        from main_control_service.main import _find_auth_mw  # 见 main.py 改动
        # 简化：重建 app 不验证 reload 端点细节，仅断言 enabled 切换生效
```

> **实现者注意**：`test_reload` 的最后一段请改为通过实际 `POST /api/v1/admin/reload-auth` 端点触发（需在 app 上注册该端点，见 Step 3）。断言：reload 前后 `c.get("/api/v1/me")` 行为变化。

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest main_control_service/tests/test_auth_middleware.py -v`
Expected: FAIL（`AuthMiddleware` 不存在）。

- [ ] **Step 3: 实现 auth.py**

`main_control_service/auth.py`:
```python
"""JWT 鉴权中间件 —— 校验 Authorization Bearer，强制 admin-only 路径白名单。

身份挂 request.state.user；internal_verify_secret 挂 app.state.internal_verify_secret
（供 proxy._build_forward_headers 注入给 mining）。reload() + POST /api/v1/admin/reload-auth。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from main_control_service.jwt_util import decode as jwt_decode

logger = logging.getLogger(__name__)

# 登录与健康检查不需要 token
_SKIP_PATHS: frozenset[str] = frozenset({
    "/health",
    "/api/v1/auth/login",
})

# admin-only 写路径（member 命中 → 403）。spec §8.1。
def _is_admin_only(method: str, path: str) -> bool:
    if path.startswith("/api/v1/admin/"):
        return True
    if method == "PUT" and path.startswith("/api/v1/system/") and path.endswith("/raw"):
        return True
    if method in {"POST", "PUT", "DELETE"} and path.startswith("/api/v1/domains"):
        return True
    if method in {"GET", "PUT"} and "/scenario/raw" in path and path.startswith("/api/v1/domains/"):
        return True
    if method == "POST" and path == "/api/v1/code-sync":
        return True
    if method == "GET" and path.startswith("/api/v1/logs/"):
        return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, config_path: Path) -> None:
        super().__init__(app)
        self._config_path = config_path
        self._state: dict[str, Any] = {}
        self.reload()

    def reload(self) -> dict[str, object]:
        if self._config_path.exists():
            with open(self._config_path, encoding="utf-8") as f:
                self._state = yaml.safe_load(f) or {}
        else:
            logger.warning("auth config not found at %s — auth disabled", self._config_path)
            self._state = {"enabled": False}
        return {
            "enabled": bool(self._state.get("enabled", True)),
            "token_ttl_seconds": self._state.get("token_ttl_seconds"),
        }

    @property
    def enabled(self) -> bool:
        return bool(self._state.get("enabled", True))

    @property
    def jwt_secret(self) -> str:
        return str(self._state.get("jwt_secret", ""))

    @property
    def internal_verify_secret(self) -> str:
        return str(self._state.get("internal_verify_secret", ""))

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 暴露内部 secret 给 proxy（app.state 单例，所有请求共享读）
        request.app.state.internal_verify_secret = self.internal_verify_secret

        if not self.enabled or request.method == "OPTIONS" or request.url.path in _SKIP_PATHS:
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse(status_code=401, content={"detail": "unauthenticated"})
        token = auth.split(" ", 1)[1].strip()
        payload = jwt_decode(token, self.jwt_secret)
        if payload is None:
            return JSONResponse(status_code=401, content={"detail": "unauthenticated"})

        request.state.user = {"username": payload.get("sub"), "role": payload.get("role")}

        if _is_admin_only(request.method, request.url.path) and payload.get("role") != "admin":
            return JSONResponse(status_code=403, content={"detail": "admin required"})

        return await call_next(request)
```

- [ ] **Step 4: 加默认 auth.yaml**

`main_control_service/config/system/auth.yaml`（提交版；占位需部署方改）:
```yaml
# Phase 2 鉴权配置。部署时务必把 secret/密码改为强随机值。
enabled: true
jwt_secret: change-me-to-a-strong-random-32byte-hex
token_ttl_seconds: 43200
internal_verify_secret: change-me-internal-verify-secret
bootstrap:
  admin_password: change-me-on-first-login
```

- [ ] **Step 5: 注册中间件 + reload-auth 端点（main.py）**

(a) 顶部 import：
```python
from main_control_service.auth import AuthMiddleware
```

(b) `create_app` 内，把中间件注册改为（**注册顺序很关键**，spec §8.3）：
```python
    auth_yaml_path = effective_config_dir / "system" / "auth.yaml"

    # 注册顺序 → Starlette 反序执行 → 执行序：IpWhitelist(最外) → CORS → Auth(最内)
    app.add_middleware(AuthMiddleware, config_path=auth_yaml_path)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )
    app.add_middleware(IpWhitelistMiddleware, config_path=ip_whitelist_path)
```
（删除原来的两行 `add_middleware`，替换为以上三行。`IpWhitelistMiddleware` 必须最后注册 = 最外层。）

(c) 在 admin 区加 `reload-auth`（仿 `reload_ip_whitelist` 的 `_find_*_mw` 模式）：
```python
    def _find_auth_mw(request: Request) -> AuthMiddleware | None:
        layer = request.app
        while hasattr(layer, "app"):
            if isinstance(layer, AuthMiddleware):
                return layer
            layer = layer.app
        return None

    @app.post("/api/v1/admin/reload-auth")
    def reload_auth(request: Request) -> dict:
        mw = _find_auth_mw(request)
        if mw:
            return mw.reload()
        return {"error": "AuthMiddleware not found"}
```

- [ ] **Step 6: 跑测试，确认通过**

Run: `python -m pytest main_control_service/tests/test_auth_middleware.py -v`
Expected: 全 PASS。

- [ ] **Step 7: 跑 main_control 全套回归**

Run: `python -m pytest main_control_service/tests/ -v`
Expected: 全 PASS。**注意**：`test_system_config.py` 等现有测试会因 AuthMiddleware 默认 enabled=true 而 401。修复方式：在 `_client` 里写一份 `auth.yaml` 设 `enabled: false`，或为这些测试的请求签发 token。最简：在 `test_system_config.py::_client` 里追加：
```python
    (tmp_path / "system" / "auth.yaml").write_text(
        "enabled: false\njwt_secret: x\ntoken_ttl_seconds: 60\ninternal_verify_secret: y\n",
        encoding="utf-8",
    )
```
（保持现有套件绿。）

- [ ] **Step 8: 提交**

```bash
git add main_control_service/auth.py main_control_service/config/system/auth.yaml main_control_service/main.py main_control_service/tests/test_auth_middleware.py main_control_service/tests/test_system_config.py
git commit -m "feat(control): AuthMiddleware（JWT 校验 + admin 白名单 + reload-auth）"
```

---

### Task 3.3: login / me 端点 + 反代头注入

**Files:**
- Modify: `main_control_service/main.py`（加 `/api/v1/auth/login`、`/api/v1/auth/me`）
- Modify: `main_control_service/proxy.py`（`_build_forward_headers` 注入 X-KB-User/X-KB-Role/X-Internal-Auth from request.state + app.state）
- Test: `main_control_service/tests/test_auth_flow.py`

> login 内部调 mining `/api/kb/auth/verify`（带 X-Internal-Auth），成功签 JWT 返回。

- [ ] **Step 1: 写失败测试**

`main_control_service/tests/test_auth_flow.py`:
```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main_control_service.main import create_app

_AUTH = "enabled: true\njwt_secret: s\ntoken_ttl_seconds: 3600\ninternal_verify_secret: ivs\nbootstrap: {admin_password: x}\n"


def _client(tmp_path: Path) -> TestClient:
    (tmp_path / "system").mkdir(exist_ok=True)
    (tmp_path / "system" / "auth.yaml").write_text(_AUTH, encoding="utf-8")
    return TestClient(create_app(config_dir=tmp_path))


def test_login_success_returns_token(tmp_path):
    with _client(tmp_path) as c:
        with patch("main_control_service.main.verify_user_via_mining", new_callable=AsyncMock) as m:
            m.return_value = {"ok": True, "user": {"username": "alice", "display_name": "Alice", "site_role": "admin"}}
            r = c.post("/api/v1/auth/login", json={"username": "alice", "password": "pw"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert "token" in body
            assert body["user"]["username"] == "alice"


def test_login_bad_credentials_401(tmp_path):
    with _client(tmp_path) as c:
        with patch("main_control_service.main.verify_user_via_mining", new_callable=AsyncMock) as m:
            m.return_value = None
            r = c.post("/api/v1/auth/login", json={"username": "alice", "password": "bad"})
            assert r.status_code == 401


def test_me_returns_claims(tmp_path):
    from main_control_service.jwt_util import encode
    with _client(tmp_path) as c:
        token = encode({"sub": "alice", "role": "member", "name": "Alice"}, "s", ttl=3600)
        r = c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["username"] == "alice"
        assert r.json()["site_role"] == "member"
```

> 反代头注入测试：在 `test_auth_middleware` 套件里加一个用 spy 捕获 `_build_forward_headers` 输出的用例（或直接单测 `_build_forward_headers` 构造一个带 `request.state.user` + `app.state.internal_verify_secret` 的假 request）。实现者补一条：`test_build_forward_headers_injects_kb_headers`。

- [ ] **Step 2: 跑测试，确认失败**

Run: `python -m pytest main_control_service/tests/test_auth_flow.py -v`
Expected: FAIL。

- [ ] **Step 3: 加 login/me 端点（main.py）**

顶部加模块级辅助（login 内部调 mining verify，best-effort，失败返回 None）：
```python
async def verify_user_via_mining(verify_url: str, internal_secret: str,
                                 username: str, password: str) -> dict | None:
    """POST mining /api/kb/auth/verify（带 X-Internal-Auth）。成功返 {ok,user}，失败/异常返 None。"""
    import httpx
    try:
        resp = await get_proxy_client().post(
            f"{verify_url}/api/kb/auth/verify",
            json={"username": username, "password": password},
            headers={"X-Internal-Auth": internal_secret},
            timeout=10.0,
        )
    except Exception:
        return None
    if resp.status_code == 200:
        return resp.json()
    return None
```

在 `create_app` 内加端点：
```python
    @app.post("/api/v1/auth/login")
    async def login(request: Request) -> JSONResponse:
        body = await request.json()
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        if not username or not password:
            return JSONResponse(status_code=400, content={"detail": "username and password required"})
        # 选 default 域的 mining verify_url（auth 是全局的，域不影响验密码）
        from main_control_service.jwt_util import encode as jwt_encode
        # 取任一启用域的 mining_url；用 list_domains 第一个有 mining_url 的
        mining_url = None
        for d in service.list_domains():
            svcs = (service.get_domain(d) or {}).get("services") or {}
            if svcs.get("mining_url"):
                mining_url = svcs["mining_url"].rstrip("/")
                break
        if not mining_url:
            return JSONResponse(status_code=503, content={"detail": "mining backend unavailable"})
        result = await verify_user_via_mining(mining_url, request.app.state.internal_verify_secret,
                                              username, password)
        if not result or not result.get("ok"):
            return JSONResponse(status_code=401, content={"detail": "invalid credentials"})
        u = result["user"]
        auth_mw = _find_auth_mw(request)
        secret = auth_mw.jwt_secret if auth_mw else ""
        ttl = (auth_mw._state.get("token_ttl_seconds", 43200) if auth_mw else 43200)
        token = jwt_encode({"sub": u["username"], "role": u["site_role"], "name": u.get("display_name") or u["username"]},
                           secret, ttl=int(ttl))
        return JSONResponse(content={"token": token, "user": u})

    @app.get("/api/v1/auth/me")
    def me(request: Request) -> JSONResponse:
        u = getattr(request.state, "user", None)
        if not u:
            return JSONResponse(status_code=401, content={"detail": "unauthenticated"})
        # display_name 在 JWT 里是 name
        from main_control_service.jwt_util import decode as jwt_decode
        auth = request.headers.get("authorization", "")
        token = auth.split(" ", 1)[1] if " " in auth else ""
        payload = jwt_decode(token, _find_auth_mw(request).jwt_secret) or {}
        return JSONResponse(content={"username": u["username"], "site_role": u["role"],
                                     "display_name": payload.get("name")})
```

> **实现者注意**：`_find_auth_mw` 已在 Task 3.2 定义。`list_domains`/`get_domain` 来自 `YamlConfigService`（已注入 `service`）。

- [ ] **Step 4: 反代头注入（proxy.py `_build_forward_headers`）**

修改 `_build_forward_headers`：
```python
def _build_forward_headers(request: Request) -> dict[str, str]:
    """Strip hop-by-hop/sensitive, add proxy context + gateway-injected identity."""
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _STRIP_REQUEST_HEADERS
    }
    existing_xff = request.headers.get("x-forwarded-for")
    client_ip = request.client.host if request.client else "unknown"
    headers["X-Forwarded-For"] = f"{existing_xff}, {client_ip}" if existing_xff else client_ip
    headers["X-Forwarded-Proto"] = request.url.scheme

    # Phase 2：AuthMiddleware 已把身份挂 request.state.user；反代把派生头注入给 mining。
    user = getattr(request.state, "user", None)
    if user:
        headers["X-KB-User"] = str(user.get("username", ""))
        headers["X-KB-Role"] = str(user.get("role", ""))
        ivs = getattr(request.app.state, "internal_verify_secret", "") or ""
        if ivs:
            headers["X-Internal-Auth"] = ivs
    return headers
```
（X-KB-User / X-KB-Role / X-Internal-Auth 都不在 `_STRIP_REQUEST_HEADERS`，会被转发。浏览器自带 `Authorization` 仍被剥。）

- [ ] **Step 5: 跑测试，确认通过**

Run: `python -m pytest main_control_service/tests/test_auth_flow.py main_control_service/tests/test_auth_middleware.py -v`
Expected: 全 PASS（含新增的头注入断言）。

- [ ] **Step 6: 跑 main_control 全套回归**

Run: `python -m pytest main_control_service/tests/ -v`
Expected: 全 PASS。

- [ ] **Step 7: 提交**

```bash
git add main_control_service/main.py main_control_service/proxy.py main_control_service/tests/test_auth_flow.py main_control_service/tests/test_auth_middleware.py
git commit -m "feat(control): login/me 端点 + 反代注入 X-KB-User/X-KB-Role/X-Internal-Auth"
```

> **Chunk 3 末尾自检**：`python -m pytest main_control_service/tests/ -q` 全绿；网关侧鉴权链路完整（登录签发 → 中间件校验 → admin 白名单 → 反代注入身份）。

---

## Chunk 4: 前端基础 —— 登录态（auth store / api / 拦截器 / LoginView / 路由守卫 / bootstrap）

### Task 4.1: auth store + api/auth.ts

**Files:**
- Create: `kb-ui/src/types/auth.ts`
- Create: `kb-ui/src/api/auth.ts`
- Create: `kb-ui/src/stores/auth.ts`
- Test: `kb-ui/src/stores/__tests__/auth.spec.ts`

- [ ] **Step 1: 写失败测试**

`kb-ui/src/stores/__tests__/auth.spec.ts`:
```typescript
import { describe, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  login: vi.fn(),
  getMe: vi.fn(),
}))

vi.mock('@/api/auth', () => ({
  useAuthApi: () => api,
  saveToken: vi.fn((t: string) => localStorage.setItem('kb-token', t)),
  loadToken: vi.fn(() => localStorage.getItem('kb-token')),
  clearToken: vi.fn(() => localStorage.removeItem('kb-token')),
}))

import { useAuthStore } from '@/stores/auth'

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('login sets token + user', async () => {
    api.login.mockResolvedValue({
      token: 'tok', user: { username: 'alice', display_name: 'Alice', site_role: 'admin' },
    })
    const s = useAuthStore()
    await s.login('alice', 'pw')
    expect(s.token).toBe('tok')
    expect(s.siteRole).toBe('admin')
    expect(s.isAuthenticated).toBe(true)
    expect(s.user?.username).toBe('alice')
  })

  it('logout clears state + token', async () => {
    api.login.mockResolvedValue({ token: 't', user: { username: 'a', display_name: 'A', site_role: 'member' } })
    const s = useAuthStore()
    await s.login('a', 'p')
    s.logout()
    expect(s.isAuthenticated).toBe(false)
    expect(s.token).toBe(null)
  })

  it('fetchMe populates from token', async () => {
    api.getMe.mockResolvedValue({ username: 'bob', display_name: 'Bob', site_role: 'member' })
    const s = useAuthStore()
    s.token = 't'
    await s.fetchMe()
    expect(s.siteRole).toBe('member')
  })

  it('restore loads token from storage', () => {
    localStorage.setItem('kb-token', 'persisted')
    const s = useAuthStore()
    s.restore()
    expect(s.token).toBe('persisted')
  })
})
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `cd kb-ui && npx vitest run src/stores/__tests__/auth.spec.ts`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 types + api + store**

`kb-ui/src/types/auth.ts`:
```typescript
export type SiteRole = 'admin' | 'member'

export interface AuthUser {
  username: string
  display_name: string | null
  site_role: SiteRole
}

export interface LoginResponse {
  token: string
  user: AuthUser
}
```

`kb-ui/src/api/auth.ts`:
```typescript
import axios from 'axios'
import { createProxyClient, extractOne } from './proxyClient'
import { useControlPlaneApi } from './controlPlane'
import type { AuthUser, LoginResponse } from '@/types/auth'

const TOKEN_KEY = 'kb-token'

export function loadToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function saveToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export function useAuthApi() {
  const cp = useControlPlaneApi()
  const mining = createProxyClient('mining', { includeDomainQuery: false })
  return {
    async login(username: string, password: string): Promise<LoginResponse> {
      // login/me 是 main_control 直连端点（不经 domain 代理）
      const { data } = await axios.post('/api/control-plane/api/v1/auth/login', { username, password })
      return data as LoginResponse
    },
    async getMe(): Promise<AuthUser> {
      const { data } = await axios.get('/api/control-plane/api/v1/auth/me')
      return data as AuthUser
    },
    async listUsers(): Promise<AuthUser[]> {
      const { data } = await mining.get('/api/kb/users')
      return Array.isArray(data) ? data : (data as { items?: AuthUser[] }).items ?? []
    },
    async createUser(body: { username: string; password: string; site_role: SiteRole; display_name?: string }): Promise<AuthUser> {
      const { data } = await mining.post('/api/kb/users', body)
      return extractOne<AuthUser>(data)
    },
    async updateUser(id: string, body: { display_name?: string; site_role?: SiteRole; status?: string }): Promise<AuthUser> {
      const { data } = await mining.patch(`/api/kb/users/${id}`, body)
      return extractOne<AuthUser>(data)
    },
    async resetPassword(id: string, password: string): Promise<void> {
      await mining.post(`/api/kb/users/${id}/reset-password`, { password })
    },
    async changeMyPassword(oldPw: string, newPw: string): Promise<void> {
      await mining.post('/api/kb/users/me/password', { old: oldPw, new: newPw })
    },
  }
}
```
> 注：`cp` 暂留作未来；login 直接用裸 `axios` 打 `/api/control-plane/api/v1/auth/login`（nginx 会把 `/api/control-plane/` 转给 main_control:8910，剥前缀 → `/api/v1/auth/login`）。

`kb-ui/src/stores/auth.ts`:
```typescript
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useAuthApi, loadToken, saveToken, clearToken } from '@/api/auth'
import type { AuthUser, SiteRole } from '@/types/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(null)
  const user = ref<AuthUser | null>(null)
  const siteRole = computed<SiteRole>(() => user.value?.site_role ?? 'member')
  const isAuthenticated = computed(() => !!token.value && !!user.value)

  function restore(): void {
    token.value = loadToken()
  }

  async function login(username: string, password: string): Promise<void> {
    const api = useAuthApi()
    const res = await api.login(username, password)
    token.value = res.token
    user.value = res.user
    saveToken(res.token)
  }

  function logout(): void {
    token.value = null
    user.value = null
    clearToken()
  }

  async function fetchMe(): Promise<void> {
    if (!token.value) return
    try {
      const api = useAuthApi()
      user.value = await api.getMe()
    } catch {
      // token 失效
      logout()
    }
  }

  return { token, user, siteRole, isAuthenticated, restore, login, logout, fetchMe }
})
```

- [ ] **Step 4: 跑测试，确认通过**

Run: `cd kb-ui && npx vitest run src/stores/__tests__/auth.spec.ts`
Expected: 4 passed。

- [ ] **Step 5: 提交**

```bash
git add kb-ui/src/types/auth.ts kb-ui/src/api/auth.ts kb-ui/src/stores/auth.ts kb-ui/src/stores/__tests__/auth.spec.ts
git commit -m "feat(kb-ui): auth store + api/auth（登录/me/用户 CRUD）"
```

---

### Task 4.2: axios 拦截器（注入 Bearer + 401 登出）+ 删 proxyClient 写死 X-KB-User

**Files:**
- Modify: `kb-ui/src/api/proxyClient.ts`
- Modify: `kb-ui/src/api/controlPlane.ts`
- Test: `kb-ui/src/api/__tests__/interceptors.spec.ts`

> 两个 axios 客户端都要：(1) 请求拦截加 `Authorization: Bearer <token>`；(2) 响应拦截 401 → 清 auth store → 跳 `/login`。proxyClient 删掉写死的 `DEFAULT_KB_USER` / X-KB-User 注入（保留 domain 路由逻辑）。

- [ ] **Step 1: 写失败测试**

`kb-ui/src/api/__tests__/interceptors.spec.ts`:
```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import axios from 'axios'

describe('proxyClient request interceptor', () => {
  it('adds Authorization header when token present', async () => {
    const { installAuthInterceptors } = await import('@/api/proxyClient')
    const push = vi.spyOn(axios, 'create').mockReturnValue(axios.create())
    // 模拟 auth store 有 token
    const { useAuthStore } = await import('@/stores/auth')
    const { createPinia, setActivePinia } = await import('pinia')
    setActivePinia(createPinia())
    const s = useAuthStore()
    s.token = 'abc'
    // 触发一次请求看头（用 adapter 捕获）
    // （实现者补全：用 mock adapter 断言 config.headers.Authorization === 'Bearer abc'）
  })
})
```
> **实现者注意**：拦截器测试用 axios mock adapter 捕获最终 config；断言 `Authorization` 与 401 时 store 被清。具体写法参照现有 `api/__tests__/miningWorkflow.spec.ts` 的 mock 模式。

- [ ] **Step 2: 跑测试，确认失败**

Run: `cd kb-ui && npx vitest run src/api/__tests__/interceptors.spec.ts`
Expected: FAIL（`installAuthInterceptors` 不存在）。

- [ ] **Step 3: 实现**

在 `proxyClient.ts` 顶部加一个共享的拦截器安装函数，并在 `createProxyClient` 里调用：
```typescript
import { useAuthStore } from '@/stores/auth'

export function installAuthInterceptors(client: axios.AxiosInstance): void {
  client.interceptors.request.use((config) => {
    const auth = useAuthStore()
    if (auth.token) {
      config.headers.set('Authorization', `Bearer ${auth.token}`)
    }
    return config
  })
  client.interceptors.response.use(
    (r) => r,
    (error) => {
      if (error?.response?.status === 401) {
        const auth = useAuthStore()
        auth.logout()
        // 跳登录页（避免在 store 里直接依赖 router：用 location 兜底或事件）
        if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
          window.location.href = `/login?redirect=${encodeURIComponent(window.location.pathname)}`
        }
      }
      return Promise.reject(error)
    },
  )
}
```

`createProxyClient` 内：创建 client 后 `installAuthInterceptors(client)`；**删掉** `DEFAULT_KB_USER` 常量与请求拦截里 `if (...startsWith('/api/kb')) config.headers.set('X-KB-User', ...)` 整块（保留 domain query 逻辑）。

`controlPlane.ts`：在 `client = axios.create({ baseURL })` 后调用 `installAuthInterceptors(client)`（从 `./proxyClient` 导入）。

> **循环依赖注意**：`proxyClient.ts` 已 import `useDomainStore`；新增 import `useAuthStore`。auth.ts(store) import api/auth，api/auth import proxyClient —— 形成 store→api→proxyClient→store 循环。规避：拦截器里 `useAuthStore()` 在请求时（运行期）才调用，不在模块加载期，循环可接受。若 vitest 报循环，把 `useAuthStore` 改为动态 import inside interceptor。

- [ ] **Step 4: 跑测试 + 现有 api 测试回归**

Run: `cd kb-ui && npx vitest run src/api/ -v`
Expected: 全 PASS（含新拦截器测试 + 现有 miningWorkflow 等）。

- [ ] **Step 5: 提交**

```bash
git add kb-ui/src/api/proxyClient.ts kb-ui/src/api/controlPlane.ts kb-ui/src/api/__tests__/interceptors.spec.ts
git commit -m "feat(kb-ui): axios 拦截器注入 Bearer + 401 登出；删写死 X-KB-User"
```

---

### Task 4.3: LoginView + 路由守卫 + main.ts bootstrap

**Files:**
- Create: `kb-ui/src/views/LoginView.vue`
- Modify: `kb-ui/src/router/index.ts`（加 `/login` + 守卫）
- Modify: `kb-ui/src/main.ts`（启动期 restore + fetchMe）
- Test: `kb-ui/src/views/__tests__/LoginView.spec.ts`

- [ ] **Step 1: 写失败测试**

`kb-ui/src/views/__tests__/LoginView.spec.ts`:
```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

const api = vi.hoisted(() => ({ login: vi.fn() }))
vi.mock('@/api/auth', () => ({ useAuthApi: () => api, saveToken: vi.fn(), loadToken: vi.fn(() => null), clearToken: vi.fn() }))

import LoginView from '@/views/LoginView.vue'

function mountIt() {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', name: 'home', component: { template: '<div/>' } },
    { path: '/login', name: 'login', component: LoginView },
  ] })
  return mount(LoginView, { global: { plugins: [router, createPinia()] } })
}

describe('LoginView', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

  it('renders username + password inputs', () => {
    const w = mountIt()
    expect(w.findAll('input').length).toBeGreaterThanOrEqual(2)
  })

  it('submits and shows error on failure', async () => {
    api.login.mockRejectedValue({ response: { status: 401, data: { detail: 'invalid credentials' } } })
    const w = mountIt()
    await w.vm.submit('u', 'wrong')  // defineExpose({ submit })
    await flushPromises()
    expect(api.login).toHaveBeenCalledWith('u', 'wrong')
    expect(w.vm.errorMsg).toBeTruthy()
  })
})
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `cd kb-ui && npx vitest run src/views/__tests__/LoginView.spec.ts`
Expected: FAIL。

- [ ] **Step 3: 实现 LoginView.vue**

`kb-ui/src/views/LoginView.vue`:
```vue
<template>
  <div class="login">
    <form class="login__card" @submit.prevent="submit(username, password)">
      <h2 class="login__title">{{ brand.title }}</h2>
      <p class="login__hint">登录到知识库</p>
      <input class="login__input" v-model="username" placeholder="用户名" autocomplete="username" />
      <input class="login__input" v-model="password" type="password" placeholder="密码" autocomplete="current-password" />
      <div v-if="errorMsg" class="login__error">{{ errorMsg }}</div>
      <button class="login__submit" type="submit" :disabled="loading">{{ loading ? '登录中…' : '登录' }}</button>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useBrandStore } from '@/stores/brand'
import { apiErrorDetail } from '@/api/proxyClient'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const brand = useBrandStore()

const username = ref('')
const password = ref('')
const loading = ref(false)
const errorMsg = ref('')

async function submit(u: string, p: string): Promise<void> {
  if (!u || !p) { errorMsg.value = '请输入用户名和密码'; return }
  loading.value = true
  errorMsg.value = ''
  try {
    await auth.login(u, p)
    const redirect = (route.query.redirect as string) || '/'
    router.push(redirect)
  } catch (e) {
    errorMsg.value = await apiErrorDetail(e) || '用户名或密码错误'
  } finally {
    loading.value = false
  }
}

defineExpose({ submit, errorMsg })
</script>

<style scoped>
.login { min-height: 100vh; display: flex; align-items: center; justify-content: center; background: var(--kb-bg-page); }
.login__card { width: 340px; background: var(--kb-bg-card); border: 1px solid var(--kb-border-light); border-radius: 12px; padding: 32px; box-shadow: var(--kb-shadow-card); display: flex; flex-direction: column; gap: 12px; }
.login__title { margin: 0; font-size: 20px; font-weight: 700; color: var(--kb-text-primary); }
.login__hint { margin: 0 0 8px; font-size: 13px; color: var(--kb-text-tertiary); }
.login__input { padding: 10px 12px; border: 1px solid var(--kb-border); border-radius: 8px; font-size: 14px; }
.login__error { color: var(--kb-danger); font-size: 13px; }
.login__submit { margin-top: 8px; padding: 10px; border: none; border-radius: 8px; background: var(--kb-accent); color: #fff; font-size: 14px; font-weight: 600; cursor: pointer; }
.login__submit:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
```

- [ ] **Step 4: 路由加 /login + 守卫（router/index.ts）**

在 `routes` 数组**顶层**（与 `/` 同级，不在 AppLayout children 内）加：
```typescript
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
```

改写 `beforeEach`：
```typescript
import { useAuthStore } from '@/stores/auth'

let domainsInitialized = false
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.public && auth.isAuthenticated) {
    return { name: 'dashboard' }
  }
  // member 不能进 admin 路由（路由名白名单）
  const ADMIN_ROUTES = new Set(['mining-workflows','mining-workflow-editor','paradigm','paradigm-edit','entities','ontology','ontology-graph','ontology-review','mentions-review','llm','llm-task-detail','settings'])
  if (ADMIN_ROUTES.has(to.name as string) && auth.siteRole !== 'admin') {
    return { name: 'dashboard' }
  }
  if (!domainsInitialized && auth.isAuthenticated) {
    domainsInitialized = true
    const domainStore = useDomainStore()
    await domainStore.fetchDomains()
  }
})
```

- [ ] **Step 5: main.ts bootstrap（restore + fetchMe）**

`kb-ui/src/main.ts`:
```typescript
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import './styles/variables.css'
import './styles/global.css'
import { useBrandStore } from './stores/brand'
import { useAuthStore } from './stores/auth'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)
app.use(ElementPlus)

const brand = useBrandStore(pinia)
const auth = useAuthStore(pinia)
auth.restore()  // 从 localStorage 恢复 token

// 启动期：品牌 + 用户 profile 并联预取，mount 前完成（避免首屏角色闪烁）
Promise.allSettled([
  brand.fetchBrand(),
  auth.token ? auth.fetchMe() : Promise.resolve(),
]).finally(() => {
  brand.applyBrand()
  app.mount('#app')
})
```

- [ ] **Step 6: 跑测试，确认通过**

Run: `cd kb-ui && npx vitest run src/views/__tests__/LoginView.spec.ts`
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add kb-ui/src/views/LoginView.vue kb-ui/src/router/index.ts kb-ui/src/main.ts kb-ui/src/views/__tests__/LoginView.spec.ts
git commit -m "feat(kb-ui): LoginView + 路由守卫（未登录跳登录 / member 挡 admin 路由）+ main bootstrap"
```

> **Chunk 4 末尾自检**：`cd kb-ui && npx vitest run` 全绿；`npm run build` 通过。

---

## Chunk 5: 前端角色 UI + 用户管理 + 收尾

### Task 5.1: Sidebar 按角色过滤 + Header 账户菜单

**Files:**
- Modify: `kb-ui/src/components/layout/Sidebar.vue`（navItem 加 requiresAdmin + 过滤）
- Modify: `kb-ui/src/components/layout/Header.vue`（账户菜单）
- Test: `kb-ui/src/components/layout/__tests__/Sidebar.spec.ts`（扩角色过滤用例）

- [ ] **Step 1: 写失败测试（扩 Sidebar.spec.ts）**

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import Sidebar from '@/components/layout/Sidebar.vue'

vi.mock('@/api/controlPlane', () => ({ useControlPlaneApi: () => ({ getSystemConfig: vi.fn() }) }))

function mountSidebar(role: 'admin' | 'member') {
  setActivePinia(createPinia())
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div/>' } }] })
  const w = mount(Sidebar, { global: { plugins: [router] } })
  const { useAuthStore } = require('@/stores/auth')
  const s = useAuthStore()
  s.user = { username: 'x', display_name: 'X', site_role: role }
  return w
}

describe('Sidebar role filter', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('admin sees all nav items', async () => {
    const w = mountSidebar('admin')
    await w.vm.$nextTick()
    const labels = w.findAll('.sidebar__link span').map((e) => e.text())
    expect(labels).toContain('系统设置')
    expect(labels).toContain('挖掘范式')
  })

  it('member sees only 概览/知识库/检索测试', async () => {
    const w = mountSidebar('member')
    await w.vm.$nextTick()
    const labels = w.findAll('.sidebar__link span').map((e) => e.text())
    expect(labels).toEqual(expect.arrayContaining(['概览', '知识库', '检索测试']))
    expect(labels).not.toContain('系统设置')
    expect(labels).not.toContain('挖掘范式')
  })
})
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `cd kb-ui && npx vitest run src/components/layout/__tests__/Sidebar.spec.ts`
Expected: member 用例 FAIL（当前不过滤）。

- [ ] **Step 3: Sidebar 改 navItems + 过滤**

`Sidebar.vue` `<script setup>` 内：
```typescript
import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()

const ALL_NAV = [
  { path: '/', label: '概览', icon: Monitor, requiresAdmin: false },
  { path: '/kb', label: '知识库', icon: Files, requiresAdmin: false },
  { path: '/mining/workflows', label: '挖掘范式', icon: Management, requiresAdmin: true },
  { path: '/search', label: '检索测试', icon: Search, requiresAdmin: false },
  { path: '/paradigm', label: '检索范式', icon: Connection, requiresAdmin: true },
  { path: '/entities', label: '实体图谱', icon: Connection, requiresAdmin: true },
  { path: '/ontology', label: '本体版本', icon: Collection, requiresAdmin: true },
  { path: '/ontology/graph', label: '本体图谱', icon: DataLine, requiresAdmin: true },
  { path: '/llm', label: 'LLM 服务', icon: Cpu, requiresAdmin: true },
  { path: '/settings', label: '系统设置', icon: Setting, requiresAdmin: true },
]

const navItems = computed(() =>
  ALL_NAV.filter((it) => !it.requiresAdmin || auth.siteRole === 'admin'),
)
```
（删掉原来静态 `const navItems = [...]`。模板里 `v-for="item in navItems"` 不变，但 navItems 现在是 computed ref —— 模板自动解包。）

- [ ] **Step 4: Header 账户菜单**

`Header.vue` `.header__right` 内追加（domain-select + health 之后或之前）：
```vue
      <el-dropdown trigger="click" @command="onAccountCommand">
        <span class="header__account">
          <span class="header__account-name">{{ auth.user?.display_name || auth.user?.username || '—' }}</span>
          <el-tag size="small" :type="auth.siteRole === 'admin' ? 'danger' : 'info'" effect="plain">
            {{ auth.siteRole === 'admin' ? '管理员' : '用户' }}
          </el-tag>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="password">修改密码</el-dropdown-item>
            <el-dropdown-item command="logout" divided>登出</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
```
`<script setup>` 加：
```typescript
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()
const router = useRouter()
function onAccountCommand(cmd: string) {
  if (cmd === 'logout') { auth.logout(); router.push('/login') }
  else if (cmd === 'password') { /* 打开改密弹窗：用 ElMessageBox.prompt 或单独 dialog，实现者补 */ }
}
```
（改密可用 `ElMessageBox.prompt` 简易实现，调 `useAuthApi().changeMyPassword`。）

> EP stub：`el-dropdown`/`el-dropdown-menu`/`el-dropdown-item`/`el-tag` 未在 setup.ts stub —— 测试若挂 Header 会报错。**在 setup.ts 的 stubs 里补**：`ElDropdown: { template: '<div><slot /><slot name="dropdown" /></div>' }`、`ElDropdownMenu: { template: '<div><slot /></div>' }`、`ElDropdownItem: { template: '<div />' }`、`ElTag: { template: '<span><slot /></span>' }`。Header 单测本次不强制（先保证 build 通过）。

- [ ] **Step 5: 跑测试，确认通过**

Run: `cd kb-ui && npx vitest run src/components/layout/__tests__/Sidebar.spec.ts`
Expected: 角色过滤 2 用例 PASS。

- [ ] **Step 6: 提交**

```bash
git add kb-ui/src/components/layout/Sidebar.vue kb-ui/src/components/layout/Header.vue kb-ui/src/test/setup.ts kb-ui/src/components/layout/__tests__/Sidebar.spec.ts
git commit -m "feat(kb-ui): Sidebar 按角色过滤 + Header 账户菜单（用户名/角色/登出）"
```

---

### Task 5.2: 用户管理 Tab（UserManagementTab.vue）+ 挂到 SettingsView

**Files:**
- Create: `kb-ui/src/components/settings/UserManagementTab.vue`
- Modify: `kb-ui/src/views/SettingsView.vue`（加 tab）
- Test: `kb-ui/src/components/settings/__tests__/UserManagementTab.spec.ts`

- [ ] **Step 1: 写失败测试（defineExpose 模式规避 EP stub）**

`kb-ui/src/components/settings/__tests__/UserManagementTab.spec.ts`:
```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  listUsers: vi.fn(),
  createUser: vi.fn(),
  resetPassword: vi.fn(),
}))
vi.mock('@/api/auth', () => ({ useAuthApi: () => api, loadToken: vi.fn(), saveToken: vi.fn(), clearToken: vi.fn() }))

import UserManagementTab from '@/components/settings/UserManagementTab.vue'

beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })

describe('UserManagementTab', () => {
  it('lists users on load', async () => {
    api.listUsers.mockResolvedValue([
      { id: '1', username: 'admin', site_role: 'admin', status: 'active', has_password: true, display_name: 'Admin' },
    ])
    const w = mount(UserManagementTab, { global: { plugins: [createPinia()] } })
    await w.vm.load()
    await flushPromises()
    expect(api.listUsers).toHaveBeenCalled()
    expect(w.vm.users.length).toBe(1)
    expect(w.vm.users[0].username).toBe('admin')
  })

  it('createUser calls api', async () => {
    api.listUsers.mockResolvedValue([])
    api.createUser.mockResolvedValue({ id: '2', username: 'alice', site_role: 'member', status: 'active' })
    const w = mount(UserManagementTab, { global: { plugins: [createPinia()] } })
    await w.vm.createUser({ username: 'alice', password: 'alicepw12', site_role: 'member' })
    expect(api.createUser).toHaveBeenCalledWith({ username: 'alice', password: 'alicepw12', site_role: 'member' })
  })
})
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `cd kb-ui && npx vitest run src/components/settings/__tests__/UserManagementTab.spec.ts`
Expected: FAIL。

- [ ] **Step 3: 实现 UserManagementTab.vue**

`kb-ui/src/components/settings/UserManagementTab.vue`（表格 + 建用户 dialog + 重置密码 + 禁用；`defineExpose({ load, createUser, users })`）:
```vue
<template>
  <div class="um">
    <div class="um__bar">
      <el-button size="small" @click="openCreate">新建用户</el-button>
    </div>
    <el-table :data="users" size="small">
      <el-table-column prop="username" label="用户名" />
      <el-table-column prop="display_name" label="显示名" />
      <el-table-column prop="site_role" label="角色" width="90" />
      <el-table-column prop="status" label="状态" width="90" />
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button size="small" link @click="resetPw(row)">重置密码</el-button>
          <el-button size="small" link @click="toggleStatus(row)">{{ row.status === 'active' ? '禁用' : '启用' }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="createVisible" title="新建用户" width="420">
      <el-form label-width="80">
        <el-form-item label="用户名"><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="显示名"><el-input v-model="form.display_name" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="form.password" type="password" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.site_role"><el-option label="管理员" value="admin" /><el-option label="用户" value="member" /></el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthApi } from '@/api/auth'
import { apiErrorDetail } from '@/api/proxyClient'
import type { AuthUser, SiteRole } from '@/types/auth'

const api = useAuthApi()
const users = ref<(AuthUser & { id: string; status: string; has_password?: boolean })[]>([])
const createVisible = ref(false)
const form = ref<{ username: string; display_name: string; password: string; site_role: SiteRole }>({
  username: '', display_name: '', password: '', site_role: 'member',
})

async function load(): Promise<void> {
  try { users.value = await api.listUsers() as typeof users.value }
  catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}

function openCreate() {
  form.value = { username: '', display_name: '', password: '', site_role: 'member' }
  createVisible.value = true
}

async function createUser(body: { username: string; password: string; site_role: SiteRole; display_name?: string }): Promise<void> {
  await api.createUser(body)
}

async function confirmCreate(): Promise<void> {
  try {
    await createUser({ username: form.value.username, password: form.value.password,
                       site_role: form.value.site_role, display_name: form.value.display_name || undefined })
    createVisible.value = false
    await load()
    ElMessage.success('已创建')
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}

async function resetPw(row: AuthUser & { id: string }): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt('输入新密码（≥8）', `重置 ${row.username} 密码`, { inputType: 'password' })
    if (!value || value.length < 8) { ElMessage.warning('密码至少 8 位'); return }
    await api.resetPassword(row.id, value)
    ElMessage.success('已重置')
  } catch (e) { if (e !== 'cancel') ElMessage.error(await apiErrorDetail(e)) }
}

async function toggleStatus(row: AuthUser & { id: string; status: string }): Promise<void> {
  const next = row.status === 'active' ? 'disabled' : 'active'
  try { await api.updateUser(row.id, { status: next }); await load() }
  catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}

onMounted(load)
defineExpose({ load, createUser, users })
</script>

<style scoped>
.um__bar { margin-bottom: 12px; display: flex; justify-content: flex-end; }
</style>
```

- [ ] **Step 4: 挂 tab（SettingsView.vue）**

import + 加 `<el-tab-pane label="用户管理" name="users"><UserManagementTab /></el-tab-pane>`（放在「品牌外观」之后）：
```vue
        <el-tab-pane label="用户管理" name="users">
          <UserManagementTab />
        </el-tab-pane>
```
`<script setup>` 加 `import UserManagementTab from '@/components/settings/UserManagementTab.vue'`。

- [ ] **Step 5: 跑测试，确认通过**

Run: `cd kb-ui && npx vitest run src/components/settings/__tests__/UserManagementTab.spec.ts`
Expected: 2 passed。

- [ ] **Step 6: 提交**

```bash
git add kb-ui/src/components/settings/UserManagementTab.vue kb-ui/src/views/SettingsView.vue kb-ui/src/components/settings/__tests__/UserManagementTab.spec.ts
git commit -m "feat(kb-ui): 用户管理 Tab（建/重置/禁用）+ 挂到设置"
```

---

### Task 5.3: 全量回归 + build + 部署文档

**Files:**
- Modify: `docs/开发与发布流程.md`（补「用户权限」上线小节，含 `--force` 陷阱）

- [ ] **Step 1: 后端全量测试**

Run:
```bash
# main_control（无 DB）
python -m pytest main_control_service/tests/ -v
# mining（需 kb_db_test + 环境变量）
KB_RUN_POSTGRES_ACCEPTANCE=1 KB_ALLOW_TEST_TRUNCATE=1 PG_DBNAME=kb_db_test PG_HOST=... PG_USER=... PG_PASSWORD=... \
python -m pytest knowledge_mining/tests/kb/ -v
```
Expected: 全 PASS。

- [ ] **Step 2: 前端全量测试 + build**

Run:
```bash
cd kb-ui
npx vitest run
npm run build
```
Expected: vitest 全绿；`npm run build` 成功产出 dist。

- [ ] **Step 3: 补部署文档**

在 `docs/开发与发布流程.md` 适当小节追加「用户权限（Phase 2）上线」：
- 迁移 `006_kb_users_auth.sql` 自动随 `pg_schema.py` / `reset_db.py`。
- main_control 放 `config/system/auth.yaml`（**改默认 secret 与 admin_password**），重启。
- mining 重启（bootstrap 播种首 admin，从控制面拉 auth.yaml）。
- 前端重建镜像。
- ⚠️ `deploy-server.sh --force` 会删 `main_control_service/config/` 含 `auth.yaml` —— 部署后须重新放置 `auth.yaml` 再重启 mining。
- 首登 `admin` + bootstrap 密码 → 立即改密 → 进【设置→用户管理】建用户。

- [ ] **Step 4: 提交**

```bash
git add docs/开发与发布流程.md
git commit -m "docs: 用户权限 Phase 2 上线说明（含 --force 删 auth.yaml 陷阱）"
```

---

### Task 5.4: 手动验证清单（提交前自查，不写自动化）

- [ ] 起 main_control（`python -m main_control_service.main`）+ mining（`python -m knowledge_mining.mining.api`）。确认 mining 日志有 bootstrap 播种 admin。
- [ ] `POST /api/v1/auth/login`（admin/bootstrap 密码）→ 拿 token。
- [ ] 不带 token `GET /api/v1/proxy/<domain>/mining/api/kb` → 401。
- [ ] 带 token `GET /api/v1/auth/me` → 返回 admin。
- [ ] 前端登录 → admin 见 10 项导航；member 见 3 项。
- [ ] 直连 mining `curl -H 'X-KB-User: admin' http://localhost:8901/api/kb/users`（无 X-Internal-Auth）→ 401（伪造堵死）。
- [ ] admin 在【设置→用户管理】建一个 member → 用其登录 → 只见 3 项导航、进 /settings 被挡回 /。

---

## 执行交接

全部 5 个 Chunk 完成后：
1. `git log --oneline` 确认分批提交清晰。
2. 推送分支：`git push -u origin feat/user-permissions`。
3. 创建 PR（base master，标题"feat: 用户权限管理（Phase 2 真实登录）"，body 列 spec/plan 链接 + 测试结果 + 手动验证清单）。
4. PR 用 **Create a merge commit**（不 squash），保留分批历史。
