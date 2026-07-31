# 知识库管理 —— 前端实现计划（kb-ui）

> **状态**：草案 / 待 review
> **版本**：v0.1（2026-07-28）
> **分支**：`feat/kb-management`
> **配套**：后端 KB 管理（mining 侧 P1–P6）已实现，28/28 测试通过。本文是前端配套计划，让你能在 UI 上端到端测试 KB 管理。
> **范围**：仅 KB 管理（建库 / 文件管理 / 触发挖掘 / 成员 / 权限）。检索相关 UI 不在本计划（serving 侧 P5 未做）。

---

## 0. 已确认的集成前提

- **`X-KB-User` 透传**：`main_control_service/proxy.py:106-117` 的 `_build_forward_headers` 只剥 hop-by-hop + cookie/authorization，**不剥 `X-KB-User`** → 前端发的用户头能直达 mining。无需改代理。
- **API 入口**：前端统一走 `createProxyClient('mining')`（`kb-ui/src/api/proxyClient.ts`），baseURL `/api/control-plane/api/v1/proxy/{domain}/mining`，自动注入 domain 参数。KB 端点都挂在 mining 服务下。
- **UI 栈**：Vue 3.5 + Element Plus 2.14 + Pinia + vue-router。组件按需自动导入（unplugin）。

---

## 1. 当前用户（Phase 1 auth）

**决策：写死默认用户**（无切换 UI）。

- `kb-ui/src/api/proxyClient.ts` 加一个请求拦截器：对 mining 请求注入 header `X-KB-User: <DEFAULT_KB_USER>`。
- `DEFAULT_KB_USER` 定义为一个常量（建议放 `kb-ui/src/api/proxyClient.ts` 顶部或 `kb-ui/.env` 的 `VITE_KB_DEFAULT_USER`），默认值 `"admin"`，便于改。
- 后续接真登录时，把这里的常量换成「从登录态 store 读」即可，前端其它代码零改。

```ts
// proxyClient.ts 顶部
const DEFAULT_KB_USER = import.meta.env.VITE_KB_DEFAULT_USER || 'admin'
// 在 createProxyClient 的 interceptor 里：
config.headers = { ...config.headers, 'X-KB-User': DEFAULT_KB_USER }
```

---

## 2. 页面设计

### Page 1：知识库列表 `/kb`（`views/kb/KbListView.vue`）

```
┌──────────────────────────────────────────────────────┐
│ 知识库                            [+ 新建知识库]       │  ← 域默认当前域（domainStore）
├──────────────────────────────────────────────────────┤
│ 名称        域          可见性   我的角色  文档数  操作 │
│ 5G规范库    cloud_core  共享     editor    12     进入 │
│ AMF资料     cloud_core  私有     owner     3      进入 │
│ ...                                                  │
└──────────────────────────────────────────────────────┘
```
- 列数据：`GET /api/kb?domain=<当前域>` → list_visible（后端已按可见性过滤）。
- 「我的角色」前端推算：owner_id == 当前用户 → owner；否则查 members（或后端 list 时附带 role——**待定，见 §6 类型缺口**）。
- 新建对话框 `KbCreateDialog.vue`：名称 / 可见性（select private|shared|public）/ 描述 → `POST /api/kb`。
- 空态用现有 `components/common/EmptyState.vue`。

### Page 2：知识库详情 `/kb/:kbId`（`views/kb/KbDetailView.vue`）

```
┌──────────────────────────────────────────────────────┐
│ ← 返回   5G规范库  [共享]            [挖掘] [⋯ 更多]   │
├──────────────────────────────────────────────────────┤
│ [文件]  成员  设置                                    │
│──────────────────────────────────────────────────────│
│ 拖拽文件到此处上传，或 [点击选择]（支持 .zip 自动解压）│
│ 目录筛选: [全部 ▾]                                   │
│ 文件名        目录        类型    状态     上传时间  ⋯ │
│ qos.pdf       5G/AMF      参考    已发布   07-28    下载│
│ spec.zip → 解压出 3 个文件                            │
└──────────────────────────────────────────────────────┘
```
- 顶部：「挖掘」按钮 → `POST /api/kb/{id}/mine`，202 后 toast + 可选轮询 mining_runs 状态。「⋯ 更多」→ 软删除库（DELETE）。
- **文件 Tab**（`KbFileTable.vue`）：
  - 上传：`ElUpload` 拖拽，多文件 + zip；调 `POST /api/kb/{id}/documents`（multipart，`directory` 字段可选）。
  - 表格：文件名 / 目录 / 类型 / 状态徽标 / 上传时间；行操作：下载（`GET .../download`，blob）、改名/标类型（PATCH dialog）、删除（DELETE，当前后端返回 501 → UI 灰显 + tooltip「撤回功能待接」）。
  - 状态徽标复用 `components/common/StatusBadge.vue`（见 §5 状态映射）。
- **成员 Tab**（`KbMembersPanel.vue`）：成员表（用户名/角色/加入时间）；添加（用户名 + 角色 select）→ `POST .../members`；移除 → `DELETE .../members/{userId}`。仅 owner/editor 可见可操作（前端按角色显隐，后端兜底 403）。
- **设置 Tab**（`KbSettingsPanel.vue`）：改名 / 改可见性 / 改描述 → `PATCH /api/kb/{id}`。

---

## 3. 组件拆分

```
kb-ui/src/
  api/kb.ts                          ← 新：useKbApi()
  views/kb/
    KbListView.vue                   ← 新
    KbDetailView.vue                 ← 新
  components/kb/
    KbCreateDialog.vue               ← 新
    KbFileTable.vue                  ← 新
    KbMembersPanel.vue               ← 新
    KbSettingsPanel.vue              ← 新
  types/kb.ts                        ← 新（或并入 types/index.ts）
```
修改：
- `api/proxyClient.ts`：加 `X-KB-User` 拦截器。
- `router/index.ts`：加 `/kb`、`/kb/:kbId`。
- `components/layout/Sidebar.vue`：加「知识库」导航项。

---

## 4. API 契约（`useKbApi()` ↔ 后端端点）

所有方法走 `createProxyClient('mining')`，返回 `extractItems` / `extractOne` 解包。

| 方法 | HTTP | 后端端点 | 说明 |
|---|---|---|---|
| `listKbs(domain)` | GET | `/api/kb?domain=` | 我可见的 KB |
| `createKb(body)` | POST | `/api/kb` | body: `{domain,name,visibility,description}` |
| `getKb(kbId)` | GET | `/api/kb/{kbId}` | |
| `updateKb(kbId,body)` | PATCH | `/api/kb/{kbId}` | body: `{name?,description?,visibility?}` |
| `deleteKb(kbId)` | DELETE | `/api/kb/{kbId}` | 软删 |
| `addMember(kbId,body)` | POST | `/api/kb/{kbId}/members` | body: `{username,role}` |
| `listMembers(kbId)` | GET | `/api/kb/{kbId}/members` | |
| `removeMember(kbId,userId)` | DELETE | `/api/kb/{kbId}/members/{userId}` | |
| `uploadDocument(kbId,file,directory?)` | POST | `/api/kb/{kbId}/documents` | multipart；zip 自动解压 |
| `listDocuments(kbId,directory?)` | GET | `/api/kb/{kbId}/documents?directory=` | |
| `getDocument(kbId,docId)` | GET | `/api/kb/{kbId}/documents/{docId}` | |
| `patchDocument(kbId,docId,body)` | PATCH | `/api/kb/{kbId}/documents/{docId}` | body: `{document_name?,document_type?}` |
| `downloadDocument(kbId,docId)` | GET | `/api/kb/{kbId}/documents/{docId}/download` | responseType blob |
| `mineKb(kbId)` | POST | `/api/kb/{kbId}/mine` | 202，返回 run_id |

> 后端返回的文档对象含派生字段 `status`（uploaded/mining/published/withdrawn/failed）。

---

## 5. 状态徽标映射（文档 / KB）

| 派生状态 | Element Plus tag type | 文案 |
|---|---|---|
| `uploaded` | info | 已上传 |
| `mining` | warning | 挖掘中 |
| `published` | success | 已发布 |
| `withdrawn` | info（淡） | 已撤回 |
| `failed` | danger | 失败 |

KB 可见性：private→danger「私有」/ shared→warning「共享」/ public→success「公开」。

---

## 6. 类型定义（`types/kb.ts`）

```ts
export type KbVisibility = 'private' | 'shared' | 'public'
export type KbDocStatus = 'uploaded' | 'mining' | 'published' | 'withdrawn' | 'failed' | 'unknown'
export type KbMemberRole = 'viewer' | 'editor'

export interface KbSummary {
  id: string; domain: string; name: string; description?: string
  owner_id: string; visibility: KbVisibility; created_at: string
}
export interface KbDetail extends KbSummary {
  status: string; deleted_at: string | null; updated_at: string
}
export interface KbMember {
  kb_id: string; user_id: string; role: KbMemberRole
  added_at: string; username: string; display_name?: string
}
export interface KbDocument {
  id: string; domain: string; kb_id: string; document_key: string
  document_name: string; document_type?: string
  storage_path: string; directory_path?: string; owner_id?: string
  created_at: string; status: KbDocStatus
}
```

**待定缺口**：列表页要显示「我的角色」+「文档数」。后端 `list_visible` 当前不返回这俩。两个选择：
- (a) 前端额外调 `listMembers(kbId)` 推算角色 + `listDocuments(kbId)` 取数量 —— 列表 N 个 KB 要 2N 次额外请求，慢。
- (b) **后端 `list_visible` 附带 `my_role` + `document_count`**（改 KbDB.list_visible 的 SQL，JOIN 聚合）—— 一次请求拿全，推荐。

→ 我倾向 (b)，前端搭建前先给后端 `list_visible` 加这两列（小改动，属后端补丁）。

---

## 7. 路由 + 导航

`router/index.ts` 在 AppLayout children 加：
```ts
{ path: 'kb', name: 'kb', component: () => import('@/views/kb/KbListView.vue') },
{ path: 'kb/:kbId', name: 'kb-detail', component: () => import('@/views/kb/KbDetailView.vue'), props: true },
```
`Sidebar.vue` 加导航项「知识库」（icon: `Collection` / `FolderOpened`），排在「挖掘」附近。

---

## 8. 构建任务拆分

| 阶段 | 内容 | 依赖 |
|---|---|---|
| **F0 后端补丁** | `list_visible` 附带 `my_role` + `document_count`（§6-b） | — |
| **F1 基础设施** | `api/kb.ts` + `types/kb.ts` + proxyClient `X-KB-User` 拦截器 + 路由 + Sidebar nav | F0 |
| **F2 列表页** | `KbListView` + `KbCreateDialog`（新建库） | F1 |
| **F3 详情页-文件** | `KbDetailView` 骨架 + `KbFileTable`（上传/zip/列表/下载/改名） | F2 |
| **F4 详情页-成员+设置** | `KbMembersPanel` + `KbSettingsPanel` | F3 |
| **F5 挖掘触发** | 「挖掘」按钮 + 202 toast +（可选）run 状态轮询 | F3 |

每阶段独立可看、可测。F2 结束你就能看到「知识库列表 + 新建」；F3 结束能「上传 + 管理文件」——这是最核心的可用里程碑。

---

## 9. 怎么连后端测（集成测试要点）

1. 起后端：`main_control_service`（:8910）→ `knowledge_mining`（:8901，连 kb_db_test）。
2. `cd kb-ui && npm run dev` → localhost:5173。
3. 浏览器访问 `/kb`：新建库 → 上传文件（含 zip）→ 看文件列表 + 状态 → 点「挖掘」→（若有 llm_service）看状态变 published → 切换 domain 看隔离 → 加成员/改可见性。
4. 权限验证：临时改 `DEFAULT_KB_USER` 常量为另一个用户名，刷新，验证看不到别人的 private KB。

---

## 10. 不在本计划（明确排除）

- 检索相关 UI（按 KB 组合检索）——待 serving 侧 P5。
- 文档软撤回 UI（后端 501 stub）——后端接了 withdrawal 之后再补 UI。
- 真实登录 —— Phase 2。
- 文档详情页（切片/单元浏览）——现有 `/knowledge/:docId` 可复用，不在本计划新建。
