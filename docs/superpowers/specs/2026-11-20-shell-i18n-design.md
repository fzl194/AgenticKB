# 壳层中英文切换 — 设计文档

- 日期：2026-11-20
- 状态：已获用户批准（范围=壳层先行 / 存储=浏览器本地 / 机制=零依赖手写）；rev2 按审查修订
- 影响面：kb-ui 前端；零新依赖、零后端改动、零数据库迁移

## 1. 背景与目标

系统内网部署，使用者中英文偏好不一。需要：

1. 界面提供中/英文切换按钮；
2. 记住每个人的默认选择（不同用户互不干扰）；
3. 影响最小。

### 现状观察（2026-11-20）

- **零 i18n 基建**：package.json 无 vue-i18n；src 下 274 个源文件（166 源码 + 108 测试）无任何 `$t()`/locale 痕迹；`main.ts:26` 静态挂载 Element Plus `zhCn` locale。
- **硬编码中文规模巨大**：95 个 .vue 文件含硬编码中文。全量翻译为巨型工程，与"影响最小"冲突。
- **可复用先例**：`stores/domain.ts:6` 已有按用户名分键的 localStorage 持久化模式（`kb-ui-current-domain:{username}`）——本设计的记忆机制直接照抄该模式。
- Element Plus 自带中/英 locale 包（已在依赖内）。

## 2. 已拍板决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| 覆盖范围 | 壳层先行：侧边栏、顶栏、登录页 + Element Plus 内置组件文案 | 95 文件全量翻译是巨型工程；壳层 ~5 文件即可交付，业务页文案暂留中文、后续逐步迁 |
| 偏好存储 | 浏览器 localStorage，按用户名分键 | 内网工位电脑固定，跨设备漫游不值得动后端（迁移+API+登录链路） |
| 实现机制 | 手写轻量 locale 模块（零新依赖） | 符合依赖冻结铁律；壳层文案均为静态短文案，vue-i18n 九成能力用不上；字典 key 结构与 vue-i18n 兼容，将来全站双语可平滑迁移 |
| 默认语言 | 中文 | 现状中文优先；不做浏览器语言探测（YAGNI，可后续加） |

## 3. 架构

### 3.1 新增文件（4 个，~250 行）

| 文件 | 职责 |
|---|---|
| `src/locales/zh.ts` | 扁平字典，值为现有中文文案（如 `nav.kb: '知识库'`） |
| `src/locales/en.ts` | 同 key 英文字典 |
| `src/locales/element.ts` | Element Plus locale 桥：`epLocale(lang)` 返回 zh-cn/en 包；`applyAppLocale(app, lang)` 封装 `provideGlobalConfig({ locale }, app, true)`（element-plus 公开导出且有 d.ts 类型，但属**未文档化 API**，锁定 2.14 行为——调用点留注释，spike 测试即升级绊网） |
| `src/stores/locale.ts` | Pinia store：`lang` 状态、`t(key)` 查询、`setLang()`、`applyUserPreference(username)`、持久化。**app 引用接线**：store 持有 `app` 引用（`main.ts` 在 `app.use(pinia)` 后注入）——`setLang` 在点击回调里跑，`getCurrentInstance()` 为 null，且从 main.ts import app 会经 App.vue 成环 |

### 3.2 修改文件（6 个）

| 文件 | 改动 |
|---|---|
| `App.vue` | ① 外包 `<el-config-provider :locale="epLocale(lang)">`——树内 EP 组件（分页/日期选择/空态等）响应式切换；② `watch(() => auth.user?.username)` → 有名则 `applyUserPreference(username)`、无名（登出）则 `clearUser()`（对抗审查 HIGH-1：不清则登录页切换覆写前用户分键）。**不改 `stores/auth.ts`**，文件清单穷尽 |
| `main.ts` | `app.use(ElementPlus, { locale: epLocale(bootLang) })`——locale 参数从 locale store 启动值取（不再静态 import zhCn）。此挂载承担**脱挂通道**初值（见 3.4） |
| `Sidebar.vue` | `ALL_NAV` 12 个 label 改为字典 key；`未选择知识域`、帮助 tooltip（`平台指南（帮助文档）`）一并接入 |
| `Header.vue` | `pageTitles` 20 项映射（去重 11 个标题）值改 key；健康状态与健康 tooltip（正常/异常、全部正常/存在异常）；角色标签（系统管理员/域管理员/用户）；账户下拉项（修改密码/登出）；修改密码两步 ElMessageBox 弹窗文案 |
| `LoginView.vue` | ~13 条登录文案接入（登录到知识库/管理员验证/用户名工号/密码/处理中/登录/下一步/错误提示/联系管理员等） |
| `ReleaseVersion.vue` | 版本条常驻中文（系统版本/版本加载中…/版本信息不可用/弹窗标题/发布日期/tooltips）接 t()——侧边栏可见面，属壳层（实施期补充） |

### 3.3 切换按钮

- `Header.vue` 右侧（健康灯与账户下拉之间）：紧凑切换控件，显式显示**目标语言**（zh 态显示 `EN`，en 态显示 `中`），避歧义；
- `LoginView` 右上角：同款（覆盖未登录场景）。

### 3.4 Element Plus 文案双通道（关键机制）

EP 有两类文案通道，须分别覆盖：

1. **树内组件**（分页、日期选择、表格空态…）：`App.vue` 的 `el-config-provider :locale` 响应式绑定，切换即时生效。
2. **命令式脱挂组件**（ElMessageBox 确认弹窗、ElMessage 通知——挂到 `document.body`，不在 provider 子树内；项目内 ElMessageBox 用于 46 文件、ElMessage 用于 75 文件，Header 修改密码弹窗即属此类）：
   - 初值：`main.ts` 的 `app.use(ElementPlus, { locale })` 提供 app 级注入；
   - 运行时切换：locale store 的 `setLang` 经由 `src/locales/element.ts` 的 `applyAppLocale(app, lang)` 重调 `provideGlobalConfig({ locale }, app, true)`——该函数（已读安装源 `element-plus/es/components/config-provider/src/hooks/use-global-config.mjs:31-55`）用 `app.provide` 覆写 app 级 provides 并更新模块级 `globalConfig` ref；**此后新建**的 ElMessageBox/ElMessage 注入即取新语言（已挂载的瞬时实例随自身关闭消亡，无残留问题）。
   - 实施计划第一个任务为 spike 验证：切到 EN 后调用 `ElMessageBox.confirm`，断言按钮文案为 OK/Cancel。

### 3.5 store 初始化

初始化**完全在 store 内部**（`defineStore` setup 中直接读 localStorage 全局键，无则 `'zh'`），不依赖 main.ts 预调用——组件测试不跑 main.ts，Sidebar/Header/LoginView 的现有 spec 无需初始化补丁即可挂载。main.ts 在 `app.use(pinia)` 后取 store 启动值仅用于 3.4 的脱挂通道初值。

## 4. 记忆机制

照抄 `domain.ts` 的按用户名分键模式：

- **全局兜底键** `kb-ui-lang`：未登录/登录页阶段读写；
- **按用户分键** `kb-ui-lang:{username}`：登录后读写——alice 选英文不影响 bob。

### 时序

1. **store 首次使用**（登录页/壳层任一组件 setup）：读全局键初始化 `lang`（无则 `'zh'`）——登录页立即正确；
2. **登录成功 / fetchMe 完成**：`App.vue` 对 `auth.user?.username` 的 watcher 触发 `applyUserPreference(username)`——存在该用户分键则覆盖当前 lang；不存在则保持当前语言（不预写）；
3. **用户切换**：写用户分键（若已知 username）+ 同步写全局键；
4. **登出**：watcher 收到 undefined → `clearUser()` 清 currentUser——此后登录页阶段的切换只写全局键，不触碰任何用户分键（对抗审查 HIGH-1 修复）。

**已知且接受的边界**：登录页显示的是全局键（本浏览器上次使用的语言），即前一用户的选择会带到登录页——这是无后端存储的必然结果，刻意接受。

### 容错

- localStorage 不可用（隐私模式等）：静默忽略（照 `tokenStorage.ts:24` 先例），语言仅存内存；
- `t(key)` 查不到：回落 zh 字典 → 再回落 key 本身（漏译开发期立即可见）；
- localStorage 值非法（非 `'zh' | 'en'`）：视为未设置，回落 `'zh'`。

## 5. 不做什么

- 不做浏览器语言自动探测；
- 不改 `document.title`（brand store 管辖）；
- 不翻译业务页面文案（95 文件中除壳层 5 个外的其余 90 个）；
- 不在 46/75 个 ElMessageBox/ElMessage 调用点逐个传 `confirmButtonText` 等文案（走 3.4 脱挂通道整体切换）；
- 不引入 vue-i18n（将来全站双语时再评估迁移）；
- 不动后端与数据库。

## 6. 测试

### 新增测试文件（3 个）

- `src/locales/__tests__/element.spec.ts`——EP 脱挂通道 spike（§3.4 机制的行为级验证 + element-plus 升级绊网；npm test 默认路径内自动跑到）；
- `src/locales/__tests__/dict.spec.ts`——zh/en key 集相等 + 无空值（en 侧类型标注的 keyof 在 zh 标注 Record<string,string> 时退化为 string，编译期不拦漏 key，运行时此测试是唯一闸门）；
- `src/stores/__tests__/locale.spec.ts`：

### `src/stores/__tests__/locale.spec.ts`

- `beforeEach` 清 `kb-ui-lang*` 全部键（jsdom localStorage 跨用例持久，per-user 隔离用例否则互相污染）；
- 默认中文；
- 切换后写全局键 + 用户分键；
- per-user 隔离（alice/bob 互不干扰，参照 `domain.spec.ts` 既有写法）；
- 登出后（`clearUser`）setLang 只写全局键，不触碰任何用户分键（HIGH-1 回归）；
- `applyUserPreference` 有分键覆盖 / 无分键保持；
- 非法存储值回落 `'zh'`；
- `t()` 回落链（当前字典 → zh → key）。

### 现有 spec 同步（完整清单）

| 测试文件 | 影响 |
|---|---|
| `src/components/layout/__tests__/Sidebar.spec.ts` | 最重：大量导航 label 中文断言（概览/知识库/检索记录/知识制品/用户管理/制作工作台/检索范式/未选择知识域及反断言）→ 改为默认 zh 断言 + 补 EN 断言 |
| `src/__tests__/AppSessionSync.spec.ts` | **挂 App.vue 无活动 pinia、无 store mock**（仅 mock stores/auth 模块）——App.vue 新增 `useLocaleStore()` 会抛 "no active Pinia"。修复：该测试补 `createPinia()` 或 vi.mock('stores/locale')，二者择一在计划中定 |
| `src/views/__tests__/LoginView.spec.ts` | `用户未在系统` 等文案断言同步 |
| `src/components/layout/__tests__/HeaderLogout.spec.ts` | 挂载 Header 但不断言中文文案——仅需冒烟确认不红 |
| `src/components/layout/__tests__/MiningWorkflowSidebar.spec.ts` | 挂载主 Sidebar 且正向断言中文 label（挖掘范式/检索范式等）——默认 zh 下应保持绿，冒烟确认 |
| `src/components/layout/__tests__/ReleaseVersion.spec.ts` | 组件新增 locale store 后 3 处 mount 需补 pinia；默认 zh 断言不变（实施期补充） |

### 验证方式

- 类型验证：`npm run build`（vue-tsc references 根 tsconfig 下 `--noEmit` 假通过的既定规矩）；
- 手工验收：登录页切换 → 登录 → 壳层语言保持；另一账号切换互不影响；切 EN 后 EP 分页/表格空态即切；**Header 修改密码弹窗按钮变 OK/Cancel**（3.4 spike 的端到端面）。

## 7. 升级路径

字典 key 采用 vue-i18n 兼容的扁平点号结构。将来若全站双语：引入 vue-i18n（届时走依赖评估）、把 zh/en 字典平移为其 message 资源、把 `t(key)` 调用点替换为 `$t(key)`——key 不变，不返工。
