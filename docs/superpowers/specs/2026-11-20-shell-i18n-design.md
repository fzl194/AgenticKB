# 壳层中英文切换 — 设计文档

- 日期：2026-11-20
- 状态：已获用户批准（范围=壳层先行 / 存储=浏览器本地 / 机制=零依赖手写）
- 影响面：kb-ui 前端；零新依赖、零后端改动、零数据库迁移

## 1. 背景与目标

系统内网部署，使用者中英文偏好不一。需要：

1. 界面提供中/英文切换按钮；
2. 记住每个人的默认选择（不同用户互不干扰）；
3. 影响最小。

### 现状观察（2026-11-20）

- **零 i18n 基建**：package.json 无 vue-i18n；src 下 274 个源文件无任何 `$t()`/locale 痕迹；`main.ts:26` 静态挂载 Element Plus `zhCn` locale。
- **硬编码中文规模巨大**：95 个 .vue 文件含硬编码中文。全量翻译为巨型工程，与"影响最小"冲突。
- **可复用先例**：`stores/domain.ts:6` 已有按用户名分键的 localStorage 持久化模式（`kb-ui-current-domain:{username}`）——本设计的记忆机制直接照抄该模式。
- Element Plus 自带中/英 locale 包（已在依赖内），`el-config-provider` 可动态切换。

## 2. 已拍板决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| 覆盖范围 | 壳层先行：侧边栏、顶栏、登录页 + Element Plus 内置组件文案 | 95 文件全量翻译是巨型工程；壳层 ~5 文件即可交付，业务页文案暂留中文、后续逐步迁 |
| 偏好存储 | 浏览器 localStorage，按用户名分键 | 内网工位电脑固定，跨设备漫游不值得动后端（迁移+API+登录链路） |
| 实现机制 | 手写轻量 locale 模块（零新依赖） | 符合依赖冻结铁律；壳层文案均为静态短文案，vue-i18n 九成能力用不上；字典 key 结构与 vue-i18n 兼容，将来全站双语可平滑迁移 |
| 默认语言 | 中文 | 现状中文优先；不做浏览器语言探测（YAGNI，可后续加） |

## 3. 架构

### 3.1 新增文件（3 个，~200 行）

| 文件 | 职责 |
|---|---|
| `src/locales/zh.ts` | 扁平字典，值为现有中文文案（如 `nav.kb: '知识库'`） |
| `src/locales/en.ts` | 同 key 英文字典 |
| `src/stores/locale.ts` | Pinia store：`lang` 状态、`t(key)` 查询、`setLang()`、`applyUserPreference(username)`、持久化 |

### 3.2 修改文件（5 个）

| 文件 | 改动 |
|---|---|
| `App.vue` | 外包 `<el-config-provider :locale="epLocale">`，按 lang 动态返回 element-plus zh-cn/en locale——**全站所有 EP 组件（分页/确认弹窗/日期选择/空态）随之切换** |
| `main.ts` | 去掉静态 `app.use(ElementPlus, { locale: zhCn })` 的 locale 参数（由 provider 接管）；启动时初始化 locale store |
| `Sidebar.vue` | `ALL_NAV` 的 12 个 label 改为字典 key；`未选择知识域` 与帮助 tooltip 一并接入 |
| `Header.vue` | `pageTitles` 映射值改 key；健康状态（正常/异常）、角色标签（系统管理员/域管理员/用户）、修改密码两步弹窗文案接入 |
| `LoginView.vue` | ~10 条登录文案接入（登录到知识库/用户名工号/密码/下一步/登录/错误提示等） |

### 3.3 切换按钮

- `Header.vue` 右侧（域选择器与健康灯之间）：紧凑 `中 / EN` 切换控件；
- `LoginView` 右上角：同款（覆盖未登录场景）。

## 4. 记忆机制

照抄 `domain.ts` 的按用户名分键模式：

- **全局兜底键** `kb-ui-lang`：未登录/登录页阶段读写；
- **按用户分键** `kb-ui-lang:{username}`：登录后读写——alice 选英文不影响 bob。

### 时序

1. **启动**（main.ts，app.mount 前）：读全局键初始化 `lang`（无则 `'zh'`）——登录页立即正确；
2. **登录成功 / fetchMe 完成**：`applyUserPreference(username)` 读该用户分键；存在则覆盖当前 lang，不存在则保持当前语言（不预写）；
3. **用户切换**：写用户分键（若已知 username）+ 同步写全局键。

### 容错

- localStorage 不可用（隐私模式等）：静默忽略（照 `tokenStorage.ts:24` 先例），语言仅存内存；
- `t(key)` 查不到：回落 zh 字典 → 再回落 key 本身（漏译开发期立即可见）；
- localStorage 值非法（非 `'zh' | 'en'`）：视为未设置，回落 `'zh'`。

## 5. 不做什么

- 不做浏览器语言自动探测；
- 不改 `document.title`（brand store 管辖）；
- 不翻译业务页面文案（95 文件中除壳层 5 个外的其余 90 个）；
- 不引入 vue-i18n（将来全站双语时再评估迁移）；
- 不动后端与数据库。

## 6. 测试

- **locale store 单测**（新增 `src/stores/__tests__/locale.spec.ts`）：
  - 默认中文；
  - 切换后写全局键 + 用户分键；
  - per-user 隔离（alice/bob 互不干扰，参照 `domain.spec.ts` 的既有写法）；
  - `applyUserPreference` 有分键覆盖 / 无分键保持；
  - 非法存储值回落 `'zh'`；
  - `t()` 回落链（当前字典 → zh → key）。
- **现有 spec 同步**：`LoginView.spec.ts` 等壳层文案断言更新。
- **类型验证**：`npm run build`（vue-tsc references 根 tsconfig 下 `--noEmit` 假通过的既定规矩）。
- **手工验收**：登录页切换 → 登录 → 壳层语言保持；另一账号切换互不影响；EP 弹窗按钮语言随切换。

## 7. 升级路径

字典 key 采用 vue-i18n 兼容的扁平点号结构。将来若全站双语：引入 vue-i18n（届时走依赖评估）、把 zh/en 字典平移为其 message 资源、把 `t(key)` 调用点替换为 `$t(key)`——key 不变，不返工。
