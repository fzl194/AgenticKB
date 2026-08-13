# 概览页重新设计方案（搜索前置版）

日期：2026-08-12
状态：方向已确认（搜索前置）；已按 2026-08-12 评审意见修订（D9/D10 为评审新增，§4.1/§5.1/§5.2/§5.3 有实质改动）

## 1. 背景与现状

概览页（`kb-ui/src/views/DashboardView.vue`，路由 `/`）是所有用户登录后的落地页——`Sidebar` 里它是 `requiresAdmin: false`。当前四个区块（服务状态 ×3 / 知识资产 ×5 / 检索单元类型饼图 / 最近挖掘任务表）**全部是域级全局口径，不区分调用者**。

这套口径定于「KB 中心化」与「Phase-2 鉴权」两波改动之前。两波改动后它对两种角色都不成立：admin 看不到想看的（KB 维度、待处理事项），member 看到了不该看的（别人私有库的挖掘任务）。

代码走查确认的缺陷：

| # | 问题 | 位置 | 性质 |
|---|---|---|---|
| D1 | Run ID 链接 `/mining/{id}` 指向已删除的路由（`863e73f` 起改为 `/kb/:kbId/run/:runId`），点击进空白页 | `DashboardView.vue:57`、`views/knowledge/MentionReviewView.vue:13`、`views/knowledge/OntologyReviewView.vue:16` | 必现功能缺陷 |
| D2 | 「创建时间」列恒为 `-`：`mining_runs` 无 `created_at` 列，`MiningRun` 类型也无此字段；el-table 插槽 `row` 是 `any`，TS 拦不住 | `DashboardView.vue:81` | 必现功能缺陷 |
| D3 | 知识资产 5 张卡在 KB-only 部署恒为 0：`/api/knowledge/stats` 只统计域级 active release，而 KB 挖掘 `publish=False` 且 `publish_release()` 现已显式拒绝 KB build | `routes/knowledge.py:78` | 口径与产品方向脱节 |
| D4 | member 能看到全域所有人的挖掘任务：`GET /api/runs` 无任何身份/KB 过滤，`input_path` 还暴露 `{upload_root}/{kb_id}` | `routes/runs.py:27,431` | 越权 / 信息泄露 |
| D5 | 状态中文映射缺 `queued` / `awaiting_review` / `interrupted`（KB 挖掘常见态），显示为英文；映射里的 `pending` 反而不在 DB CHECK 内。run 状态 CHECK 实为 **7** 个：`queued/running/completed/interrupted/failed/cancelled/awaiting_review`（`002_mining_runtime_postgresql.sql:12`） | `DashboardView.vue:172` | UX |
| D6 | 「查看全部 →」跳 `/kb`，但表格是全域 run 列表——`RunsView` 删除后已无全局任务页 | `DashboardView.vue:86` | UX |
| D7 | 切域竞态：`loadData` 对 stats 与 3 个 health 无 generation 守卫，慢的旧域响应会覆盖新域数字 | `DashboardView.vue:140` | 稳定性 |
| **D8** | **检索默认范围在 KB-only 部署必然失败**：`selectedKbIds` 默认 `[]` → `serving.ts` 空数组时不发 `kbIds` → 后端走域级 active release → `no_active_release`。选择器 placeholder 写的「全部（当前生效发布）」在这类部署里是个不存在的范围 | `SearchView.vue:197,229`、`api/serving.ts:32` | **搜索前置的阻断项** |
| **D9** | **派生状态跨 KB 串味**：`_STATUS_JOIN_SQL` 的 LATERAL 只按 `r.document_key = d.document_key` 关联，而 `build_document_key()` 产的是 `doc:/{相对路径}`、**不含 kb_id**（全局唯一的是 `storage_path`）。两个 KB 各有一个根目录 `spec.pdf` 时，一方的挖掘状态会显示成另一方的 | `kb/db.py:50`、`kb/storage.py:62` | 既有正确性缺陷 |
| **D10** | **`/api/runs/*` 另外 17 个端点完全无鉴权**：除 list 外还有 `/{run_id}`、`/stages`、`/progress`、`/documents/{doc_id}/{stages,artifacts,**segments**,**units**,**relations**}`、`/trace`、`/artifacts`，以及 **`/cancel`、`/publish`、`/resume` 三个写端点**。拿到任一 runId 即可读别人私库的段落/检索单元/关系正文，并中止/发布/恢复别人的挖掘 | `routes/runs.py:481-1347` | 越权 / 未授权变更 |
| D11 | 前端 `getRunDocumentRawContent()` 调 `/api/runs/{runId}/documents/{docId}/raw-content`，**该路由在后端根本不存在**（`runs.py` 无此注册），必然 404 | `kb-ui/src/api/mining.ts:135` | 死接口（本次不处理，另记） |

D8 是本次方向调整后新暴露的：搜索前置的前提是"搜索框开箱能用"，而现在它默认就是坏的。`SearchView.vue:294` 已经有 `no_active_release` 的专门错误文案，说明有人踩过但当成了"数据没准备好"。

D9、D10 是评审阶段新增的：

- **D9 必须先于本方案落地**。它今天只造成一个显示态偏差（list/detail 已在用这段 SQL），但 §4.1 区块 2 把派生状态升级成**可点击的待办任务条**——串味会给 KB-B 的编辑者列出「1 篇文档解析失败 → 去处理」，而那篇文档从没失败过。带着 D9 上线待处理区块，等于把一个显示 bug 放大成"指挥人去处理不存在的问题"。
- **D10 说明 D4 不是"唯一的安全性问题"**。id 是 UUID，堵掉 list 确实关闭了批量枚举这条主路径，但从分享链接/浏览记录拿到 runId 的成本远低于猜。而 `cancel`/`publish`/`resume` 是**零鉴权的跨 KB 变更**，性质比 D4 的读泄露更重。

## 2. 设计方向：搜索前置

行业实践（Glean、Onyx、Dify、RAGFlow 一类的企业搜索/RAG 平台）在落地页上的共识：

1. **搜索框占首屏主体**——知识库的价值在检索，进来就该能搜。
2. **资源列表 + 状态角标**——状态长在知识库卡片上，而不是聚合成首页的一个数字。用户看到「3 个失败」还得自己去找是哪三个，等于把工作推回去。
3. **不给普通用户看数字仪表盘**——用户来这里是做事，不是看统计。纯统计页留存极低。
4. **运维视角独立成页**——终端用户界面与管理控制台分开，而不是同一个 `/` 按角色渲染两套逻辑。

据此，`/` 从「仪表盘」改为「检索入口 + 我的资源」。

## 3. 目标 / 非目标

**目标**

1. `/` 首屏可直接检索，且默认范围开箱可用（修 D8）。
2. member 与 admin 共用同一页面结构，差异只体现在**可见知识库的多少**（`list_visible` 的 admin 短路已实现），不做两套渲染逻辑。
3. 运维内容（服务健康、域级资产统计、检索单元类型分布）移出 `/`。
4. 所有数据经后端身份收敛：`/api/runs` **整族**收口，不只是 list（修 D4 + D10）。
5. 待处理区块所依赖的派生状态必须是可信的——先修 D9，再上区块 2。
6. 顺带修掉 D1、D2、D5；使 D3、D6 随区块删除而消失。

**非目标**

- 不做 per-domain 用户准入（隔离粒度止于 KB）。
- 不做审计流水（当前无审计表）。
- 不在首页复制一套检索结果渲染——首页只负责输入，结果仍由 `SearchView` 呈现。
- 不做实时推送/轮询。

## 4. 页面设计

### 4.1 `/` —— 检索入口 + 我的知识库

> **实现落点（已完成）**：`DashboardView.vue` 重写 + 新增 `components/dashboard/KbCard.vue`；派生逻辑（卡片角标优先级、待处理清单、卡片截断、搜索跳转 target）抽到 `utils/dashboard.ts`，run 状态文案抽到 `utils/runStatus.ts` 并与 `KbRunDetailView` **共用**——那里原本也缺 `queued`、多一个 DB 里不存在的 `pending`，属同一处 D5。
>
> 数据源从 5 个（stats + 3 个 health + runs）降到 **1 个**（`/api/kb/overview`），D7 的竞态面本身大幅收窄；剩下的那一个仍加 generation 守卫，并在 `onUnmounted` 里递增作废。「空态组件」直接复用既有的 `EmptyState.vue`，未另建。

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│              在 6 个知识库中搜索                               │
│    ┌────────────────────────────────────────────┐ ┌──────┐   │
│    │  搜索知识库内容…                             │ │ 搜索 │   │
│    └────────────────────────────────────────────┘ └──────┘   │
│              范围：我的全部知识库  [调整范围]                   │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ ⚠ 待处理（仅在有内容时出现）                                    │
│   核心网文档  挖掘已暂停，等待人工审核 · 2 天前   [去处理 →]     │
│   ODN 资料    3 篇文档解析失败              [查看 →]           │
├──────────────────────────────────────────────────────────────┤
│ 我的知识库                                    [+ 新建]  查看全部→│
│ ┌────────────────┐ ┌────────────────┐ ┌────────────────┐     │
│ │ 核心网文档   ⚠ │ │ ODN 资料    ⏳ │ │ 施工规范     ✓ │     │
│ │ 42 篇 · 拥有者 │ │ 18 篇 · 编辑者 │ │ 7 篇 · 只读   │     │
│ │ 2 篇待处理     │ │ 挖掘中 3/18    │ │ 昨天 14:20    │     │
│ └────────────────┘ └────────────────┘ └────────────────┘     │
├──────────────────────────────────────────────────────────────┤
│ 最近挖掘  （限 5 条）                                          │
│  核心网文档 · 已完成 · +3 ~1 · 2m14s · 08-11 09:12  →         │
└──────────────────────────────────────────────────────────────┘
```

**区块 1：搜索**

- 标题动态显示可搜范围：「在 N 个知识库中搜索」。
- **默认范围 = 我可见的全部知识库，显式传 `kbIds`**（不是留空）。这是修 D8 的核心：留空走的是域级 active release 分支，KB 挖掘 `publish=False` 永不产生 release。
- 「调整范围」展开一个多选，默认全选；取消勾选即收窄。
- 回车/点击 → 跳 `/search?q={query}&kbIds={a,b,c}`，由 `SearchView` 渲染结果。首页不复制结果视图（证据卡、原文下钻、debug 都在那边）。
- 可见 KB 为 0 → 搜索框禁用并提示「你还没有可检索的知识库」，下方直接给建库 CTA。

**「域级发布」这个范围：按域探测，不是一刀切删掉**

初版写的是「不提供『全部（域级发布）』选项」，理由是它在 KB-only 部署里不存在。这个论证跳得太快：域级 active release 由 legacy `/api/runs` 线产出，而那条线**仍在监听、仍默认 `publish=true`**。混合部署（既有 legacy 发布语料、又有 KB 语料）下一刀切删掉，等于把 legacy 语料静默移出检索范围且用户无法找回——这比 D8 更难查，因为它不报错，只是少结果。

改为按域探测：

| 该域有无 active release | 选择器 | 默认值 |
|---|---|---|
| 无（KB-only，出厂常态） | **不出现**「域级发布」项 | 全部可见 KB |
| 有（混合/legacy 部署） | 出现，标注口径「域级生效发布（含未归属知识库的历史语料）」 | 仍是全部可见 KB |

探测数据由 §5.1 的 overview 端点回传 `has_active_release`，不额外开接口。默认值两种部署下都是"全部可见 KB"——只是有 release 的域多一条切回去的路。

**清空选择的行为（必须定死）**

初版一边删掉「全部（域级发布）」选项，一边保留 `serving.ts` 的「空数组不发 `kbIds`」，这两条互相矛盾：UI 已不暴露"全域"概念，用户点一下 `clearable` 的叉就静默落回那条被判定为"不存在的范围"的路径，原地复现 D8。

定为：**清空选择 = 禁用检索按钮 + 行内提示「请至少选择一个知识库」**。不自动还原全选（用户刚做的动作被撤销会更困惑），也不放行到空 `kbIds`。「域级发布」在有 release 的域是选择器里的一个**显式选项**，而不是"什么都不选"的隐式含义——隐式语义正是 D8 的成因。

**区块 2：待处理**（有内容才渲染，无则整块不出现）

任务条形态，**不是计数**——计数没法直接处理，列表可以。v1 收两类：

| 类型 | 判定 | 动作 |
|---|---|---|
| 挖掘等待人审 | 该 KB 有 `status='awaiting_review'` 的 run | 「去处理」→ `/kb/{kbId}/run/{runId}` |
| 文档解析失败 | 该 KB 有派生状态为 `failed` 的文档 | 「查看」→ `/kb/{kbId}`（文件 tab） |

只列**我有写权限**的 KB——对只读的库列出待办没有意义，他也处理不了。判定不调 `KbDB.can_write`（那是单 KB 查询，逐个调就是 N 次往返），直接从 `list_visible` 已返回的 `my_role` 推：`owner|editor|admin` → 可写，`viewer` → 不可写。这两组条件与 `db.py:513-527` 的 SQL 逐项一致。

**前置依赖：D9 未修之前不上这个区块。** 「文档解析失败」整条依赖 `_STATUS_JOIN_SQL` 的派生状态，而它跨 KB 串味（见 D9）。带着串味上线，这个区块会稳定生产假任务。

**区块 3：我的知识库**

卡片网格，按「最近挖掘时间 DESC NULLS LAST，创建时间 DESC」排。每张卡：

- 名称 + 角色徽标（拥有者/编辑者/只读；admin 全通时显示"管理员"）
- 文档数
- **右上角状态角标**：`⚠` 有失败文档 / `⏳` 挖掘中 / `✓` 就绪；副行给具体数字（"2 篇待处理" / "挖掘中 3/18" / 最近挖掘时间）
- 点击进 `/kb/{id}`

首页显示前 6 张，「查看全部 →」进 `/kb`。这是唯一保留的"查看全部"，且语义自洽（修 D6）。

**区块 4：最近挖掘**

限 5 条，列：知识库名 · 状态 · 文档增量 · 耗时 · **开始时间**（用 `started_at`，修 D2）。整行点击进 `/kb/{kbId}/run/{runId}`（依赖接口回传 `kb_id`，修 D1）。

### 4.2 `设置 → 系统状态`（新 tab，admin-only）

> **⚠️ 第 5 步与第 6 步之间有一段空窗**：概览页重写（第 5 步）已把这三块从 `/` 摘掉，而它们要到第 6 步才落到设置页。中间这段时间 `ServiceHealthCard` / `StatsCard` / `PieChart` 无人引用，服务健康在界面上暂时看不到。两步紧邻发布即可，别把第 5 步单独上线。

从 `/` 移过来的运维内容，原样复用现有组件：

- 服务状态 ×3（`ServiceHealthCard`）
- 域级知识资产 ×5（`/api/knowledge/stats`），**顶部标注口径**：「域级 active release」；无 release 时显示「该域无发布语料（KB 挖掘不产生 release）」而不是一排 0——把"没数据"和"口径不适用"区分开（这是 D3 的最小成本处理）
  - **判据是有没有 release，不是计数是不是 0**：后端刻意保留了这个区分——撤回最后一个文档会发布一个**空**的 active build，那时的 0 是真的 0。用 `/api/knowledge/stats` 已有的 `active_releases` 数组判定，不额外开接口、也不去借 overview 的 `has_active_release`（那个端点会跑三段文档聚合，为一个布尔值不值当）。
  - 顺带修了个死类型：前端 `KnowledgeStats.active_release?: string` 是**单数**，而后端返回的是**复数** `active_releases` 数组，这个字段从来取不到值，也从来没人用。
- 检索单元类型分布饼图

理由：健康指标是**出事时才看**的东西，放设置里完全够用；天天顶在首屏反而稀释首屏的信息密度。admin 大部分时间也在建库/看挖掘结果，不在盯指标。

### 4.3 侧边栏调整

- 「检索测试」→ **「检索」**。"测试"是建设者的语言，与搜索前置的定位冲突。
- 顺序不变（概览 / 知识库 / 检索 / …）。首页已有搜索入口，无需再把它提到最前。

### 4.4 共用行为

- **加载**：搜索框立即可交互（它只依赖 KB 列表）；其余三块骨架屏。
- **切域**：整页重取，**所有请求带 generation 守卫**，过期响应丢弃（修 D7）。`alive` 只挡 unmount，挡不住切域竞态。切域必须同时清空搜索范围选择——旧域的 `kb_id` 在新域必然 404。
- **失败**：单区块失败只在该块显示「加载失败 · 重试」，不牵连其余。现在 `Promise.allSettled` 把失败完全吞掉，用户看到的是空白。
- **不轮询**。

## 5. 数据契约

### 5.1 新增 `GET /api/kb/overview?domain={domain}`（mining，KB 层）

一次调用取全首页所需。做成聚合端点而非多个小接口：一个授权点（不会某个子接口漏了收敛）、一次往返、各区块数据不会因分次请求而互相矛盾。

> **⚠️ 路由落点是承重的，不能按惯例追加到 `kbs.py` 末尾。** `kb_router` 的 prefix 是 `/api/kb` 且第 88 行有吞噬型动态段 `@router.get("/{kb_id}")`；追加到文件末尾会让 `/api/kb/overview` 命中 `/{kb_id}`（kb_id="overview"）→ 404「知识库不存在」。这正是 `62aebd1` 的事故形态。
>
> **实际落点（已实现）**：新建 `mining/kb/routes/overview.py`，在 `app.py` 里注册于 `kb_auth_router` 之后、`kb_router` **之前**。比「塞进 auth.py」组织上更干净，又同样不依赖同文件内的行序——后来者往 `kbs.py` 里加路由时不会无意破坏。`app.py` 那段注释已改成「任何新增的 `/api/kb/<字面量>` 路由都挂在这一段」。§9 有三条对应回归：手工装配正序、反序反证、以及对**真实 `create_app()`** 走一遍路由匹配。

```jsonc
{
  "has_active_release": false,              // 该域有无域级 active release，决定检索范围选择器是否出现「域级发布」项
  "kbs": [                                  // 全部可见 KB（不截断——搜索范围要用全集）
    {
      "id": "...", "name": "核心网文档",
      "my_role": "owner",                   // owner|editor|viewer|admin
      "can_write": true,                    // 由 my_role 推导，非单独查询；决定待处理是否列它
      "status_counts": {                    // 只回首页真正渲染的三个数，三个键恒存在
        "total": 42, "mining": 0, "failed": 2
      },
      "last_mined_at": "2026-08-11T09:12:00Z",
      "awaiting_review_run_id": null        // 有则区块 2 列出「等待人审」
    }
  ],
  "recent_runs": [                          // 限 5 条，跨 KB
    { "id": "...", "kb_id": "...", "kb_name": "核心网文档",
      "status": "completed", "total_documents": 12,
      "new_count": 3, "updated_count": 1,
      "started_at": "...", "finished_at": "..." }
  ]
}
```

实现要点：

- 挂 `Depends(current_user)`；先解析可见 KB 集合（复用 `list_visible` 的可见性条件，**含 site admin 全通短路**），后续查询以该集合为边界。
- `kbs` **不截断**——首页只渲染前 6 张卡，但搜索范围需要全集。若某部署 KB 数量极大（>200），再考虑分离一个轻量的 `kb_ids` 字段。
- **不单独回 `document_count`**：它与 `status_counts.total` 是同一个数，两个字段迟早会不一致。卡片上的「N 篇」直接读 `total`。
- **排序在后端做**（最近挖掘 DESC NULLS LAST，其次创建时间 DESC），前端只取前 6 张。`created_at` 仅用于排序，**不进契约**——留着会让前端误以为可以依赖它。
- `can_write` 从 `my_role` 推导（`owner|editor|admin` → true），**不逐 KB 调 `KbDB.can_write`**。
- `status_counts` **只回 `total` / `mining` / `failed` 三个数**。初版设计回 6 态齐全，但首页实际渲染的只有「⚠ N 篇待处理」「⏳ 挖掘中 x/y」「文档数」——`published`/`withdrawn`/`uploaded`/`mined` 四个计数没有任何渲染位。而代价不对称：`_STATUS_JOIN_SQL` 里的 `pub`/`rm` 两条 LATERAL（各自 `asset_publish_releases JOIN asset_build_document_snapshots` 的 EXISTS）**恰恰只为算 published/withdrawn 而存在**。这是每个用户登录后必访的落地页，admin 还要跑全域文档——为四个不显示的数字付两次 release 级 EXISTS 不划算。等有了渲染位再补。
- 为此 `_STATUS_JOIN_SQL` 已拆成 **`_RUN_DOC_JOIN_SQL`**（归属收敛的 run 关联）+ **`_RELEASE_JOIN_SQL`**（贵的那两条 release EXISTS），完整派生仍是两者拼接。聚合只用前者：
  ```sql
  SELECT d.kb_id,
         COUNT(*) AS total,
         COUNT(*) FILTER (WHERE rs.rd_status IN ('pending','processing')) AS mining,
         COUNT(*) FILTER (WHERE rs.rd_status = 'failed')                  AS failed
  FROM asset_documents d
  LEFT JOIN LATERAL (...见下，D9 修复后的版本...) rs ON TRUE
  WHERE d.kb_id = ANY(%s)
  GROUP BY d.kb_id
  ```
  仍是**单独一段聚合**，不塞进 `list_visible`——那条已经有 `document_count` 子查询。
- **该 LATERAL 用 D9 修复后的版本**（已落地）：原来只按 `r.document_key = d.document_key` 关联，`document_key` 不含 kb_id，跨 KB 串味。修复是经 `r.run_id → mining_runs` 补上归属维度：
  ```sql
  JOIN mining_runs mr ON mr.id = r.run_id
  ...  AND mr.domain = d.domain
       AND mr.kb_id IS NOT DISTINCT FROM d.kb_id
  ```
  两点必须原样保留：**`IS NOT DISTINCT FROM` 不能退回 `=`**——legacy 文档与 legacy run 两侧 `kb_id` 都是 NULL，等值比较得 UNKNOWN，会让全部 legacy 文档退回 `uploaded`；**`mr.domain = d.domain`** 是 legacy 场景下唯一的区分手段（kb_id 两边都 NULL 时）。语义结论：legacy 域级 run 不再给 KB 文档定状态——它的 `document_key` 相对的是另一个输入根，对上纯属巧合。**这是 `_STATUS_CASE_SQL`/`_STATUS_JOIN_SQL` 的共享修改，list/detail 一并受益、也一并需要回归**。
- `awaiting_review_run_id` 与 `last_mined_at` **合成一次扫描**（`overview_run_rollup`）：都是按 `kb_id` 分组的 `mining_runs` 聚合，没理由查两遍。前者用 `(ARRAY_AGG(id ORDER BY started_at DESC) FILTER (WHERE status='awaiting_review'))[1]` 取最新一条；FILTER 无匹配行时 `array_agg` 为 NULL，下标取到 NULL，正好是"没有待审"。
- `last_mined_at` **口径定死**：该 KB 下 `status='completed'` 的 run 的最大 `finished_at`；无则 NULL。不含 failed/cancelled/interrupted（"最近挖掘时间"给用户看的是"最近一次成功产出知识"，把失败算进去会让一个反复失败的库看起来很新鲜）。
- **`started_at` / `finished_at` 在 schema 里是 `TEXT` 而非时间类型**（`002_mining_runtime_postgresql.sql:27-28`）。`ORDER BY started_at DESC` 走的是字典序——因为写入侧统一是 `_utcnow()` 的同形态 ISO-8601 才成立。不要在这里引入别的时间格式，也不要以为可以直接做时间运算。
- `recent_runs`：`mining_runs WHERE kb_id = ANY(%s) ORDER BY started_at DESC LIMIT 5`，`JOIN knowledge_bases` 取名。**`kb_id` 必须回传**——这是修 D1 缺的那一环。
- `has_active_release`：`EXISTS(SELECT 1 FROM asset_publish_releases WHERE domain=%s AND status='active')`。单行 EXISTS，供 §5.3 的选择器决定是否呈现「域级发布」项。
- 可见集为空 → 返回空数组，**不是 404**（"还没有知识库"是合法状态）。

### 5.2 `/api/runs/*` 整族加身份护栏（修 D4 + D10）

即使已标废弃，只要还在监听就是绕过口。初版只护 list，评审后扩到整族——理由见 D10：堵掉 list 只关闭了"批量枚举"，而 `cancel`/`publish`/`resume` 是零鉴权的跨 KB 变更，`raw-content` 直接吐原文。

**统一护栏**：全部 18 个路由挂 `Depends(current_user)`，再按下表分级。

| 类别 | 端点 | 规则 |
|---|---|---|
| 列表 | `GET /api/runs` | admin 全域（含 `kb_id IS NULL`）；非 admin 仅 `kb_id = ANY(可见集)`，legacy 域级 run 不可见 |
| 读单 run | `GET /{run_id}`、`/stages`、`/documents*`（含 `segments`/`units`/`relations`）、`/progress`、`/artifacts`、`/trace` | 取 `mining_runs.kb_id` → 该 KB `is_visible` 才放行；`kb_id IS NULL` 仅 admin |
| 写 | `POST /{run_id}/cancel`、`/publish`、`/resume` | 同上但要 `can_write`；`kb_id IS NULL` 仅 admin |
| 创建 | `POST /api/runs`、`/preflight` | 已废弃的域级入口，收紧为 **admin only**（KB 用户走 `/api/kb/{id}/mine`） |

要点：

- 不可见一律 **404**，与 KB 层一致（不泄露存在性）；可见但无写权限才 403。
- 返回结构不变，避免动到既有调用方。
- **会牵动 KB 两个详情页**：`KbRunDetailView`（`getRunTrace` / `resumeRun`）与 `KbRunDocDetailView`（`getRunDocumentSegments|Units|Relations`）走的正是这批端点。它们本来就在 KB 上下文里，用户对该 KB 有可见性，正常路径不受影响——但**必须纳入回归**，这是本条工作量的主要来源，不是"顺手加个 Depends"。
- 单 run 的 kb 归属查询会给每个端点加一次 `SELECT kb_id FROM mining_runs WHERE id=%s`。可接受（主键点查），不要为省这一次而把 kb_id 塞进 token 或信任前端传参。

### 5.3 前端检索默认范围（修 D8）

两处一起改，否则首页和 `/search` 行为不一致：

> **实现落点（已完成 `SearchView` 侧）**：选择语义抽成纯函数放在 `kb-ui/src/utils/searchScope.ts`（`reconcileScopeSelection` / `resolveRequestKbIds` / `canSearchWithScope` / `defaultScopeSelection` / `scopeFromQuery`），组件只负责接线。这么拆是因为「域级发布与知识库互斥」「空选择不等于全域」这些规则**必须能被单测钉住**，散在组件的 watch 里测不了（Element Plus 在 `test/setup.ts` 里被全局 stub，操作选择器等于在测 stub）。
>
> `SearchView` 改用 **`GET /api/kb/overview`** 而不是 `listKbs`——它还需要 `has_active_release`。同一步顺带做了 **`?q=` / `?kbIds=` 初始化**（§6 表里列在 `SearchView` 名下），这样第 5 步的首页跳转是纯增量的。`?kbIds=` **只在首次进入时作数**：切域后那些 id 属于旧域，已经没有意义。

- **首页**：默认 `kbIds = 全部可见 KB 的 id`，显式传。
- **`SearchView`**：`selectedKbIds` 初值从 `[]` 改为**全选**；`watch(currentDomain)` 里重取 KB 后同样全选。
- **范围取不到时不再静默退化**：原来 `loadKbs` 失败会 `kbs=[]` 并注释「退化成只能全域检索」——而那条路径在纯 KB 部署下必然 `no_active_release`，等于把网络/配置问题伪装成"没有数据"。现在显示「检索范围加载失败 · 重试」，检索按钮保持禁用。
- **placeholder「全部（当前生效发布）」删除**，但「域级发布」不是消失而是**降级为按域探测的显式选项**（见 §4.1）：`has_active_release=true` 时选择器里出现一个独立项「域级生效发布（含未归属知识库的历史语料）」，选中它即发空 `kbIds`；为 false 时该项不出现。
- **清空选择不再等于"全域"**：`selectedKbIds` 为空 → 检索按钮禁用 + 行内提示「请至少选择一个知识库」，请求根本不发出。初版保留"空数组 = 全域"的隐式语义与"删掉全域选项"是矛盾的，会让用户点一下叉就原地复现 D8。
- `api/serving.ts` 的「空数组不发 `kbIds`」逻辑**保留不动**——它现在服务于「域级发布」这个显式选项（选中它时前端就是发空数组），而不再是清空选择的兜底。这样 API 层不用改，语义改在 UI 层。

> 边界：`kbIds` 里任一 id 不可读 → 整个请求 **404 `kb_not_found`**（`GlobalExceptionHandler.java:45-49` 是 `NOT_FOUND`；400 的是 `kb_ids_required`，503 的是 `no_active_release`）。后端刻意不做静默子集。由于列表与鉴权同源（都出自 `list_visible`），正常不会触发；切域后未清空选择才会，故 §4.4 要求切域清空。

## 6. 复用 vs 新增

**已有、零改动直接用**

- `KbDB.list_visible(user_id, domain)` —— 已身份收敛，已返回 `my_role` 与 `document_count`
- `SearchView` —— 结果渲染整套复用，首页只传参跳转
- `ServiceHealthCard` / `StatsCard` / `PieChart` —— 平移到「系统状态」tab

**已有但需要改**

- `_STATUS_CASE_SQL` / `_STATUS_JOIN_SQL` —— **不是零改动**：D9 的 kb 维度收敛要落在这里。改动被 list/detail 共享，需一并回归
- `KbDB.can_write` —— **不在 overview 里用**（单 KB 查询，逐个调是 N+1）。待处理区块改从 `my_role` 推导；它在别处的既有用法不动

**需新增**

| 项 | 位置 |
|---|---|
| `GET /api/kb/overview` + `KbDB` 四段查询（kbs / status_counts / awaiting_review / recent_runs）+ `has_active_release` | **`mining/kb/routes/auth.py`（挂 `kb_auth_router`，先于 `kb_router` 注册）** 或 `kbs.py` 中 `/{kb_id}` 之前；`mining/kb/db.py` |
| `/api/runs/*` 整族身份护栏（18 个路由） | `mining/api/routes/runs.py` |
| `DashboardView` 重写（搜索框 / 待处理 / KB 卡片 / 最近挖掘） | `kb-ui/src/views/DashboardView.vue` |
| `KbCard.vue`（带状态角标）、空态组件 | `kb-ui/src/components/kb/`、`components/common/` |
| `SystemStatusTab.vue` | `kb-ui/src/components/settings/` |
| `useKbApi().getOverview()` | `kb-ui/src/api/kb.ts` |
| `SearchView` 支持 `?q=` / `?kbIds=` 初始化 + 默认全选 | `kb-ui/src/views/SearchView.vue` |

## 7. 缺陷处置对照

| # | 处置 |
|---|---|
| D1 死链 | §5.1 回传 `kb_id` → 拼 `/kb/{kbId}/run/{runId}`。**`views/knowledge/MentionReviewView.vue:13` 与 `views/knowledge/OntologyReviewView.vue:16` 的同款死链一并修**：它们有 `runId` 但无 `kbId`，**用 `GET /api/runs/{runId}` 取 `kb_id` 反查即可**（`mining_runs.kb_id` 由 `007` 提供），不必退化成跳 KB 列表；`kb_id` 为 NULL（legacy run）时才隐藏按钮 |
| D2 创建时间 | 改用 `started_at`（`NOT NULL`），列名改「开始时间」 |
| D3 统计恒为 0 | **随区块移出 `/` 而在首页消失**；「系统状态」tab 里加口径标注与无-release 分支 |
| D4 越权 | §5.2（已扩到整族） |
| D5 状态文案 | `statusLabel` 补 `queued` / `awaiting_review` / `interrupted`，删掉 DB 里不存在的 `pending`（`StatusBadge.vue` 的配色**已覆盖全部 7 个 run 状态**，只缺文案） |
| D6 查看全部 | **消失**——唯一保留的「查看全部」指向 `/kb`，与其上方的「我的知识库」区块语义自洽 |
| D7 竞态 | §4.4 generation 守卫；数据源从 5 个降到 2 个，竞态面本身大幅收窄 |
| D8 检索默认范围 | §5.3；「域级发布」按域探测保留为显式选项，清空选择不再隐式等于全域 |
| D9 状态串味 | §5.1：`_STATUS_JOIN_SQL` 的 LATERAL 经 `run_id → mining_runs.kb_id` 补上 kb 维度。**排在待处理区块之前**，否则该区块稳定生产假任务 |
| D10 `/api/runs/*` 无鉴权 | §5.2 整族护栏；写端点额外要 `can_write`，域级创建入口收紧为 admin only |

## 8. 实施顺序

1. **`/api/runs/*` 整族护栏**（§5.2）——安全性问题，独立、可单测，不依赖任何前端决策，先做。含 KB 两个详情页的回归，是 1–6 里工作量最大的一项，别按"顺手加个 Depends"排期。
2. **D9 派生状态收敛**（§5.1 的 LATERAL 修复）——独立于概览改版，修完 list/detail 立刻受益。**必须早于第 5 步**，因为待处理区块建立在它之上。
3. **`GET /api/kb/overview`**（§5.1）——后端聚合端点 + 单测。依赖 2（`status_counts` 直接用修好的 LATERAL）。注意路由落点。
4. **检索默认范围**（§5.3）——独立于概览改版，做完 `SearchView` 立刻可用，也解掉现网的 `no_active_release` 困惑。依赖 3 的 `has_active_release` 字段。
5. **概览页重写**（§4.1）——依赖 1–4。
6. **运维内容迁移**（§4.2）+ 侧边栏改名（§4.3）——纯前端搬运。
7. **D1 的另两处死链**（`MentionReviewView` / `OntologyReviewView`）——独立小修，但反查走 `GET /api/runs/{runId}`，故排在 1 之后。

## 9. 测试要点

- `GET /api/kb/overview`：admin 全通 / owner / editor 成员 / viewer 成员 / public 非成员 / 完全不可见，六种身份的可见集正确；可见集为空返回空数组而非 404；`status_counts` 三个键（`total`/`mining`/`failed`）恒存在；`can_write` 与 `KbDB.can_write` 的判定对每种 `my_role` 一致。
- **路由级回归（D9 之外最容易复发的一条）**，三条一组：正序装配时 `GET /api/kb/overview` 命中 overview handler 且响应体形状正确；**反序装配时确实 404**（反证，证明前一条测的是真约束）；以及对**真实 `create_app()`** 走一遍路由匹配、断言第一个匹配 `/api/kb/overview` 的路由就是 `kb_overview`（前两条测的是手工装配，这条才覆盖 `app.py` 的实际注册顺序）。
- **D9 回归**：两个 KB 各有一个同名同相对路径的文档（如均为根目录 `spec.pdf`），其中一个挖掘失败——另一个的派生状态**不得**变成 `failed`；`status_counts.failed` 归属正确；`list_visible` / 文档详情两条既有路径同样断言。
- `GET /api/runs`：非 admin 拿不到不可见 KB 的 run，也拿不到 `kb_id IS NULL` 的 legacy run；admin 行为与改动前逐字一致（回归）。
- **`/api/runs/{run_id}/*` 护栏**（D10）：非成员对他人 KB 的 run 读端点（尤其 `raw-content`）一律 404；写端点 `cancel`/`publish`/`resume` 对 viewer 403、对非成员 404；`POST /api/runs` 与 `/preflight` 对非 admin 403。KB 两个详情页在正常身份下功能不回退。
- **D8 回归**：KB-only 部署（无 active release）下，首页默认搜索与 `SearchView` 默认搜索都**不得**产生 `no_active_release`；且选择器里**不出现**「域级发布」项。
- **混合部署回归**：域内存在 active release 时，「域级发布」项出现且选中后发出的请求不带 `kbIds`（legacy 语料可达）。
- **清空选择**：`selectedKbIds` 清空后检索按钮禁用、请求不发出，不再落到空 `kbIds` 路径。
- 切域：搜索范围被清空并重新全选；过期响应被丢弃。
- 空态三种：无 KB / 有 KB 无文档 / 无挖掘记录，各自渲染且搜索框状态正确。
- 死链回归：`/kb/{kbId}/run/{runId}` 可达；`kb_id` 为 NULL 的行不渲染成链接；两个 review 页的「返回 Run 详情」在反查到 `kb_id` 时可达、为 NULL 时隐藏。
