# 用户权限管理（Phase 2 真实登录）— 设计规格

- **日期**：2026-08-06
- **分支**：`feat/user-permissions`
- **状态**：设计中（待 spec 审阅 + 用户复核 → 转 writing-plans）
- **作者**：Claude（与 fzl 协同）

---

## 1. 目标

为 CoreMasterKB 增加端到端的用户权限管理：真实登录、站点级角色（admin/member）、按角色分流的前端界面、以及一个 admin 可用的前端用户管理 UI。

一句话：**让"不同的人登录后看到不同的界面"成立，并把 KB 级权限模型（早已存在但一直吃写死的 `admin`）通电。**

## 2. 背景：这不是从零开始，是「Phase 2」

系统设计之初就分了两期。Phase 1（现状）埋好了所有接缝，代码注释明确标注了 Phase 2 的交接点：

| 已存在 | 位置 | 现状 |
|---|---|---|
| 用户表 `kb_users`（无密码列） | `databases/kb/schemas/001_kb_users.sql` | Phase 1 由 `X-KB-User` 头 upsert |
| **完整且已强制执行的 KB 级权限** | `knowledge_bases.owner_id`/`visibility`/`kb_members.role` + `kb/db.py` 的 `is_visible`/`can_write` | 读=owner∨public∨成员；写=owner∨editor。已在跑 |
| 身份注入接缝 `current_user()` | `knowledge_mining/mining/kb/auth.py`（23 行） | 读 `X-KB-User` 头；docstring 写明"Phase 2 只换身份来源，表与权限逻辑零改" |
| 前端写死默认用户 | `kb-ui/src/api/proxyClient.ts:11` `DEFAULT_KB_USER='admin'` | 注释写明"接真登录时把这里换成『从登录态 store 读』即可，其余零改" |

**净新增（本需求的核心）**：
1. 站点级角色 `site_role`（admin/member）—— 现在只有 KB 级角色，无站点级之分。
2. 真实登录（密码、JWT、登录页、auth store、路由守卫）—— 现在全无。
3. 登录后 UI（Header 账户菜单、Sidebar 按角色过滤）。
4. 鉴权强制点（main_control 现在对所有请求零鉴权直通后端）。

## 3. 已确认的决策

| 决策点 | 结论 |
|---|---|
| 身份来源 / 账号存储 | **数据库**：扩展 `kb_users` 表加 `password_hash` + `site_role` |
| 角色分档 | **两档** `admin` / `member`（KB 内部权限仍由 `kb_members.role` owner/editor/viewer 控制，两层叠加） |
| 范围 | **含前端用户管理 UI**（admin 在【设置】里增删用户、改角色、重置密码、禁用） |
| 鉴权架构 | **方案 A**：main_control 保持纯 YAML 网关，mining 持用户库 + 验密码，main_control 发令牌 + 强制 |

**我自己定的实现细节**（异议可改）：
- 令牌 = JWT-HS256 放 `Authorization` 头（不用 cookie：反代会剥 cookie/auth 头；main_control CORS 没开 `allow_credentials`）。
- 密码哈希用 stdlib `hashlib.pbkdf2_hmac`，JWT-HS256 手写（stdlib）—— **零新 pip 包**，守"不装多余包"约束。
- 首 admin 靠 `auth.yaml.bootstrap.admin_password` 在 mining 启动期播种（幂等）。

## 4. 非目标（Non-goals）

- **serving/检索侧不做用户隔离**：serving 是只读 pipeline 构建器，无 per-user 数据概念。未登录浏览器会被网关挡（符合预期），但 serving 内部不引入用户概念。
- **mcp_server 不接鉴权**：它直连 serving:8081 绕过网关（既有设计），鉴权管不到。内部工具集成，本 phase 不处理（见 §11 安全边界）。
- **per-domain 角色**：site_role 是全局两档，不做"同人在不同域权限不同"。留作未来扩展。
- **viewer 第三档**：不做只读访客档。member 即"普通用户"，能检索/用 KB。
- **SSO/OIDC**：不做。本地账号够用。

## 5. 数据模型

### 5.1 迁移 `databases/kb/schemas/006_kb_users_auth.sql`

```sql
ALTER TABLE kb_users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE kb_users ADD COLUMN IF NOT EXISTS site_role TEXT NOT NULL DEFAULT 'member'
                  CHECK (site_role IN ('admin','member'));
```

- `kb_users.status('active'|'disabled')` 列已存在，复用（`disabled` → 登录拒绝）。
- `password_hash = NULL` 表示不可登录（Phase 1 那批仅被 upsert 的行）。
- 现有行 `site_role` 落 `'member'`（DEFAULT）。Phase 1 已存在的 `admin` 用户名行由 §5.2 bootstrap 提权。
- 由 `pg_schema.py` 按序执行（kb schema 组）；`reset_db.py` 的 `SCHEMA_FILES` 顺序照旧（ontology 最后）。

### 5.2 首 admin 引导（破鸡生蛋）

mining 启动期（`knowledge_mining/mining/infra/control_plane.py` 已在拉控制面配置）多拉一份 `auth.yaml`，执行幂等播种逻辑：

> 若 `kb_users` 中**不存在**任何 `site_role='admin' AND password_hash IS NOT NULL` 的行，则把 `admin` 用户（不存在则建）提权为 `site_role='admin'`，`password_hash` = 对 `auth.yaml.bootstrap.admin_password` 做 PBKDF2 哈希。

仅在"无可用 admin"时生效，重复执行无副作用。新部署开机即有一个可登录 admin。

## 6. 密码哈希与令牌（零新包）

### 6.1 密码 —— PBKDF2-HMAC-SHA256（stdlib）

新模块 `knowledge_mining/mining/kb/security.py`：
- `hash_password(plain) -> str`：`secrets.token_bytes(16)` 盐，`hashlib.pbkdf2_hmac('sha256', plain, salt, 200_000)`，格式 `pbkdf2_sha256$200000$<salt_b64>$<hash_b64>`。
- `verify_password(plain, stored) -> bool`：拆格式 → 重算 → `hmac.compare_digest` 恒定时间比较。格式不符/算法不识别 → False。
- 200k 迭代（OWASP 2023 量级），可调。

### 6.2 令牌 —— JWT-HS256 手写（stdlib）

新模块 `main_control_service/jwt_util.py`（main_control 与 mining 都需要：main_control 签发/验证，mining 不需要验 token 但需要 internal secret，故 jwt_util 只在 main_control）：
- `encode(payload, secret, ttl) -> str`：`base64url({"alg":"HS256","typ":"JWT"}) . base64url({**payload, exp: now+ttl}) . base64url(HMAC_SHA256(secret, header.payload))`。
- `decode(token, secret) -> dict | None`：拆三段 → 验签名（`hmac.compare_digest`）→ 查 `exp`（`now > exp` → None）→ 返回 payload。**钉死 HS256**：header.alg ≠ "HS256" 一律拒（防 alg-confusion / "none"）。
- payload 字段：`{sub: username, role: site_role, name: display_name, exp}`。

## 7. 后端 · mining（持用户库）

### 7.1 新路由 `knowledge_mining/mining/kb/routes/auth.py`（挂进 `mining/api/app.py`）

| 方法 | 路径 | 鉴权 | 用途 |
|---|---|---|---|
| POST | `/api/kb/auth/verify` | **内部**：`X-Internal-Auth` 头 = `auth.yaml.internal_verify_secret`（缺失/不符 → 403） | 验 `{username, password}` → `{ok, user:{username, display_name, site_role}}` 或 401。供 main_control 内部调用，**堵住"绕网关直连 8901 暴力破解"** |
| GET | `/api/kb/users` | `require_admin` | 列用户（id/username/display_name/site_role/status/has_password） |
| POST | `/api/kb/users` | `require_admin` | 建用户 `{username, password, display_name, site_role}` |
| PATCH | `/api/kb/users/{id}` | `require_admin` | 改 display_name / site_role / status |
| POST | `/api/kb/users/{id}/reset-password` | `require_admin` | 设新密码 |
| POST | `/api/kb/users/me/password` | 任一登录用户 | 改自己密码（验旧密码） |

### 7.2 鉴权依赖

- `current_user(request)`（`kb/auth.py`，**行为不变**）：读 `X-KB-User` → upsert `kb_users` → 返回 user dict（现多读一列 `site_role`）。
- **新增** `require_admin = current_user → 查 `kb_users.site_role` → 非 admin 抛 403`。
  - 关键：mining **信任** `X-KB-User`（网关已验签注入的身份），但 **site_role 从自己库里现查**（纵深防御，不靠 `X-KB-Role` 头）。`X-KB-Role` 头仅信息用。

### 7.3 mining 启动期拉 `auth.yaml`

`infra/control_plane.py` 增 `fetch_auth_config()`（复用 `_get_raw("auth")`）。app 启动时调用一次 → 跑 §5.2 播种。`internal_verify_secret` 也从这里取，存模块级供 verify 端点校验。

## 8. 后端 · main_control（保持纯 YAML 网关）

### 8.1 新中间件 `main_control_service/auth.py`（照抄 `IpWhitelistMiddleware` 形状）

```
AuthMiddleware(BaseHTTPMiddleware):
  - 构造：从 config/system/auth.yaml 读 {enabled, jwt_secret, token_ttl_seconds, internal_verify_secret}
  - SKIP_PATHS = {/health, /api/v1/auth/login, /, /index.html, 静态资源}
  - 每请求：
      1. enabled=False 或 path in SKIP_PATHS → 放行
      2. 取 Authorization: Bearer <jwt>；缺失/验签失败/过期 → 401 {detail:"unauthenticated"}
      3. 注入请求头 X-KB-User = payload.sub，X-KB-Role = payload.role
      4. admin-only 路径白名单（见下）：X-KB-Role ≠ admin → 403
  - reload() + POST /api/v1/admin/reload-auth
```

**admin-only 路径白名单**（网关层纵深防御，member 即便伪造请求也拦）：
- `PUT /api/v1/system/{name}/raw`（改任意系统配置）
- `POST|PUT|DELETE /api/v1/domains*`（域 CRUD）
- `GET|PUT /api/v1/domains/{id}/scenario/raw`（场景包）
- `POST /api/v1/code-sync`（拉代码）
- `GET /api/v1/logs/{name}`（读日志）
- `/api/v1/admin/*`（除 `/api/v1/auth/login`、`/api/v1/auth/me`）

> 注意：用户管理端点（`/api/kb/users*`）在 mining 侧，经反代走 `/api/v1/proxy/{domain}/mining/api/kb/users`，由 mining 的 `require_admin` 兜底（不进 main_control admin 白名单）。

### 8.2 新端点（main_control）

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/auth/login` | body `{username, password}` → 内部 POST mining `/api/kb/auth/verify`（带 `X-Internal-Auth`）→ 成功签 JWT（§6.2）返回 `{token, user:{username, display_name, site_role}}`；失败 401 |
| GET | `/api/v1/auth/me` | 解 JWT claims 返回当前用户（无 DB 调用） |

### 8.3 注册

`main_control_service/main.py::create_app()`：
- `app.add_middleware(AuthMiddleware, config_path=auth_yaml_path)`（与 `IpWhitelistMiddleware` 并列；Starlette 反序注册，AuthMiddleware 注册顺序使其在 CORS 之内、IpWhitelist 之外即可）。
- 新增 `/api/v1/auth/login`、`/api/v1/auth/me`、`/api/v1/admin/reload-auth` 三个 `@app` 装饰器（沿用现有内联风格，无 APIRouter）。

### 8.4 `main_control_service/config/system/auth.yaml`

```yaml
enabled: true                      # 中间件总开关；测试/排查可关
jwt_secret: <强随机 32B hex>        # HS256 签名密钥（与库内 llm API key / DB 密码同性质，按部署轮换）
token_ttl_seconds: 43200           # 12h
internal_verify_secret: <强随机>    # main_control → mining verify 调用的内部头
bootstrap:
  admin_password: <初始密码>        # 仅首次播种首 admin 用，事后建议改/删
```

## 9. 请求流

```
登录:
  浏览器 → main_control /api/v1/auth/login {u,p}
    → main_control 内部 POST mining /api/kb/auth/verify (带 X-Internal-Auth)
    ← mining {ok, user}
    → main_control 签 JWT → {token, user} 回浏览器

之后每个请求:
  浏览器 Authorization: Bearer <jwt>
    → nginx /api/control-plane/* → main_control:8910
    → AuthMiddleware 验签 → 注入 X-KB-User + X-KB-Role
    → admin-only 白名单查 X-KB-Role（member 命中→403）
    → 反代剥 Authorization（现状）→ 转 mining/serving（带 X-KB-User/X-KB-Role）
    → mining current_user 读 X-KB-User（不变）；require_admin 现查 site_role
```

## 10. 前端（kb-ui）

### 10.1 新增

| 文件 | 作用 |
|---|---|
| `src/stores/auth.ts` | Pinia：`{user, token, siteRole, isAuthenticated}`；`login/logout/fetchMe/changeMyPassword`；token 持久化 `localStorage` |
| `src/api/auth.ts` | `login`/`getMe` 走 controlPlane 客户端（main_control 直连）；用户 CRUD + 改密走 `proxyClient('mining')` |
| `src/views/LoginView.vue` | 登录表单。**独立顶层路由，不在 AppLayout**（全屏） |
| `src/components/settings/UserManagementTab.vue` | 用户表 + 建用户/改角色/禁用/重置密码 弹窗（沿用 EP，`defineExpose` 暴露方法供测试） |

### 10.2 改动

- **`src/api/proxyClient.ts` + `src/api/controlPlane.ts`**（两 axios 客户端各装拦截器）：
  - 请求拦截：有 token 加 `Authorization: Bearer <token>`。
  - 响应拦截：**401 → 清 auth store → 跳 /login**（过期/失效统一兜底）。
  - **删掉** `proxyClient.ts` 写死 `X-KB-User: 'admin'` 的注入（约 36-42 行）—— Phase 2 交接点，X-KB-User 改由网关注入。
- **`src/router/index.ts`**：加 `/login`（`meta.public`）；改写 `beforeEach`：未登录且非 public → `/login?redirect=...`；已登录 → 跑现有"加载域列表"逻辑再放行；member 深链 admin 路由 → 挡回 `/`。
- **`src/components/layout/Sidebar.vue`**：navItem 加 `requiresAdmin` 标，按 `siteRole` 过滤。
- **`src/components/layout/Header.vue`**：右侧加账户菜单（用户名 + 角色色标 + 下拉：修改密码 / 登出）。
- **`src/views/SettingsView.vue`**：加 `<el-tab-pane label="用户管理" name="users"><UserManagementTab/></el-tab-pane>`。
- **`src/main.ts`**：启动期 `auth.restore()`（localStorage）+ 有 token 则 `fetchMe()`，与 `brand.fetchBrand()` 并联，mount 前完成 → 首屏按角色渲染不闪。

### 10.3 角色 → 导航映射（定死）

| 导航项 | 路由 | admin | member |
|---|---|---|---|
| 概览 | `/` | ✅ | ✅ |
| 知识库 | `/kb` | ✅ | ✅ |
| 检索测试 | `/search` | ✅ | ✅ |
| 挖掘范式 | `/mining/workflows` | ✅ | ❌ |
| 检索范式 | `/paradigm` | ✅ | ❌ |
| 实体图谱 | `/entities` | ✅ | ❌ |
| 本体版本 | `/ontology` | ✅ | ❌ |
| 本体图谱 | `/ontology/graph` | ✅ | ❌ |
| LLM 服务 | `/llm` | ✅ | ❌ |
| 系统设置 | `/settings` | ✅ | ❌ |

> 无侧边栏入口的 `/knowledge`、`/graph` 等深链路由：member 命中由路由守卫挡回 `/`。

## 11. 错误处理

| 场景 | 行为 |
|---|---|
| 任一接口 401（token 过期/无效） | axios 响应拦截 → 清 token → 跳 /login |
| 登录失败 | LoginView 提示"用户名或密码错误" |
| member 触达 admin 路由 | 前端守卫挡回 /；后端 admin 白名单 / `require_admin` 也 403（双层） |
| 网络错误 | 复用现有 `apiErrorDetail` |
| mining verify 直连且无内部头 | 403 |
| 验证码级输入校验 | LoginView/UserManagementTab 用 el-form rules（非空、密码长度 ≥ 8） |

## 12. 安全考量与边界

1. **后端端口发布到宿主机**（`docker-compose.yml` 把 8900/8901/8081/8910/9000 都 publish）：网关鉴权只护浏览器路。直连这些端口仍绕过。
   - 缓解：verify 端点强制 `X-Internal-Auth`（暴力破解口子堵掉）；其余端点的直连风险是部署决策（内网 + IP 白名单可接受；严格隔离需把端口改容器内监听）—— **本 phase 不改 docker-compose，文档标注**。
2. **mcp_server 直连 serving:8081**：绕过网关，鉴权管不到。内部工具集成，本 phase 不处理。
3. **JWT secret / DB 密码 / 内部 secret 存 yaml**：与库内现有做法一致（`llm_service.yaml` 放 LLM API key、`database.yaml` 放 DB 密码）。`auth.yaml` 应按部署轮换；`bootstrap.admin_password` 首次播种后建议改/删。
4. **mining 信任网关注入的 X-KB-User 但现查 site_role**：身份信任网关，授权自查库 —— 即便 X-KB-Role 头被伪造，授权仍以库为准。
5. **令牌存 localStorage**：标准 XSS 风险。内部工具可接受；token 12h 过期 + 401 自动登出兜底。
6. **首包播种的 admin 默认密码**：来自 `auth.yaml`，部署方应在首登后立即改密。

## 13. 测试

### 13.1 Python · mining（连 kb 库 → 仅 `kb_db_test` + `KB_ALLOW_TEST_TRUNCATE=1`）

`knowledge_mining/tests/kb/test_auth.py`：
- `security.hash_password` / `verify_password` 往返（对/错密码、格式损坏、不同盐）
- verify 端点：好密码、坏密码、disabled 用户、未设密码（password_hash NULL）、错/缺 `X-Internal-Auth` → 403
- `require_admin`：admin 通过、member → 403
- 用户 CRUD：建（含哈希落库）、列、改 site_role、禁用、重置密码
- `/api/kb/users/me/password`：旧密码错 → 拒，对 → 改成功
- bootstrap 幂等：无 admin 时播种、已有 admin 时不重复改

### 13.2 Python · main_control（TestClient，无 DB，monkeypatch mining verify）

`main_control_service/tests/test_auth_middleware.py`：
- `jwt_util.encode/decode` 往返、坏签名 → None、过期 → None、alg 篡改 → None
- 中间件：合法 token 放行且注入 `X-KB-User`/`X-KB-Role`；缺失/坏/过期 → 401；SKIP_PATHS（`/health`、`/login`）免 token 放行
- admin-only 白名单：admin 放行、member → 403
- `/api/v1/auth/login`：verify 成功 → 返回 token；verify 失败 → 401（monkeypatch 掉内部 mining 调用）
- `/api/v1/auth/me`：合法 token → 返回 claims
- **回归**：`AuthMiddleware.enabled=False` 时，现有 `test_system_config.py` 等套件全绿（提供测试 helper 签发 token 或直接关 enabled）

### 13.3 Vitest（kb-ui）

- `auth.spec.ts`：login 置 token+user、logout 清、fetchMe 填充、localStorage 持久化
- `LoginView.spec.ts`：提交调 login、成功跳转、失败提示
- `Sidebar.spec.ts` 扩展：admin 见全部、member 见 3 项
- 路由守卫：无 token → /login；member 深链 admin 路由 → /
- `UserManagementTab.spec.ts`：列表渲染、建/重置调 API（`defineExpose` 规避 EP stub）
- axios 拦截：401 → logout + 跳 /login

## 14. 上线 / 部署

1. 迁移 `006_kb_users_auth.sql`（`pg_schema.py` 自动；或 `reset_db.py`）。
2. main_control：加 `config/system/auth.yaml`（volume 挂载 → 重启生效）。
3. mining：重启（新端点 + bootstrap 播种 admin；从控制面拉 `auth.yaml`）。
4. 前端：**重建 kb-ui 镜像**（登录/拦截器/侧边栏过滤都是代码改动）。
5. 用 `admin` + bootstrap 密码首登 → 改密 → 建用户。

## 15. 文件清单

**新增**
- `databases/kb/schemas/006_kb_users_auth.sql`
- `knowledge_mining/mining/kb/security.py`（PBKDF2）
- `knowledge_mining/mining/kb/routes/auth.py`（verify + 用户 CRUD）
- `knowledge_mining/tests/kb/test_auth.py`
- `main_control_service/auth.py`（AuthMiddleware）
- `main_control_service/jwt_util.py`（HS256）
- `main_control_service/tests/test_auth_middleware.py`
- `main_control_service/config/system/auth.yaml`
- `kb-ui/src/stores/auth.ts`
- `kb-ui/src/api/auth.ts`
- `kb-ui/src/views/LoginView.vue`
- `kb-ui/src/components/settings/UserManagementTab.vue`
- `kb-ui/src/**tests**/{auth.spec.ts, LoginView.spec.ts, UserManagementTab.spec.ts}`

**改动**
- `knowledge_mining/mining/kb/auth.py`（`current_user` 多读 site_role；新增 `require_admin`）
- `knowledge_mining/mining/kb/db.py`（user CRUD + site_role 读写）
- `knowledge_mining/mining/infra/control_plane.py`（拉 auth.yaml）
- `knowledge_mining/mining/api/app.py`（挂 auth 路由 + 启动期播种）
- `main_control_service/main.py`（注册中间件 + 3 个端点）
- `main_control_service/proxy.py`（无需改 — 已剥 authorization，转发 X-KB-*）
- `kb-ui/src/api/{proxyClient.ts, controlPlane.ts}`（拦截器 + 删 X-KB-User 写死）
- `kb-ui/src/router/index.ts`（/login + 守卫）
- `kb-ui/src/components/layout/{Sidebar.vue, Header.vue}`
- `kb-ui/src/views/SettingsView.vue`（用户管理 tab）
- `kb-ui/src/main.ts`（启动期 fetchMe）
- `kb-ui/src/components/layout/__tests__/Sidebar.spec.ts`（角色过滤）

## 16. 未来扩展（不在本 phase）

- per-domain 角色（user × domain role 表）。
- viewer 第三档（只读访客）。
- SSO / OIDC（接公司 IdP）。
- mcp_server 鉴权。
- 把后端端口改为容器内监听（堵直连绕过）。
- 审计日志（谁在何时做了什么管理操作）。
