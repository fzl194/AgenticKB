# 制品入口收紧 + 四区英化 — 设计文档

- 日期：2026-11-22
- 状态：已获用户批准（制品=域管理员+管理员可见/仅菜单隐藏；英化=普通用户可见四区全量；字典=公共词+分区）
- 前置：壳层 i18n 已实施（`2026-11-20-shell-i18n-design.md`，master 3a11aac）——t() store / EP 双通道 / 按用户分键记忆全部复用
- 影响面：仅 kb-ui 前端；零新依赖、零后端改动、零数据库迁移

## 1. 背景与目标

1. 知识制品功能未完善，**不引导普通用户使用**——前端菜单对普通成员隐藏（后端权限本就允许，保持不动）；
2. 英化范围从壳层扩展到**普通用户可见的四区**：概览、知识库、检索记录、MCP 接入（清单 38 文件 + api/store 层错误文案 4 处 ≈ 42 个触点）。

### 现状（2026-11-22 探查 + 审查修订）

- 侧边栏 `artifacts` 现为 `access: 'all'`（61号批次4 为 viewer 运行态视图开的口子）——是唯一对普通成员开放的管理类入口；`/artifacts` 路由无前端门（member 输 URL 可直达）。既有 `Sidebar.spec.ts:48-50` 断言 member **可见**制品——需反转为不可见。
- 四区含中文文案文件 **38 个**：
  - 概览 5：DashboardView、OpsPanel、KbCard、QueryList、`utils/dashboard.ts`（DOC_STATUS_META 等）
  - 知识库 25：5 视图 + 16 组件 + 4 utils（`runStatus.ts`、`documentStructureGraph.ts`、`miningWorkflowPresentation.ts`、`managementError.ts`——审查补齐后三个）
  - 检索记录 4：3 vue + `components/retrieval/timeRange.ts`（校验文案，审查补）
  - MCP 2：McpAccessView、McpKeyConfigPanel
  - 共用件 2：`EmptyState`（默认"暂无数据"，四区广泛使用）、`WordCloud`（tooltip 模板 `${w.name}：${w.value} 次`，检索记录消费）
- **api/store 层前端写死中文**（审查发现，纳入口径见 §3.2）：`api/proxyClient.ts:139`（'请求失败'兜底）、`api/serving.ts:39,43,47`（KB 检索 tab 3 条）、`api/kb.ts:344`、`stores/mining.ts:80`（KB run 详情）。MCP 的 `apiErrorDetail` 在 `McpAccessView.vue:140` 实为别名 `managementErrorMessage`——即 `managementError.ts` 前端映射，非后端透传。
- **明确不在范围**（审查裁定）：`ServiceHealthCard`（唯一消费方是设置页 SystemStatusTab，四区零使用）、`utils/searchScope.ts`（已无运行时消费方，仅剩自身 spec——勿在实施中"发现"）、`components/retrieval/segmentTerms.ts`（分词停用词表，是**语言数据不是文案**，机械替换中文会毁掉它）。
- 2026-11-17 先例：制作工作台收紧 = 菜单 `access: 'domain-users'`，路由不加门。

## 2. 已拍板决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| 制品可见性 | `'domain-users'`（站点管理员+域管理员） | 与制作工作台/用户管理同门；制品是工作台（domain-users）的下游产物 |
| 隐藏深度 | **仅菜单隐藏**，路由不加门 | 工作台先例；产品性隐藏（功能未完善不引导）而非安全门；后端红线不动 |
| 英化范围 | 四区全量 38 文件（含共用件 2 + 6 个映射 utils + api/store 层前端错误文案） | 用户明确"英化的就是普通用户可见的这几个界面"；KB 详情 tab 若半翻会出现一页中英混排 |
| 字典组织 | `common.*` 公共词 + `dashboard.*`/`kb.*`/`records.*`/`mcp.*` 分区 | 33+ 文件高频词（取消/确认/删除/重试/加载中…）重复出现，提炼避免字典线性膨胀；后续新英化区直接复用 |
| 管理页混合态 | 制品/工作台/LLM/设置/范式/一张网等在 EN 态保持中文 | 用户明确只英化四区；管理员为内部用户 |

## 3. 设计

### 3.1 制品菜单收紧（1 文件）

- `Sidebar.vue`：`ALL_NAV` 中 `{ path: '/artifacts', …, access: 'all' }` → `access: 'domain-users'`，注释更新（"功能未完善，暂不引导普通用户；后端权限不动——2026-11-22 用户指令"）。
- 路由、后端、Header pageTitles 均不动；KB 详情内"表格查询"tab（TbTableQueryPanel，KB 内的制品消费面）**保留**（用户确认不动）。

### 3.2 四区英化（38 文件）

**字典结构**（扩容 `src/locales/zh.ts`/`en.ts`，en 编译期对齐 + dict.spec 运行时闸门照旧）：

- `common.*`：动工前先做一轮词表盘点，从四区文件中提取重复 ≥3 次的词（预期：取消/确认/删除/重试/加载中…/暂无数据/搜索/刷新/下载/复制/操作成功/操作失败/请求失败等）；**跨区共用的映射 utils 也归 common**：`runStatus`（概览+KB 双消费）、`managementError`（KB+MCP+设置页共用）；
- 分区：`dashboard.*`、`kb.*`（含 documentStructureGraph/miningWorkflowPresentation 的标签）、`records.*`（含 timeRange 校验文案与 WordCloud tooltip 完整模板 `{name}：{value} 次`——全角冒号属于模板一部分）、`mcp.*`；
- `EmptyState` 默认 text → `common.empty`。

**状态映射 utils 的行为面改动**：`runStatus.ts`、`dashboard.ts`、`documentStructureGraph.ts`、`miningWorkflowPresentation.ts`、`managementError.ts`、`timeRange.ts` 的枚举→中文映射，值改为字典 key，渲染/抛出处 `t()`；被测试断言的映射同步其 spec。**只翻映射值，不翻后端数据**。

**api/store 层前端错误文案的口径（审查裁定）**：`proxyClient.ts` 兜底、`serving.ts`/`kb.ts`/`mining.ts` 的写死中文——这些是**前端代码**（非后端透传），四区用户会看到，纳入翻译（key 归所在区或 common）。已知边界（接受，实施时勿当 bug 报）：共用 utils 会把 EN 带到管理面——`managementError`（设置页 toast）、`miningWorkflowPresentation`（挖掘范式编辑器的算子标签）——EN 用户在管理页看到英文 toast/标签，代价小于成员页中英混排。

**插值约定**：带参数文案（`timeRange` 的 `最长跨 {n} 天`、WordCloud 的 `{name}：{value} 次`）一律走 store 的 `t(key, params)` 插值（`stores/locale.ts:53` 既有约定），禁止调用点字符串拼接。

**不翻清单（口径写死）**：
- 后端真正返回的错误消息（apiErrorDetail 从响应体透出的部分，原样显示）；
- 用户数据：KB 名/描述/文档名/文档内容/检索结果；
- 语言数据：`segmentTerms.ts` 停用词表（是数据不是文案，禁止机械替换）；
- 帮助书 `/help`（23 章中文文档）；
- 非四区页面（EN 态保持中文；共用 utils 的 toast 泄漏除外，见上）。

### 3.3 复用面（不重做）

t() store、按用户分键记忆、EP 双通道（树内 provider + 脱挂重注入）、登录页/壳层切换按钮——全部沿用 2026-11-20 批次，零新增基建。

## 4. 测试

- **Sidebar.spec**：artifacts 可见性三态断言（member **不可见**——反转现有 48-50 行断言 / 域管理员可见 / 站点管理员可见——照工作台同型）；
- **四区既有 spec**：默认 zh 下文案不变则绿——全量冒烟；改动最大的既有中文断言面（预估）：`DashboardView.spec`（~93 行断言）、`RetrievalRecordsPanel.spec`（~73）、`McpKeyConfigPanel.spec`（~54）、`docStatus36.spec`（~35）、`dashboardStats.spec`（~30）、`KbSearchPanel.spec`（~41）+其余 14 个 kb specs、`runStatus.spec`、`documentStructureGraph.spec`（~51）、`managementError.spec`——映射值改 key 的同步按新断言；
- **EN 断言**：每区挑 1-2 个核心组件补英文渲染断言；新用例沿用 `Sidebar.spec` 的 `afterEach localStorage.removeItem('kb-ui-lang')` 隔离模式（jsdom localStorage 跨用例持久）；
- dict.spec 自动覆盖新 key 对齐与空值。

## 5. 提交策略

按区分批提交，每批可独立回滚：①制品收紧+Sidebar.spec → ②dashboard → ③records → ④mcp → ⑤kb 视图 → ⑥kb 组件 → ⑦状态映射 utils（或随所在区提交）。全程显式 add 路径。

## 6. 约束红线

- 只改 `kb-ui/`，**后端零改动**（接口/权限/迁移都不动）；
- 零新依赖（package.json 不动）；
- 类型验证 `npm run build`；vitest 必须在 kb-ui/ 目录下跑（cwd 漂移坑）。

## 7. 不做什么

- 不给 `/artifacts` 加前端路由门（用户拍板仅菜单隐藏）；
- 不翻后端错误消息/用户数据/帮助书；
- 不碰语言数据 `segmentTerms.ts`（停用词表）与死代码 `utils/searchScope.ts`；
- 不动 `ServiceHealthCard`（设置页专属，不在四区）；
- 不动制品区自身文案的英化（其页面保持中文，管理员混合态已接受）；
- 不动 KB"表格查询"tab；
- 不引入 vue-i18n。

## 8. 实施期说明（2026-11-23 回写）

- **触点 44 个**（较 §1 的 42 补入 `views/kb/kbMeta.ts`、`components/mining/workflow/MiningWorkflowTrace.vue`；另有 `MiningOperatorPalette.vue` 2 行消费点适配）。
- **字典结构**：`src/locales/{zh,en}/` 目录，按 `shell / common / dashboard / kbView / kbSearch / kbManage / kbStruct / records / mcp` 九个 part 拆分（§3.2 原写的 `kb.*` 单命名空间因此拆成四个），en part 以 `Record<keyof typeof zhX, string>` 在编译期强制对齐；共约 1040 个 key。
- **新增两个基础模块**（§3.3 原称"零新增基建"，特此更正，机制未变）：`locales/translate.ts`（纯查表）与 `locales/t.ts`（非组件统一入口；无活动 pinia 时回落 zh，使既有中文断言零改动保持绿）。
- **管理面泄漏第三项**（接受）：`apiErrorDetail` 的兜底 `'请求失败'` 在 EN 态显示为 `Request failed`。
- **"不动 KB 表格查询 tab"的准确含义是不隐藏**；其文案已随检索质量簇英化。
- **新增闸门**：`locales/__tests__/keyUsage.spec.ts` 双向对账（未被引用的 key / 调用了字典里不存在的 key）。
- 验收：全量 122 文件 / 908 用例通过，`vue-tsc -b` 与 `vite build` 通过；后端零改动、零新依赖。
