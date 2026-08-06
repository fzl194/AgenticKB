# 用户权限管理（Phase 2 真实登录）— 设计规格

- **日期**：2026-08-06
- **分支**：`feat/user-permissions`
- **状态**：设计中（spec 审阅第 2 轮 → 待用户复核 → 转 writing-plans）
- **作者**：Claude（与 fzl 协同）

> **v2 修订**：根据 spec 审阅第 1 轮反馈修正。主要变更：(1) **堵住 mining:8901 直连伪造 `X-KB-User` 的账号接管洞** —— 网关注入 `X-Internal-Auth`，mining `current_user` 强制校验；(2) 明确 `upsert_user_by_username` 的 RETURNING 与不变量；(3) 钉死中间件注册顺序；(4) 补 bootstrap 幂等不变量、deploy `--force` 陷阱等。

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
| 身份注入接缝 `current_user()` | `knowledge_mining/mining/kb/auth.py` | 读 `X-KB-User` 头 → upsert `kb_users`；docstring 写明"Phase 2 只换身份来源，表与权限逻辑零改" |
| 前端写死默认用户 | `kb-ui/src/api/proxyClient.ts:11` `DEFAULT_KB_USER='admin'` | 注释写明"接真登录时把这里换成『从登录态 store 读』即可" |

**净新增（本需求的核心）**：
1. 站点级角色 `site_role`（admin/member）—— 现在只有 KB 级角色，无站点级之分。
2. 真实登录（密码、JWT、登录页、auth store、路由守卫）—— 现在全无。
3. 登录后 UI（Header 账户菜单、Sidebar 按角色过滤）。
4. 鉴权强制点（main_control 现在对所有请求零鉴权直通后端）。

> **关于"零改"的修正**：Phase 1 注释说"表与权限逻辑零改"——指 `kb_members`/`is_visible`/`can_write` 这套 KB 级授权逻辑不动。但 `current_user()` 本身**会改**（见 §7.2：加 `X-Internal-Auth` 校验堵伪造）。这是必要的安全强化，不属于"权限逻辑"。

## 3. 已确认的决策

| 决策点 | 结论 |
|---|---|
| 身份来源 / 账号存储 | **数据库**：扩展 `kb_users` 表加 `password_hash` + `site_role` |
| 角色分档 | **两档** `admin` / `member`（KB 内部权限仍由 `kb_members.role` owner/editor/viewer 控制，两层叠加） |
| 范围 | **含前端用户管理 UI**（admin 在【设置】里增删用户、改角色、重置密码、禁用） |
| 鉴权架构 | **方案 A**：main_control 保持纯 YAML 网关，mining 持用户库 + 验密码，main_control 发令牌 + 强制 |

**我自己定的实现细节**（异议可改）：
- 令牌 = JWT-HS256 放 `Authorization` 头（不用 cookie：反代会剥 cookie/auth 头；main_control CORS 没开 `allow_credentials`）。
- 密码哈希用 stdlib `hashlib.pbkdf2_hmac`，JWT-HS256 手写（stdlib）—— **零新 pip 包**，守"不装多余包"约束。明确拒绝 `PyJWT`/`python-jose`（YAGNI，HS256 用 stdlib 30 行搞定，钉死 alg 规避库的风险面）。
- 首 admin 靠 `auth.yaml.bootstrap.admin_password` 在 mining 启动期播种（幂等）。
- **网关即信任边界**：网关在每个已鉴权转发请求里注入 `X-Internal-Auth` 头（secret 来自 auth.yaml）；mining 的 `current_user` 同时要求 `X-KB-User` + `X-Internal-Auth`，否则 401 —— 堵死"直连 8901 伪造 X-KB-User"（见 §7.2、§12）。

## 4. 非目标（Non-goals）

- **serving/检索侧不做用户隔离**：serving 是只读 pipeline 构建器，无 per-user 数据概念。未登录浏览器会被网关挡（符合预期），但 serving 内部不引入用户概念。
- **mcp_server 不接鉴权**：它直连 serving:8081 绕过网关（既有设计），鉴权管不到。内部工具集成，本 phase 不处理（见 §12）。
- **per-domain 角色**：site_role 是全局两档，不做"同人在不同域权限不同"。留作未来扩展。
- **viewer 第三档**：不做只读访客档。member 即"普通用户"，能检索/用 KB。
- **SSO/OIDC**：不做。本地账号够用。

## 5. 数据模型

### 5.1 迁移 `databases/kb/schemas/006_kb_users_auth.sql`（编号已确认空闲：现有 001–005）

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

mining 启动期（`knowledge_mining/mining/infra/control_plane.py` 已在拉控制面配置）多拉一份 `auth.yaml`，执行幂等播种：

> 若 `kb_users` 中**不存在**任何 `site_role='admin' AND password_hash IS NOT NULL` 的行（含"表完全空"和"有 member 无 admin"两种情况），则把 `admin` 用户（不存在则建）提权为 `site_role='admin'`，`password_hash` = 对 `auth.yaml.bootstrap.admin_password` 做 PBKDF2 哈希。

仅在"无可用 admin"时生效，重复执行无副作用。新部署开机即有一个可登录 admin。

### 5.3 `upsert_user_by_username` 不变量（关键）

迁移加了 `site_role` 后，`kb/db.py::upsert_user_by_username` 必须同步改，且守住两条不变量：

1. **RETURNING 扩列**：返回值从 `id, username, display_name, status` 增至含 `site_role`（`current_user` 直接消费 upsert 返回值，见 §7.2）。
2. **冲突时不得覆盖 `site_role` / `password_hash`**：现有 `ON CONFLICT (username) DO UPDATE` 只动 `display_name`（保持不变）。Phase 2 后，浏览器 KB 流量仍会经网关带 `X-KB-User` 命中此 upsert —— **绝不能因为某个 admin 用户的日常 KB 请求就把他降级或清空密码**。显式钉死：`DO UPDATE SET display_name = COALESCE(...)`，`site_role` 与 `password_hash` 不出现在 SET 里。

## 6. 密码哈希与令牌（零新包）

### 6.1 密码 —— PBKDF2-HMAC-SHA256（stdlib）

新模块 `knowledge_mining/mining/kb/security.py`：
- `hash_password(plain) -> str`：`secrets.token_bytes(16)` 盐，`hashlib.pbkdf2_hmac('sha256', plain, salt, 200_000)`，格式 `pbkdf2_sha256$200000$<salt_b64>$<hash_b64>`。
- `verify_password(plain, stored) -> bool`：拆格式 → 重算 → `hmac.compare_digest` 恒定时间比较。格式不符/算法不识别 → False。
- 200k 迭代（OWASP 2023 量级），可调常量。

### 6.2 令牌 —— JWT-HS256 手写（stdlib）

新模块 `main_control_service/jwt_util.py`（仅 main_control 需要：签发 login + 中间件验证）：
- `encode(payload, secret, ttl) -> str`：`base64url({"alg":"HS256","typ":"JWT"}) . base64url({**payload, iat: now, exp: now+ttl}) . base64url(HMAC_SHA256(secret, header.payload))`。
- `decode(token, secret) -> dict | None`：拆三段 → 验签名（`hmac.compare_digest`）→ 查 `exp`（`now > exp` → None）→ 返回 payload。**钉死 HS256**：header.alg ≠ "HS256" 一律拒（防 alg-confusion / "none"）。
- payload 字段：`{sub: username, role: site_role, name: display_name, iat, exp}`（`name` 即 display_name，供前端/Header 显示）。
- 拒绝 `PyJWT`/`python-jose`：HS256 stdlib 足够，少一个依赖少一份风险面。

## 7. 后端 · mining（持用户库）

### 7.1 新路由 `knowledge_mining/mining/kb/routes/auth.py`（挂进 `mining/api/app.py`）

| 方法 | 路径 | 鉴权 | 用途 |
|---|---|---|---|
| POST | `/api/kb/auth/verify` | **内部**：`X-Internal-Auth` 头 = `auth.yaml.internal_verify_secret`（缺失/不符 → **401**，与 `current_user` 一致：未证明身份） | 验 `{username, password}` → `{ok, user:{username, display_name, site_role}}` 或 401。供 main_control 内部调用 |
| GET | `/api/kb/users` | `require_admin` | 列用户（id/username/display_name/site_role/status/has_password） |
| POST | `/api/kb/users` | `require_admin` | 建用户 `{username, password, display_name, site_role}` |
| PATCH | `/api/kb/users/{id}` | `require_admin` | 改 display_name / site_role / status |
| POST | `/api/kb/users/{id}/reset-password` | `require_admin` | 设新密码 |
| POST | `/api/kb/users/me/password` | 任一登录用户 | 改自己密码（验旧密码） |

> 所有 `/api/kb/users*` 与既有 `/api/kb/*` 路由一样经 `Depends(current_user)`；而 `current_user` 现在强制 `X-Internal-Auth`（§7.2）—— 故这些 admin 端点天然只能从网关到达，**直连 8901 伪造 `X-KB-User: admin` 不再能接管用户管理**。

### 7.2 鉴权依赖（含安全强化）

- **`current_user(request)`（`kb/auth.py`，行为变更）**：
  1. 读 `X-KB-User` 头；缺失 → 401。
  2. **新增**：读 `X-Internal-Auth` 头，比对 `auth.yaml.internal_verify_secret`（mining 启动期从控制面拉取，存模块级）；缺失/不符 → **401**。
  3. `upsert_user_by_username`（含 §5.3 的 RETURNING + 不变量）→ 返回 user dict（含 `site_role`）。
  - **为什么**：mining:8901 被 publish 到宿主机（`docker-compose.yml`）。若只信 `X-KB-User`，任何人知道某 admin 用户名就能直连 8901 伪造该头。要求 `X-Internal-Auth`（只有网关知道 secret）= 证明请求确实经过了已鉴权网关。这堵死的不止 admin 用户管理，连"伪造他人身份读写 KB"一起堵。
- **新增** `require_admin = current_user → 查 `kb_users.site_role` → 非 admin 抛 403`。
  - 关键：mining **信任** `X-KB-User`（网关已验签注入的身份），但 **site_role 从自己库里现查**（纵深防御，不靠 `X-KB-Role` 头）。`X-KB-Role` 头仅信息用。
- **dev 直连 mining 的注意**：本地直接打 mining:8901 调试时，须在请求里带 `X-Internal-Auth`（值取自 `main_control_service/config/system/auth.yaml`）。测试 fixture 见 §13.1。

### 7.3 mining 启动期拉 `auth.yaml`

`infra/control_plane.py` 增 `fetch_auth_config()`（复用 `_get_raw("auth")`）。app 启动时调用一次 → 跑 §5.2 播种。`internal_verify_secret` 也从这里取，存模块级供 `current_user` / verify 端点校验。

**启动期 best-effort，不 fail-fast**：若控制面不可达，记 warning 并继续（与现有 `MiningDbConfig` 回落策略一致）—— 此时 `internal_verify_secret` 缺位，`current_user` 一律 401，mining 暂不可用直到下次 reload 拉到。提供运行期重新拉取路径（admin `reload-config` 触发，或定时重试）。

## 8. 后端 · main_control（保持纯 YAML 网关）

### 8.1 新中间件 `main_control_service/auth.py`（照抄 `IpWhitelistMiddleware` 形状）

```
AuthMiddleware(BaseHTTPMiddleware):
  - 构造：从 config/system/auth.yaml 读 {enabled, jwt_secret, token_ttl_seconds, internal_verify_secret}
  - SKIP_PATHS = {/health, /api/v1/auth/login}（静态资源由 nginx 直出，不进本服务）
  - 每请求：
      1. enabled=False 或 path in SKIP_PATHS 或 method == OPTIONS → 放行（OPTIONS 交由外层 CORS 处理 preflight）
      2. 取 Authorization: Bearer <jwt>；缺失/验签失败/过期 → 401 {detail:"unauthenticated"}
      3. 把身份挂到 request.state.user = {username: sub, role}（供下游反代注入头用，不直接改 scope headers）
      4. admin-only 路径白名单（见下）：request.state.user.role ≠ admin → 403
  - reload() + POST /api/v1/admin/reload-auth
```

**admin-only 路径白名单**（网关层纵深防御，member 即便伪造请求也拦）：
- `PUT /api/v1/system/{name}/raw`（改任意系统配置）
- `POST|PUT|DELETE /api/v1/domains*`（域 CRUD）
- `GET|PUT /api/v1/domains/{id}/scenario/raw`（场景包）
- `POST /api/v1/code-sync`（拉代码）
- `GET /api/v1/logs/{name}`（读日志）

> 说明：`/api/v1/auth/login` 在 SKIP_PATHS（登录不能要求已登录）。`/api/v1/auth/me` 走正常鉴权（合法 token 即可，不限 admin）。`/api/v1/admin/*`（含新 `reload-auth`）天然 admin-only —— 与 `reload-ip-whitelist` 被 IP 白名单 skip 的不对称是有意的：auth 功能正常时 admin token 可达 reload-auth；若 auth 彻底锁死，恢复路径是改 `auth.yaml` 设 `enabled: false` 再重启 main_control（volume 挂载，无需鉴权）。
> 用户管理端点（`/api/kb/users*`）在 mining 侧，经反代走 `/api/v1/proxy/{domain}/mining/api/kb/users`，由 mining 的 `require_admin` + `current_user`(含 X-Internal-Auth) 兜底，不进 main_control admin 白名单。

### 8.2 新端点（main_control）

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/auth/login` | body `{username, password}` → 内部 POST mining `/api/kb/auth/verify`（带 `X-Internal-Auth`）→ 成功签 JWT（§6.2）返回 `{token, user:{username, display_name, site_role}}`；失败 401 |
| GET | `/api/v1/auth/me` | 解 JWT claims 返回当前用户（无 DB 调用） |

### 8.3 注册（钉死顺序）

`main_control_service/main.py::create_app()` 中间件按 **Starlette 反序执行**（后注册 = 外层）。目标执行序（外→内）：**IpWhitelist → CORS → Auth → app**。故注册顺序（代码里自上而下）：

```python
app.add_middleware(AuthMiddleware, config_path=auth_yaml_path)   # 最先注册 → 最内（CORS 之内）
app.add_middleware(CORSMiddleware, allow_origins=[...], ...)     # 中
app.add_middleware(IpWhitelistMiddleware, config_path=...)       # 最后注册 → 最外
```

> 现状是 `IpWhitelist` 在 `CORS` 之后注册（IpWhitelist 最外）。本变更把 Auth 插到 CORS 之内，保证 **CORS 在 Auth 之前执行**——否则浏览器 preflight OPTIONS 会被 Auth 当成无 token 请求 401，登录流必崩。Auth 的 SKIP_PATHS 也显式放行 OPTIONS 双保险。

端点新增 `/api/v1/auth/login`、`/api/v1/auth/me`、`/api/v1/admin/reload-auth`（沿用现有 `@app` 内联装饰器，无 APIRouter）。

### 8.4 反代头注入（`proxy.py` 改）

`proxy._build_forward_headers`（`proxy.py`）：转发前，若 `request.state.user` 存在（即 AuthMiddleware 已鉴权），则在下发请求里注入：
- `X-KB-User = request.state.user.username`
- `X-KB-Role = request.state.user.role`（信息用）
- `X-Internal-Auth = internal_verify_secret`（证明经网关；mining `current_user` 校验）

这三个头都不在现有 `_STRIP_REQUEST_HEADERS` 里（该集合剥 `authorization`/`cookie` 等，保留 X-KB-*）。浏览器原本自带的 `Authorization` 仍被剥掉（现状），只把网关派生的三个内部头传给下游。

### 8.5 `main_control_service/config/system/auth.yaml`

```yaml
enabled: true                      # 中间件总开关；测试/排查/锁死恢复可关
jwt_secret: <强随机 32B hex>        # HS256 签名密钥（与库内 llm API key / DB 密码同性质，按部署轮换）
token_ttl_seconds: 43200           # 12h
internal_verify_secret: <强随机>    # 网关→mining 的信任凭证（注入 X-Internal-Auth）
bootstrap:
  admin_password: <初始密码>        # 仅首次播种首 admin 用，事后建议改/删
```

## 9. 请求流

```
登录:
  浏览器 → main_control /api/v1/auth/login {u,p}    [SKIP_PATHS，免 token]
    → main_control 内部 POST mining /api/kb/auth/verify (带 X-Internal-Auth)
    ← mining {ok, user}
    → main_control 签 JWT → {token, user} 回浏览器

之后每个请求:
  浏览器 Authorization: Bearer <jwt>
    → nginx /api/control-plane/* → main_control:8910
    → (IpWhitelist) → (CORS 处理 preflight) → AuthMiddleware 验签
       → 身份挂 request.state.user；admin-only 白名单查 role（member 命中→403）
    → 反代剥 Authorization（现状）；_build_forward_headers 注入 X-KB-User / X-KB-Role / X-Internal-Auth
    → mining current_user 校验 X-Internal-Auth + 读 X-KB-User（upsert，含 site_role）
       → require_admin 现查 site_role
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
  - **删掉** `proxyClient.ts` 里写死的 `X-KB-User` 注入 —— 即 `const DEFAULT_KB_USER = ...`（line 11）和请求拦截里 `config.headers.set('X-KB-User', DEFAULT_KB_USER)`（line 40 附近，含其上的 `if (...startsWith('/api/kb'))` 守卫）。**保留**同拦截器里的 `domain` query 参数逻辑（lines 33–37）—— 那是域路由职责，与鉴权无关。X-KB-User 改由网关注入。
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
> member 的检索结果**不做 per-user 隔离**（serving 无用户概念，见 §4）—— member 与 admin 检索范围一致，仅界面不同。

## 11. 错误处理

| 场景 | 行为 |
|---|---|
| 任一接口 401（token 过期/无效） | axios 响应拦截 → 清 token → 跳 /login |
| mining 收到缺 `X-Internal-Auth` 的 `/api/kb/*` 请求 | `current_user` → 401（直连伪造被拒） |
| 登录失败 | LoginView 提示"用户名或密码错误" |
| member 触达 admin 路由 | 前端守卫挡回 /；后端 admin 白名单 / `require_admin` 也 403（双层） |
| 网络错误 | 复用现有 `apiErrorDetail` |
| mining verify 直连且无/错内部头 | 401（与 current_user 统一） |
| 表单输入校验 | LoginView/UserManagementTab 用 el-form rules（非空、密码长度 ≥ 8） |

## 12. 安全考量与边界

1. **`X-KB-User` 伪造（已堵）**：mining:8901 publish 到宿主机。Phase 1 只信 `X-KB-User`，直连即可伪造身份。**Phase 2 由 `current_user` 强制 `X-Internal-Auth` 堵死**——只有已鉴权网关能产出该头，伪造者无 secret。此修复覆盖全部 `/api/kb/*`（含 admin 用户管理、KB 读写），不止 admin 端点。
2. **后端端口发布到宿主机**（`docker-compose.yml` 把 8900/8901/8081/8910/9000 都 publish）：浏览器路被网关鉴权 + mining 被内部头保护后，**残留风险**是直连 serving:8081 / llm:8900（这两个不校验 X-KB-User，但仅只读检索 / LLM 调用，且无用户管理类操作）与直连 mining 的非 `/api/kb/*` 端点。属部署决策（内网 + IP 白名单可接受；严格隔离需把端口改容器内监听）—— **本 phase 不改 docker-compose，文档标注**。
3. **mcp_server 直连 serving:8081**：绕过网关，鉴权管不到。内部工具集成，本 phase 不处理。
4. **secret 存 yaml**：`jwt_secret` / `internal_verify_secret` / `bootstrap.admin_password` 放 `auth.yaml`，与库内现有做法一致（`llm_service.yaml` 放 LLM API key、`database.yaml` 放 DB 密码）。按部署轮换；`bootstrap.admin_password` 首次播种后建议改/删。
5. **mining 信任网关注入的 X-KB-User 但现查 site_role**：身份信任网关（X-Internal-Auth 证明），授权自查库 —— 即便 X-KB-Role 头被伪造，授权仍以库为准。
6. **令牌存 localStorage**：标准 XSS 风险。内部工具可接受；token 12h 过期 + 401 自动登出兜底。
7. **首 admin 默认密码**：来自 `auth.yaml`，部署方应首登后立即改密。

## 13. 测试

### 13.1 Python · mining（连 kb 库 → 仅 `kb_db_test` + `KB_ALLOW_TEST_TRUNCATE=1`）

`knowledge_mining/tests/kb/test_auth.py`：
- `security.hash_password` / `verify_password` 往返（对/错密码、格式损坏、不同盐）
- verify 端点：好密码、坏密码、disabled 用户、未设密码（password_hash NULL）、错/缺 `X-Internal-Auth` → 403
- `current_user`：缺 `X-KB-User` → 401；有 `X-KB-User` 缺 `X-Internal-Auth` → 401（**伪造堵死的核心断言**）；两者齐全 → upsert 成功
- `require_admin`：admin 通过、member → 403
- 用户 CRUD：建（含哈希落库）、列、改 site_role、禁用、重置密码
- `/api/kb/users/me/password`：旧密码错 → 拒，对 → 改成功
- bootstrap 幂等 + **不变量**：无 admin 时播种；**已有 admin 时二次启动不改其 `password_hash`/`site_role`**（显式断言两字段不变 —— 防重蹈"每次操作扣两次额度"类静默回归）
- `upsert_user_by_username` 冲突不变量：对已存在 admin 用户名重复 upsert，`site_role`/`password_hash` 不变
- **conftest 调整**：所有打 mining `/api/kb/*` 的测试请求须带 `X-Internal-Auth` 头（取测试用 secret）；既有 `test_mining.py`/`test_kb_db.py` 等套件的请求 fixture 同步补该头，保持绿。**做法**：在 `conftest.py` 暴露一个共享 `auth_headers` fixture（返回 `{'X-KB-User': <测试用户>, 'X-Internal-Auth': <测试 secret>}`），各测试模块统一引用，最小化每文件改动。

### 13.2 Python · main_control（TestClient，无 DB，monkeypatch mining verify）

`main_control_service/tests/test_auth_middleware.py`：
- `jwt_util.encode/decode` 往返、坏签名 → None、过期 → None、alg 篡改（"none"/"RS256"）→ None、`iat`/`exp` 存在
- 中间件：合法 token 放行且 `request.state.user` 被设；缺失/坏/过期 → 401；SKIP_PATHS（`/health`、`/login`）免 token 放行；OPTIONS 放行（不被 auth 拦）
- 反代头注入：合法 token 的请求经反代后，下游收到 `X-KB-User`/`X-KB-Role`/`X-Internal-Auth`（用 monkeypatch 捕获转发请求头断言）
- admin-only 白名单：admin 放行、member → 403
- `/api/v1/auth/login`：verify 成功 → 返回 token；verify 失败 → 401（monkeypatch 掉内部 mining 调用）
- `/api/v1/auth/me`：合法 token → 返回 claims
- **回归**：`AuthMiddleware.enabled=False` 时，现有 `test_system_config.py` 等套件全绿（提供测试 helper 签发 token 或直接关 enabled）
- **中间件顺序**：一条断言确认 CORS 在 Auth 之外（OPTIONS preflight 不被 auth 401）

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

> ⚠️ **`deploy-server.sh --force` 陷阱**（CLAUDE.md 已记录）：`--force` 会 `rm -rf main_control_service/`，连带删掉 `config/system/auth.yaml`。部署后须**重新放置 `auth.yaml`** 再重启 mining，否则 bootstrap 无密码可读、首 admin 起不来。`--force-config` 只管 `.env`，不管这里。运维 runbook 须列此步。

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
- `knowledge_mining/mining/kb/auth.py`（`current_user` 加 `X-Internal-Auth` 校验；新增 `require_admin`）
- `knowledge_mining/mining/kb/db.py`（`upsert_user_by_username` RETURNING + 不变量；user CRUD + site_role 读写）
- `knowledge_mining/mining/infra/control_plane.py`（拉 auth.yaml）
- `knowledge_mining/mining/api/app.py`（挂 auth 路由 + 启动期播种）
- `knowledge_mining/tests/conftest.py`（请求 fixture 补 `X-Internal-Auth`）
- `main_control_service/main.py`（注册中间件 + 3 个端点；调整中间件注册顺序）
- `main_control_service/proxy.py`（`_build_forward_headers` 注入 X-KB-User/X-KB-Role/X-Internal-Auth from request.state）
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
- 把后端端口改为容器内监听（堵 serving/llm 直连绕过）。
- 审计日志（谁在何时做了什么管理操作）。
