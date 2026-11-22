# 制品入口收紧 + 四区英化 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 知识制品菜单对普通成员隐藏（仅前端、后端不动）；概览/知识库/检索记录/MCP 四区约 1005 条中文文案接入 t() 并补齐英文字典。

**Architecture:** 复用壳层批次（2026-11-20）的 locale store / EP 双通道 / 按用户记忆。字典从单文件拆成 `src/locales/{zh,en}/` 目录（shell/common + 四区分区），新增纯函数 `translate` 与无 pinia 回落的 `t()` 助手——非组件代码与组件模板统一 `import { t } from '@/locales/t'`，既有中文断言在无 pinia 的单测里零改动保持绿。

**Tech Stack:** Vue 3 + Pinia + Element Plus 2.14 + Vitest/jsdom（全部现有依赖，零新增）。

**依据 spec:** `docs/superpowers/specs/2026-11-22-artifacts-gate-and-four-section-i18n-design.md`（rev2 审查通过）
**配套清单:** `docs/superpowers/plans/2026-11-22-four-section-i18n-inventory.md`（逐文件中文字符串+行号，脚本产出，42 文件/1005 条）

---

## 硬约束（每个任务开工前重读）

1. **只改 `kb-ui/`，后端零改动**（接口/权限/迁移都不动）；**零新依赖**（package.json 不动）。
2. **所有命令在 `D:\mywork\AgenticKB\kb-ui` 下执行**（cwd 漂移到仓库根会让 vitest 丢 `@` alias 且可能退出 0——上批踩过）。提交命令单独 `cd /d/mywork/AgenticKB`。
3. 提交只显式 add 本任务列出的文件路径（禁止 `git add -A`）；`kb-ui/components.d.ts` 预期脏、不入库。
4. 类型验证必须 `npm run build`（vue-tsc references 下 `--noEmit` 假通过）。
5. **zh 字典值必须与原文逐字节一致**（含全角标点、`…`）——这是既有数百条中文断言不改即绿的根基。
6. 开工第一步：`cd /d/mywork/AgenticKB && git rev-parse HEAD` 记下 pre-batch SHA，Task 11 红线核查用。

## 术语表（en 字典一致性，所有分区任务必须遵守）

| zh | en | zh | en |
|---|---|---|---|
| 知识库 | Knowledge Base | 文档 | Document |
| 检索 | Retrieval | 检索记录 | Retrieval Records |
| 检索单元 | Retrieval Unit | 挖掘 | Mining |
| 切片 | Chunk | 向量化 | Embedding |
| 分段 | Segmentation | 语篇分析 | Discourse Analysis |
| 解析 | Parsing | 已入库/入库 | Indexed |
| 待人审 | Awaiting Review | 一张网 | Onenet |
| MCP 接入 | MCP Access | 钥匙（MCP） | Key |
| 成员 | Member | 编辑者/只读用户 | Editor / Viewer |
| 发布/撤回 | Publish / Withdraw | 替换原件 | Replace Original |
| 运行（run） | Run | 质量评估 | Quality |
| 表格查询 | Table Query | 知识域 | Domain |
| 排队中/运行中/已中断 | Queued / Running / Interrupted | 草稿 | Draft |

与壳层既有译法保持一致：Workbench=制作工作台、Mining Paradigms=挖掘范式、System Settings=系统设置、Healthy/Issues=健康灯。

## 转换规则 R1–R11（分区任务逐条照做）

- **R1 统一入口**：`import { t } from '@/locales/t'`；模板与脚本一律 `t('key')` / `t('key', { n })`。四区文件**不引入** `useLocaleStore`（壳层既有文件保持原样）。
- **R2 只翻用户可见文案**：模板文本节点；属性（label/placeholder/title/tooltip/描述/el-table-column label，静态属性改绑定 `:label="t('…')"`）；脚本里进 UI 的字符串（ElMessage/校验返回/抛给 UI 的 Error/映射值）。**不翻**：代码注释、`console.*`、**匹配数据**（正则源文本、与后端文本比对的键——如 `managementError.ts` 的中文键）、`components/retrieval/segmentTerms.ts` 停用词表。
- **R3 zh 值字节级等于原文**；从清单或源文件原样复制，不许顺手改措辞/标点。
- **R4 插值**：模板字符串 `` `${x} 篇` `` → 字典 `'{x} 篇'` + `t(key, { x })`；模板文本节点里的 `{{ n }}` 同理整句入字典；**禁止调用点字符串拼接**。
- **R5 key 命名**：`<前缀>.<语义驼峰>`（前缀见各任务）。同文案同 key：动手前先查 `common` 与本区字典，已有则复用；跨区通用词提 `common.*` 需在提交说明列出。
- **R6 en 质量**：按术语表；en 值不得残留中文（`≥ … —` 等符号除外）；中英信息量对等，不增删语义。
- **R7 字典落位**：新增 key 写进本任务指定的 `src/locales/zh/<part>.ts` 与 `en/<part>.ts`；en part 类型 `Record<keyof typeof zhX, string>`——漏 key 编译即错。
- **R8 响应性陷阱**：`<script setup>` 顶层**非响应式常量表**（`const TABS = [{ label: '文件' }]`、模块级 options 数组）里的文案**不能**在定义时 `t()`（切语言不重算）——表里存 `labelKey`，渲染处 `t(item.labelKey)`（壳层 Sidebar 的 labelKey 模式）。在 computed/函数/事件回调/模板里调用 `t()` 是响应式的，无需处理。
- **R9 完整性闸门**（每个文件转完即查）：`grep -nP '[\x{4e00}-\x{9fa5}]' <文件> | grep -vP '^[^:]+:\d+:\s*(//|\*|/\*\*|<!--)'` 的每一行剩余命中必须能归入 R2 不翻项，否则就是漏翻。
- **R10 既有断言零放松**：只允许因"断言对象形态变化"等价替换（如常量 `.label` → 函数返回值），断言的中文期望值不变。
- **R11 每个分区任务交付物**：①字典 part（zh+en）②转换后的文件 ③分区既有 spec 全绿 ④**至少一条 EN 渲染/求值断言**（挂载 harness 复制同目录既有 spec 的 mock 块；`beforeEach` 里 `localStorage.setItem('kb-ui-lang','en')` 必须在 `setActivePinia(createPinia())` **之前**——store 在创建时读全局键；`afterEach` 清 `kb-ui-lang`）。

---

## Chunk 1: 基建与行为面（Task 0–4）

### Task 0: 开工准备

- [ ] **Step 1:** 记 pre-batch SHA（硬约束 6）。
- [ ] **Step 2:** 通读本计划"硬约束/术语表/转换规则"；打开配套清单确认 42 文件在列。

### Task 1: 制品菜单收紧

**Files:**
- Modify: `src/components/layout/Sidebar.vue`（ALL_NAV 的 artifacts 行）
- Test: `src/components/layout/__tests__/Sidebar.spec.ts`

- [ ] **Step 1: 先改测试（反转既有断言 + 补管理员两态）**

`Sidebar.spec.ts` 的 `member sees the unified retrieval records entry…` 用例中，把：

```typescript
    // 61号批次4：知识制品对普通成员开放（viewer 也有运行态视图）
    expect(wrapper.text()).toContain('知识制品')
    expect(wrapper.html()).toContain('/artifacts')
```

改为：

```typescript
    // 2026-11-22 收紧：知识制品功能未完善，暂不引导普通用户（仅前端菜单隐藏，后端权限不动）
    expect(wrapper.text()).not.toContain('知识制品')
    expect(wrapper.html()).not.toContain('/artifacts')
```

`shows standalone user management to the selected domain administrator` 与 `shows standalone user management to the site administrator` 两个用例末尾各追加：

```typescript
    // 2026-11-22：知识制品对域管理员/站点管理员仍可见
    expect(wrapper.text()).toContain('知识制品')
    expect(wrapper.html()).toContain('/artifacts')
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/layout/__tests__/Sidebar.spec.ts`
Expected: FAIL（member 用例仍见"知识制品"；域管理员用例此前就可见——仅 member 用例红）

- [ ] **Step 3: 改 Sidebar.vue**

把 ALL_NAV 中：

```typescript
  // 61号批次4：知识制品（表格建表发布，viewer 也有运行态视图）
  { path: '/artifacts', labelKey: 'nav.artifacts', icon: Grid, access: 'all' },
```

改为：

```typescript
  // 61号批次4：知识制品（表格建表发布）。2026-11-22 收紧（用户指令）：功能未完善，
  // 暂不引导普通用户——仅前端菜单隐藏（复用 'domain-users'，与制作工作台同门）；
  // 后端权限不动、路由不加门（手输 URL 仍可达，属产品性隐藏非安全门）。
  { path: '/artifacts', labelKey: 'nav.artifacts', icon: Grid, access: 'domain-users' },
```

- [ ] **Step 4: 跑测试确认通过**

Run: `npx vitest run src/components/layout/__tests__/Sidebar.spec.ts`
Expected: PASS（7 用例）

- [ ] **Step 5: 提交**

```bash
cd /d/mywork/AgenticKB && git add kb-ui/src/components/layout/Sidebar.vue kb-ui/src/components/layout/__tests__/Sidebar.spec.ts && git commit -m "feat(kb-ui): 知识制品菜单收紧为域管理员+站点管理员——仅前端隐藏后端不动"
```

---

### Task 2: 字典目录化 + `t()` 助手 + common 种子 + EN 渲染样板

**Files:**
- Move+Modify: `src/locales/zh.ts` → `src/locales/zh/shell.ts`
- Move+Modify: `src/locales/en.ts` → `src/locales/en/shell.ts`
- Create: `src/locales/zh/{index,common,dashboard,kbView,kbSearch,kbManage,kbStruct,records,mcp}.ts`
- Create: `src/locales/en/{index,common,dashboard,kbView,kbSearch,kbManage,kbStruct,records,mcp}.ts`
- Create: `src/locales/translate.ts`、`src/locales/t.ts`
- Modify: `src/stores/locale.ts`（t 委托 translate，删 messages computed 与字典 import）
- Modify: `src/locales/__tests__/dict.spec.ts`
- Test: `src/components/common/__tests__/EmptyState.spec.ts`（新建，EN 渲染样板）

- [ ] **Step 1: 写 EN 渲染样板测试（先红）**

```typescript
// src/components/common/__tests__/EmptyState.spec.ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import EmptyState from '../EmptyState.vue'
import { useLocaleStore } from '@/stores/locale'

afterEach(() => localStorage.removeItem('kb-ui-lang'))

describe('EmptyState text', () => {
  it('falls back to zh without an active pinia (pure-module t helper)', () => {
    const wrapper = mount(EmptyState)
    expect(wrapper.text()).toContain('暂无数据')
  })

  it('renders the caller-provided text as-is', () => {
    const wrapper = mount(EmptyState, { props: { text: '自定义空态' } })
    expect(wrapper.text()).toContain('自定义空态')
  })

  it('follows the language switch reactively', async () => {
    setActivePinia(createPinia())
    const wrapper = mount(EmptyState, { global: { plugins: [createPinia()] } })
    // 插件与活动 pinia 必须是同一个，否则 t() 读不到切换
    expect(wrapper.text()).toContain('暂无数据')
    useLocaleStore().setLang('en')
    await nextTick()
    expect(wrapper.text()).toContain('No data')
    useLocaleStore().setLang('zh')
    await nextTick()
    expect(wrapper.text()).toContain('暂无数据')
  })
})
```

注意第三个用例：`setActivePinia` 与 mount 插件必须用**同一个 pinia 实例**——写成 `const pinia = createPinia(); setActivePinia(pinia); mount(EmptyState, { global: { plugins: [pinia] } })`（上面为便于阅读拆写，实施时按此合并）。

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/components/common/__tests__/EmptyState.spec.ts`
Expected: FAIL（`暂无数据` 在模板里是写死的，切 EN 不变；或文件尚无 t 可用）

- [ ] **Step 3: 平移既有字典为 shell part**

```bash
mkdir -p src/locales/zh src/locales/en
git mv src/locales/zh.ts src/locales/zh/shell.ts
git mv src/locales/en.ts src/locales/en/shell.ts
```

`zh/shell.ts`：首行改为 `export const zhShell = {`（去掉 `: Record<string, string>`），文件头注释改为"壳层文案字典（2026-11-20 批次原样平移）"，对象结尾 `}` 改 `} as const`。
`en/shell.ts`：`import { zh } from './zh'` 改为 `import type { zhShell } from '../../zh/shell'`；签名改 `export const enShell: Record<keyof typeof zhShell, string> = {`；注释改为"与 zhShell key 严格对齐（keyof 为字面量联合，漏 key/多 key 编译即错）"。

- [ ] **Step 4: 写 `translate.ts` 与 `t.ts`**

```typescript
// src/locales/translate.ts
import { zh } from './zh'
import { en } from './en'
import type { Language } from './element'

const ZH: Record<string, string> = zh
const EN: Record<string, string> = en

/**
 * 纯查表（无响应性、无 pinia 依赖）：当前语言 → zh 兜底 → key 本身（漏译开发期可见）；
 * params 做 {name} 插值，未提供的占位符原样保留。store 的 t 与非组件助手 t 都委托到这里。
 */
export function translate(
  lang: Language,
  key: string,
  params?: Record<string, string | number>,
): string {
  const text = (lang === 'en' ? EN[key] : undefined) ?? ZH[key] ?? key
  if (!params) return text
  return text.replace(/\{(\w+)\}/g, (token, name) =>
    params[name] !== undefined ? String(params[name]) : token,
  )
}
```

```typescript
// src/locales/t.ts
import { getActivePinia } from 'pinia'
import { useLocaleStore } from '@/stores/locale'
import { translate } from './translate'

/**
 * 四区组件与所有非 .vue 模块（utils/api/stores）的统一入口。
 * 有活动 pinia → 走 store.t（读 lang ref，渲染期调用即建立响应依赖，切换语言自动重算）；
 * 无活动 pinia → 回落 zh 纯查表（纯函数单测不崩，既有中文断言零改动）。
 */
export function t(key: string, params?: Record<string, string | number>): string {
  return getActivePinia() ? useLocaleStore().t(key, params) : translate('zh', key, params)
}
```

- [ ] **Step 5: 改 `src/stores/locale.ts`**

删除 `import { zh }`、`import { en }` 与 `messages` computed；原 `t` 函数整体替换为：

```typescript
  /** 查表委托 locales/translate.ts（纯函数）；渲染期读 lang.value 即建立响应依赖。
   * params 支持 {name} 插值。 */
  function t(key: string, params?: Record<string, string | number>): string {
    return translate(lang.value, key, params)
  }
```

并加 `import { translate } from '@/locales/translate'`。其余（持久化/applyAppLocale/clearUser）不动。

- [ ] **Step 6: 写 common part（种子词 + 跨区映射）**

```typescript
// src/locales/zh/common.ts
export const zhCommon = {
  // 通用 UI 词（跨 ≥2 个四区文件重复出现）
  'common.retry': '重试',
  'common.cancel': '取消',
  'common.confirm': '确认',
  'common.save': '保存',
  'common.create': '创建',
  'common.add': '新增',
  'common.update': '更新',
  'common.delete': '删除',
  'common.deletePermanently': '永久删除',
  'common.view': '查看',
  'common.status': '状态',
  'common.name': '名称',
  'common.description': '描述',
  'common.type': '类型',
  'common.size': '大小',
  'common.actions': '操作',
  'common.submittedAt': '提交时间',
  'common.duration': '耗时',
  'common.loading': '加载中…',
  'common.loadFailed': '加载失败',
  'common.requestFailed': '请求失败',
  'common.empty': '暂无数据',
  // 挖掘 run 状态（utils/runStatus.ts：概览+知识库共用）
  'common.runStatus.queued': '排队中',
  'common.runStatus.running': '运行中',
  'common.runStatus.completed': '已完成',
  'common.runStatus.interrupted': '已中断',
  'common.runStatus.failed': '失败',
  'common.runStatus.cancelled': '已取消',
  'common.runStatus.awaiting_review': '待人审',
  // 文档状态（utils/dashboard.ts）
  'common.docStatus.mined': '已入库',
  'common.docStatus.mining': '处理中',
  'common.docStatus.uploaded': '待挖掘',
  'common.docStatus.failed': '失败',
  'common.docStatus.update_failed': '更新失败',
  'common.docStatus.published': '已发布',
  'common.docStatus.withdrawn': '已撤回',
  // 检索单元类型（utils/dashboard.ts）
  'common.unitType.raw_text': '原始文本',
  'common.unitType.contextual_text': '上下文文本',
  'common.unitType.summary': '摘要',
  'common.unitType.generated_question': '生成问题',
  'common.unitType.entity_card': '实体卡片',
  'common.unitType.table_row': '表格行',
  'common.unitType.other': '其他',
  // 挖掘算子族/算子/执行区（utils/miningWorkflowPresentation.ts）
  'common.miningFamily.input_parse': '输入与解析',
  'common.miningFamily.retrieval_assets': '搜索表示与增强',
  'common.miningFamily.asset_publish': '资产与发布',
  'common.miningFamily.other': '其他',
  'common.miningOperator.input_ingest': '输入发现',
  'common.miningOperator.document_parse': '文档解析',
  'common.miningOperator.segment_compile': '切片编译',
  'common.miningOperator.retrieval_unit_project': '检索表示投影',
  'common.miningOperator.query_expansion_generate': '问题别名生成',
  'common.miningOperator.hierarchical_summary_generate': '层级摘要生成',
  'common.miningOperator.embedding': '向量化',
  'common.miningOperator.asset_persist': '资产持久化',
  'common.miningOperator.mining_finalize': '构建收尾',
  'common.miningZone.input': '输入阶段',
  'common.miningZone.document': '逐文档',
  'common.miningZone.global': '整批次',
} as const
```

Task 3 还会往本文件追加 `common.mgmtError.*`（管理面错误码映射）——届时加在末尾 `} as const` 之前。

`en/common.ts` 对应（类型 `Record<keyof typeof zhCommon, string>`，`import type { zhCommon } from '../../zh/common'`）：

```typescript
export const enCommon: Record<keyof typeof zhCommon, string> = {
  'common.retry': 'Retry',
  'common.cancel': 'Cancel',
  'common.confirm': 'Confirm',
  'common.save': 'Save',
  'common.create': 'Create',
  'common.add': 'Add',
  'common.update': 'Update',
  'common.delete': 'Delete',
  'common.deletePermanently': 'Delete Permanently',
  'common.view': 'View',
  'common.status': 'Status',
  'common.name': 'Name',
  'common.description': 'Description',
  'common.type': 'Type',
  'common.size': 'Size',
  'common.actions': 'Actions',
  'common.submittedAt': 'Submitted At',
  'common.duration': 'Duration',
  'common.loading': 'Loading…',
  'common.loadFailed': 'Failed to load',
  'common.requestFailed': 'Request failed',
  'common.empty': 'No data',
  'common.runStatus.queued': 'Queued',
  'common.runStatus.running': 'Running',
  'common.runStatus.completed': 'Completed',
  'common.runStatus.interrupted': 'Interrupted',
  'common.runStatus.failed': 'Failed',
  'common.runStatus.cancelled': 'Cancelled',
  'common.runStatus.awaiting_review': 'Awaiting Review',
  'common.docStatus.mined': 'Indexed',
  'common.docStatus.mining': 'Processing',
  'common.docStatus.uploaded': 'Pending Mining',
  'common.docStatus.failed': 'Failed',
  'common.docStatus.update_failed': 'Update Failed',
  'common.docStatus.published': 'Published',
  'common.docStatus.withdrawn': 'Withdrawn',
  'common.unitType.raw_text': 'Raw Text',
  'common.unitType.contextual_text': 'Contextual Text',
  'common.unitType.summary': 'Summary',
  'common.unitType.generated_question': 'Generated Question',
  'common.unitType.entity_card': 'Entity Card',
  'common.unitType.table_row': 'Table Row',
  'common.unitType.other': 'Other',
  'common.miningFamily.input_parse': 'Input & Parsing',
  'common.miningFamily.retrieval_assets': 'Search Representation & Augmentation',
  'common.miningFamily.asset_publish': 'Assets & Publishing',
  'common.miningFamily.other': 'Other',
  'common.miningOperator.input_ingest': 'Input Discovery',
  'common.miningOperator.document_parse': 'Document Parsing',
  'common.miningOperator.segment_compile': 'Chunk Compilation',
  'common.miningOperator.retrieval_unit_project': 'Retrieval Representation Projection',
  'common.miningOperator.query_expansion_generate': 'Question Alias Generation',
  'common.miningOperator.hierarchical_summary_generate': 'Hierarchical Summary Generation',
  'common.miningOperator.embedding': 'Embedding',
  'common.miningOperator.asset_persist': 'Asset Persistence',
  'common.miningOperator.mining_finalize': 'Build Finalization',
  'common.miningZone.input': 'Input Stage',
  'common.miningZone.document': 'Per Document',
  'common.miningZone.global': 'Whole Batch',
}
```

- [ ] **Step 7: 建空的分区 part 骨架**

七个分区 part（`dashboard`/`kbView`/`kbSearch`/`kbManage`/`kbStruct`/`records`/`mcp`）本任务先建空骨架，由后续任务填充。zh 侧（以 dashboard 为例，导出名=`zh`+首字母大写的 part 名）：

```typescript
// src/locales/zh/dashboard.ts
/** 概览区文案（views/DashboardView + components/dashboard + 本区专属词条）。 */
export const zhDashboard = {
} as const
```

en 侧：

```typescript
// src/locales/en/dashboard.ts
import type { zhDashboard } from '../../zh/dashboard'

export const enDashboard: Record<keyof typeof zhDashboard, string> = {
}
```

其余六个同形（`zhKbView`/`zhKbSearch`/`zhKbManage`/`zhKbStruct`/`zhRecords`/`zhMcp`；文件头注释分别写明"kb 视图 5 文件"/"kb 组件·检索质量簇 8 文件"/"kb 组件·文件库管理簇 8 文件"/"文档结构图映射（utils/documentStructureGraph）"/"检索记录区"/"MCP 接入区"）。

- [ ] **Step 8: 写两个 index**

```typescript
// src/locales/zh/index.ts
import { zhShell } from './shell'
import { zhCommon } from './common'
import { zhDashboard } from './dashboard'
import { zhKbView } from './kbView'
import { zhKbSearch } from './kbSearch'
import { zhKbManage } from './kbManage'
import { zhKbStruct } from './kbStruct'
import { zhRecords } from './records'
import { zhMcp } from './mcp'

/** 全量 zh 字典 = 各 part 合并。part 间 key 不得重复（__tests__/dict.spec.ts 有闸门）。 */
export const zh = {
  ...zhShell,
  ...zhCommon,
  ...zhDashboard,
  ...zhKbView,
  ...zhKbSearch,
  ...zhKbManage,
  ...zhKbStruct,
  ...zhRecords,
  ...zhMcp,
}

export type MessageKey = keyof typeof zh
```

```typescript
// src/locales/en/index.ts
import type { zh } from '../zh'
import { enShell } from './shell'
import { enCommon } from './common'
import { enDashboard } from './dashboard'
import { enKbView } from './kbView'
import { enKbSearch } from './kbSearch'
import { enKbManage } from './kbManage'
import { enKbStruct } from './kbStruct'
import { enRecords } from './records'
import { enMcp } from './mcp'

/** 全量 en 字典：keyof 为字面量联合，缺 part/漏 key 编译即错。 */
export const en: Record<keyof typeof zh, string> = {
  ...enShell,
  ...enCommon,
  ...enDashboard,
  ...enKbView,
  ...enKbSearch,
  ...enKbManage,
  ...enKbStruct,
  ...enRecords,
  ...enMcp,
}
```

- [ ] **Step 9: 扩 `dict.spec.ts`（加 part 撞 key 闸门）**

保留既有两个用例（import 路径 `'../zh'`/`'../en'` 现解析到目录 index，无需改），追加：

```typescript
import { zhShell } from '../zh/shell'
import { zhCommon } from '../zh/common'
import { zhDashboard } from '../zh/dashboard'
import { zhKbView } from '../zh/kbView'
import { zhKbSearch } from '../zh/kbSearch'
import { zhKbManage } from '../zh/kbManage'
import { zhKbStruct } from '../zh/kbStruct'
import { zhRecords } from '../zh/records'
import { zhMcp } from '../zh/mcp'

  it('no key is defined by two dictionary parts (spread would silently overwrite)', () => {
    const all = [
      zhShell, zhCommon, zhDashboard, zhKbView, zhKbSearch,
      zhKbManage, zhKbStruct, zhRecords, zhMcp,
    ].flatMap(part => Object.keys(part))
    expect(new Set(all).size).toBe(all.length)
  })

  it('interpolation placeholders match between zh and en for parameterized messages', () => {
    const placeholders = (text: string) => (text.match(/\{\w+\}/g) ?? []).sort()
    for (const [key, value] of Object.entries(zh)) {
      expect(placeholders(en[key as keyof typeof en]), key)
        .toEqual(placeholders(value))
    }
  })
```

- [ ] **Step 10: 跑基建测试**

Run: `npx vitest run src/locales src/stores/__tests__/locale.spec.ts src/components/common/__tests__/EmptyState.spec.ts`
Expected: dict 4 用例 PASS、element spike PASS、**EmptyState 3 用例中"切换响应"仍红**（模板未接 t）——其余全绿

- [ ] **Step 11: 提交基建**

```bash
cd /d/mywork/AgenticKB && git add kb-ui/src/locales kb-ui/src/stores/locale.ts && git status --short kb-ui/src/locales && git commit -m "refactor(kb-ui): 字典目录化——zh/en分part+translate纯函数+t助手(无pinia回落zh)+common种子+撞key闸门"
```

提交前 `git status --short kb-ui/src/locales` 确认旧的 `zh.ts`/`en.ts` 显示为 rename（`R`），无遗留未跟踪文件。


---

### Task 3: 映射 utils 行为面（runStatus / dashboard / miningWorkflowPresentation / documentStructureGraph / managementError / timeRange）

**Files:**
- Modify: `src/utils/runStatus.ts`、`src/utils/dashboard.ts`、`src/utils/miningWorkflowPresentation.ts`
- Modify: `src/utils/documentStructureGraph.ts`、`src/utils/managementError.ts`、`src/components/retrieval/timeRange.ts`
- Modify（消费点适配，1 行）: `src/components/mining/workflow/MiningOperatorPalette.vue:50`
- Modify: `src/utils/__tests__/docStatus36.spec.ts`（1 处等价替换，见 Step 4）
- Dictionary: `src/locales/zh/{common,dashboard,kbStruct,records}.ts` 及 `en/` 对应
- Test（新建）: `src/utils/__tests__/i18n.en.spec.ts`

**原则**：映射函数**内部**调用 `t()`（调用时取语言），所有调用点零改动；无 pinia 的既有纯函数 spec 经 `t()` 的 zh 回落**断言不变即绿**。

- [ ] **Step 1: 写 EN 求值测试（先红）**

```typescript
// src/utils/__tests__/i18n.en.spec.ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { runStatusLabel } from '@/utils/runStatus'
import {
  pendingTasks, docStatusLabel, documentStatusSlices, unitTypeBars,
} from '@/utils/dashboard'
import {
  miningOperatorLabel, miningZoneLabel, groupMiningOperators,
} from '@/utils/miningWorkflowPresentation'
import {
  managementErrorCodeMessage, managementErrorMessage,
} from '@/utils/managementError'
import { validateCustomRange } from '@/components/retrieval/timeRange'
import type { KbOverviewItem, KbStats } from '@/types/kb'
import type { MiningOperatorDef } from '@/types/miningWorkflow'

// 顺序铁律：全局键必须先于 pinia 创建写入——store 在创建时读它
beforeEach(() => {
  localStorage.setItem('kb-ui-lang', 'en')
  setActivePinia(createPinia())
})
afterEach(() => localStorage.removeItem('kb-ui-lang'))

describe('mapping utils follow the language switch', () => {
  it('runStatusLabel', () => {
    expect(runStatusLabel('queued')).toBe('Queued')
    expect(runStatusLabel('awaiting_review')).toBe('Awaiting Review')
    expect(runStatusLabel('not_a_status')).toBe('not_a_status')
  })

  it('pendingTasks / docStatusLabel / documentStatusSlices / unitTypeBars', () => {
    const kb = {
      id: 'kb1', name: '库一', can_write: true,
      awaiting_review_run_id: 'run1',
      status_counts: { failed: 2, mining: 0, total: 5 },
    } as unknown as KbOverviewItem

    const [review, failed] = pendingTasks([kb])
    expect(review.text).toBe('Mining is paused, awaiting manual review')
    expect(review.actionLabel).toBe('Resolve')
    expect(failed.text).toBe('2 documents failed to parse')
    expect(failed.actionLabel).toBe('View')

    expect(docStatusLabel('mined')).toBe('Indexed')
    expect(documentStatusSlices({ has_active_release: false, document_status: { mined: 3 } } as unknown as KbStats)
      .map(slice => slice.name)).toEqual([
      'Indexed', 'Processing', 'Pending Mining', 'Failed', 'Update Failed',
    ])
    expect(unitTypeBars({ table_row: 4 }).map(bar => bar.name)).toEqual(['Table Row'])
  })

  it('mining operator / zone / family labels', () => {
    expect(miningOperatorLabel('embedding')).toBe('Embedding')
    expect(miningOperatorLabel('mystery')).toBe('mystery')
    expect(miningZoneLabel('global')).toBe('Whole Batch')
    const groups = groupMiningOperators([
      { type: 'mystery', zone: 'input' } as unknown as MiningOperatorDef,
    ])
    expect(groups.map(group => group.label)).toEqual(['Other'])
  })

  it('managementError messages', () => {
    expect(managementErrorCodeMessage('user_deleted')).toBe('User is deleted; restore the account first')
    // 中文键是后端 detail 文本的匹配数据——匹配逻辑不变，输出随语言
    expect(managementErrorCodeMessage('不能删除自己的账号')).toBe('You cannot delete your own account')
  })

  it('custom time range validation', () => {
    expect(validateCustomRange({ days: 'custom', startDate: '', endDate: '' }))
      .toBe('Select both a start date and an end date for the custom range')
    expect(validateCustomRange({ days: 'custom', startDate: '2026-02-01', endDate: '2026-01-01' }))
      .toBe('The start date cannot be later than the end date')
    expect(validateCustomRange({ days: 'custom', startDate: '2020-01-01', endDate: '2026-01-01' }))
      .toBe('The custom range cannot exceed 365 days')
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run src/utils/__tests__/i18n.en.spec.ts`
Expected: FAIL（5 用例全红：映射仍返回中文；`docStatusLabel`/`miningZoneLabel` 未导出）

- [ ] **Step 3: 转换 `runStatus.ts`**

文件头注释首句改为"挖掘 run 状态的文案（key 经 t() 按当前语言翻译）。"并加 `import { t } from '@/locales/t'`；映射表与函数替换为：

```typescript
const RUN_STATUS_LABEL_KEYS: Record<string, string> = {
  queued: 'common.runStatus.queued',
  running: 'common.runStatus.running',
  completed: 'common.runStatus.completed',
  interrupted: 'common.runStatus.interrupted',
  failed: 'common.runStatus.failed',
  cancelled: 'common.runStatus.cancelled',
  awaiting_review: 'common.runStatus.awaiting_review',
}

export function runStatusLabel(status: string): string {
  const key = RUN_STATUS_LABEL_KEYS[status]
  return key ? t(key) : status
}
```

文件末尾 `export const RUN_STATUSES = Object.keys(RUN_STATUS_LABEL_KEYS)`（原引用 `RUN_STATUS_LABELS` 改名）。其余函数不动。

- [ ] **Step 4: 转换 `utils/dashboard.ts`**

加 `import { t } from '@/locales/t'`。四处改动：

```typescript
// pendingTasks 内
      text: t('dashboard.pending.reviewText'),
      actionLabel: t('dashboard.pending.reviewAction'),
// …
      text: t('dashboard.pending.failedDocs', { n: kb.status_counts.failed }),
      actionLabel: t('common.view'),
```

```typescript
export const DOC_STATUS_META: { key: KbDocStatusKey; labelKey: string; color: string }[] = [
  { key: 'mined', labelKey: 'common.docStatus.mined', color: '#10b981' },
  { key: 'mining', labelKey: 'common.docStatus.mining', color: '#f59e0b' },
  { key: 'uploaded', labelKey: 'common.docStatus.uploaded', color: '#64748b' },
  { key: 'failed', labelKey: 'common.docStatus.failed', color: '#ef4444' },
  { key: 'update_failed', labelKey: 'common.docStatus.update_failed', color: '#f97316' },
  { key: 'published', labelKey: 'common.docStatus.published', color: '#0891b2' },
  { key: 'withdrawn', labelKey: 'common.docStatus.withdrawn', color: '#8b5cf6' },
]

/** 文档状态名（按当前语言翻译；label 入字典后不再在常量里求值——R8）。 */
export function docStatusLabel(key: KbDocStatusKey): string {
  return t(DOC_STATUS_META.find(meta => meta.key === key)?.labelKey ?? key)
}
```

（原数组上方大段注释原样保留；`update_failed` 行上方的两行注释也保留。）`documentStatusSlices` 内 `.map(m => ({ name: m.label, …` 改 `name: t(m.labelKey),`。

```typescript
const UNIT_TYPE_LABEL_KEYS: Record<string, string> = {
  raw_text: 'common.unitType.raw_text',
  contextual_text: 'common.unitType.contextual_text',
  summary: 'common.unitType.summary',
  generated_question: 'common.unitType.generated_question',
  entity_card: 'common.unitType.entity_card',
  table_row: 'common.unitType.table_row',
  other: 'common.unitType.other',
}
```

`unitTypeBars` 内 `name: UNIT_TYPE_LABELS[k] ?? k` 改：

```typescript
    .map(([k, v]) => ({
      name: UNIT_TYPE_LABEL_KEYS[k] ? t(UNIT_TYPE_LABEL_KEYS[k]) : k,
      value: v,
    }))
```

**等价替换既有断言（R10）**：`utils/__tests__/docStatus36.spec.ts:38` 的 `expect(DOC_STATUS_META.find(m => m.key === 'mined')?.label).toBe('已入库')` 改为 `expect(docStatusLabel('mined')).toBe('已入库')`，import 行补 `docStatusLabel`。期望值不变。

字典追加——`zh/dashboard.ts`：

```typescript
export const zhDashboard = {
  'dashboard.pending.reviewText': '挖掘已暂停，等待人工审核',
  'dashboard.pending.reviewAction': '去处理',
  'dashboard.pending.failedDocs': '{n} 篇文档解析失败',
} as const
```

`en/dashboard.ts`：

```typescript
export const enDashboard: Record<keyof typeof zhDashboard, string> = {
  'dashboard.pending.reviewText': 'Mining is paused, awaiting manual review',
  'dashboard.pending.reviewAction': 'Resolve',
  'dashboard.pending.failedDocs': '{n} documents failed to parse',
}
```

- [ ] **Step 5: 转换 `utils/miningWorkflowPresentation.ts`**

加 `import { t } from '@/locales/t'`。改动点：

```typescript
export const MINING_OPERATOR_FAMILIES: readonly MiningOperatorFamily[] = [
  { key: 'input_parse', labelKey: 'common.miningFamily.input_parse', types: ['input_ingest', 'document_parse', 'segment_compile'] },
  {
    key: 'retrieval_assets',
    labelKey: 'common.miningFamily.retrieval_assets',
    types: ['retrieval_unit_project', 'query_expansion_generate', 'hierarchical_summary_generate', 'embedding'],
  },
  { key: 'asset_publish', labelKey: 'common.miningFamily.asset_publish', types: ['asset_persist', 'mining_finalize'] },
]
```

接口 `MiningOperatorFamily` 的 `label: string` 改 `labelKey: string`。

```typescript
const MINING_OPERATOR_LABEL_KEYS: Readonly<Record<string, string>> = {
  input_ingest: 'common.miningOperator.input_ingest',
  document_parse: 'common.miningOperator.document_parse',
  segment_compile: 'common.miningOperator.segment_compile',
  retrieval_unit_project: 'common.miningOperator.retrieval_unit_project',
  query_expansion_generate: 'common.miningOperator.query_expansion_generate',
  hierarchical_summary_generate: 'common.miningOperator.hierarchical_summary_generate',
  embedding: 'common.miningOperator.embedding',
  asset_persist: 'common.miningOperator.asset_persist',
  mining_finalize: 'common.miningOperator.mining_finalize',
}

export function miningOperatorLabel(operatorType: string): string {
  const key = MINING_OPERATOR_LABEL_KEYS[operatorType]
  return key ? t(key) : operatorType
}

const MINING_ZONE_LABEL_KEYS: Record<MiningExecutionZone, string> = {
  input: 'common.miningZone.input',
  document: 'common.miningZone.document',
  global: 'common.miningZone.global',
}

export function miningZoneLabel(zone: MiningExecutionZone): string {
  return t(MINING_ZONE_LABEL_KEYS[zone])
}
```

`groupMiningOperators` 内：`label: family.label` → `label: t(family.labelKey)`；`{ key: 'other', label: '其他', items: unknown }` → `{ key: 'other', label: t('common.miningFamily.other'), items: unknown }`。

**消费点适配**：`src/components/mining/workflow/MiningOperatorPalette.vue` 第 32 行 import 改为 `import { groupMiningOperators, miningZoneLabel } from '@/utils/miningWorkflowPresentation'`，第 50 行 `return MINING_ZONE_LABELS[operator.zone]` 改 `return miningZoneLabel(operator.zone)`。（该组件属管理面——EN 态其标签随之变英文，spec 已裁定接受。）

`MINING_OPERATOR_LABELS`/`MINING_ZONE_LABELS` 两个旧导出删除（唯一外部消费点已改；grep 确认无其他引用：`grep -rn "MINING_OPERATOR_LABELS\|MINING_ZONE_LABELS" src` 应只剩本文件内部）。

- [ ] **Step 6: 转换 `utils/documentStructureGraph.ts`**

加 `import { t } from '@/locales/t'`。按下表逐行替换（行号为转换前；模板字符串的 `${x}` → 占位符 + params，R4）：

| 行 | 原文 | 替换为 |
|---|---|---|
| 85–89 | `prose: '正文'` 等 5 项映射值 | 值改 key：`'kbStruct.block.prose'`/`list`/`code`/`formula`/`figure_caption`；**使用处**包 `t(...)`（原样读取该映射的位置，照 R8：在使用/渲染处翻译） |
| 149 | `input.documentTitle \|\| '未命名文档'` | `input.documentTitle \|\| t('kbStruct.untitledDoc')` |
| 150 | `subtitle: '当前文档'` | `subtitle: t('kbStruct.currentDoc')` |
| 174 | `` `章节数量较多，当前结构视图仅构建前 ${MAX_MODEL_SECTIONS} 个章节` `` | `t('kbStruct.diag.tooManySections', { max: MAX_MODEL_SECTIONS })` |
| 176 | `'章节列表已由服务端截断，完整结构请查看高级信息'` | `t('kbStruct.diag.sectionsTruncated')` |
| 184 | `` `发现重复结构元素编号 ${elementId}，已保留为独立章节` `` | `t('kbStruct.diag.dupSection', { id: elementId })` |
| 201 | `` `章节 ${item.title \|\| elementId} 的父章节不可用，已降级到文档根节点` `` | `t('kbStruct.diag.parentMissing', { title: item.title \|\| elementId })` |
| 207 | `label: item.title \|\| '未命名章节'` | `label: item.title \|\| t('kbStruct.untitledSection')` |
| 208 | `` subtitle: `${level} 级标题` `` | `subtitle: t('kbStruct.headingLevel', { level })` |
| 256、286 | `` `${items.length} 个内容片段` / `${unassignedSegments.length} 个内容片段` `` | `t('kbStruct.segmentCount', { n: <原长度表达式> })` |
| 269 | `` `表格数量较多，当前结构视图仅构建前 ${MAX_MODEL_TABLES} 张表格` `` | `t('kbStruct.diag.tooManyTables', { max: MAX_MODEL_TABLES })` |
| 271 | `'表格列表已由服务端截断，完整结构请查看高级信息'` | `t('kbStruct.diag.tablesTruncated')` |
| 285 | `label: '未归属内容'` | `label: t('kbStruct.unassigned')` |
| 302 | `` `发现重复表格编号 ${table.table_id \|\| index}，已保留为独立表格` `` | `t('kbStruct.diag.dupTable', { id: table.table_id \|\| index })` |
| 311 | `` label: table.caption?.trim() \|\| `表格 ${index + 1}` `` | `… \|\| t('kbStruct.tableFallback', { n: index + 1 })` |
| 312 | `` subtitle: `${table.rows} 行 × ${table.columns} 列` `` | `subtitle: t('kbStruct.tableDims', { rows: table.rows, columns: table.columns })` |

85–89 的映射若在**模块级函数之外**被直接消费（如导出常量被组件读取），按 R8 改存 key、消费处 `t()`；若仅在构建函数内部使用，直接 `t(key)`。执行者读文件确认后择一，并在提交说明写明。

字典 `zh/kbStruct.ts` / `en/kbStruct.ts`：

| key | zh | en |
|---|---|---|
| kbStruct.block.prose | 正文 | Paragraph |
| kbStruct.block.list | 列表 | List |
| kbStruct.block.code | 代码 | Code |
| kbStruct.block.formula | 公式 | Formula |
| kbStruct.block.figure_caption | 图片说明 | Figure caption |
| kbStruct.untitledDoc | 未命名文档 | Untitled document |
| kbStruct.currentDoc | 当前文档 | Current document |
| kbStruct.diag.tooManySections | 章节数量较多，当前结构视图仅构建前 {max} 个章节 | Too many sections; the structure view only builds the first {max} |
| kbStruct.diag.sectionsTruncated | 章节列表已由服务端截断，完整结构请查看高级信息 | The section list was truncated by the server; see the advanced details for the full structure |
| kbStruct.diag.dupSection | 发现重复结构元素编号 {id}，已保留为独立章节 | Duplicate structure element id {id}; kept as a separate section |
| kbStruct.diag.parentMissing | 章节 {title} 的父章节不可用，已降级到文档根节点 | The parent of section "{title}" is unavailable; moved to the document root |
| kbStruct.untitledSection | 未命名章节 | Untitled section |
| kbStruct.headingLevel | {level} 级标题 | Heading level {level} |
| kbStruct.segmentCount | {n} 个内容片段 | {n} segments |
| kbStruct.diag.tooManyTables | 表格数量较多，当前结构视图仅构建前 {max} 张表格 | Too many tables; the structure view only builds the first {max} |
| kbStruct.diag.tablesTruncated | 表格列表已由服务端截断，完整结构请查看高级信息 | The table list was truncated by the server; see the advanced details for the full list |
| kbStruct.unassigned | 未归属内容 | Unassigned content |
| kbStruct.diag.dupTable | 发现重复表格编号 {id}，已保留为独立表格 | Duplicate table id {id}; kept as a separate table |
| kbStruct.tableFallback | 表格 {n} | Table {n} |
| kbStruct.tableDims | {rows} 行 × {columns} 列 | {rows} rows × {columns} columns |

- [ ] **Step 7: 转换 `utils/managementError.ts`**（⚠ 中文键是匹配数据，禁翻）

加 `import { t } from '@/locales/t'`。两个消息表改成 **code → key**（键名原样不动——含 `CODE_ALIASES`/`CURRENT_MESSAGES` 里那 3 个中文键，它们用来比对后端 detail 文本）：

```typescript
const STABLE_KEYS: Record<string, string> = {
  username_reserved: 'common.mgmtError.username_reserved',
  user_deleted: 'common.mgmtError.user_deleted',
  user_not_active: 'common.mgmtError.user_not_active',
  last_active_admin: 'common.mgmtError.last_active_admin',
  cannot_delete_self: 'common.mgmtError.cannot_delete_self',
  user_owns_active_kbs: 'common.mgmtError.user_owns_active_kbs',
  owner_target_not_domain_bound: 'common.mgmtError.owner_target_not_domain_bound',
  domain_grants_must_not_be_empty: 'common.mgmtError.domain_grants_must_not_be_empty',
  domain_admin_requires_password: 'common.mgmtError.domain_admin_requires_password',
  cannot_manage_domain_admin: 'common.mgmtError.cannot_manage_domain_admin',
  mcp_key_must_be_revoked_before_delete: 'common.mgmtError.mcp_key_must_be_revoked_before_delete',
  invalid_member_role: 'common.mgmtError.invalid_member_role',
  import_validation_failed: 'common.mgmtError.import_validation_failed',
}

// 别名归一（原样保留）：中文键 = 后端 detail 文本的匹配数据，**不得翻译**
const CODE_ALIASES: Record<string, string> = {
  user_owns_knowledge_bases: 'user_owns_active_kbs',
  domain_has_owned_kbs: 'user_owns_active_kbs',
  domains_must_not_be_empty: 'domain_grants_must_not_be_empty',
  import_user_not_active: 'user_not_active',
  '至少保留一个启用的管理员': 'last_active_admin',
  '不能删除自己的账号': 'cannot_delete_self',
  '请先吊销钥匙，再删除记录': 'mcp_key_must_be_revoked_before_delete',
}

const CURRENT_KEYS: Record<string, string> = {
  '至少保留一个启用的管理员': STABLE_KEYS.last_active_admin,
  '不能删除自己的账号': STABLE_KEYS.cannot_delete_self,
  '请先吊销钥匙，再删除记录': STABLE_KEYS.mcp_key_must_be_revoked_before_delete,
  confirm_username_mismatch: 'common.mgmtError.confirm_username_mismatch',
  password_changed_concurrently: 'common.mgmtError.password_changed_concurrently',
  initial_password_not_required: 'common.mgmtError.initial_password_not_required',
  new_owner_has_same_name_kb: 'common.mgmtError.new_owner_has_same_name_kb',
  owner_target_not_found: 'common.mgmtError.owner_target_not_found',
  owner_target_inactive: 'common.mgmtError.owner_target_inactive',
  owner_previous_inactive: 'common.mgmtError.owner_previous_inactive',
  owner_transfer_actor_forbidden: 'common.mgmtError.owner_transfer_actor_forbidden',
  user_not_found: 'common.mgmtError.user_not_found',
  domain_membership_not_found: 'common.mgmtError.domain_membership_not_found',
  domain_admin_required: 'common.mgmtError.domain_admin_required',
  site_admin_does_not_require_domain_grant: 'common.mgmtError.site_admin_does_not_require_domain_grant',
  invalid_domain: 'common.mgmtError.invalid_domain',
  domain_not_bound: 'common.mgmtError.domain_not_bound',
  import_changed_since_preview: 'common.mgmtError.import_changed_since_preview',
  file_too_large: 'common.mgmtError.file_too_large',
  unsupported_file_type: 'common.mgmtError.unsupported_file_type',
  empty_import: 'common.mgmtError.empty_import',
  missing_required_columns: 'common.mgmtError.missing_required_columns',
  too_many_rows: 'common.mgmtError.too_many_rows',
  formula_not_allowed: 'common.mgmtError.formula_not_allowed',
  csv_must_be_utf8: 'common.mgmtError.csv_must_be_utf8',
  invalid_xlsx: 'common.mgmtError.invalid_xlsx',
  xlsx_archive_limits_exceeded: 'common.mgmtError.xlsx_archive_limits_exceeded',
  username_required: 'common.mgmtError.username_required',
  conflicting_display_name: 'common.mgmtError.conflicting_display_name',
}
```

函数体里的表名引用同步改名（`STABLE_MESSAGES[`→`STABLE_KEYS[`、`CURRENT_MESSAGES[`→`CURRENT_KEYS[`，共 4 处），末尾两个导出函数改为：

```typescript
export function managementErrorCodeMessage(code: string): string {
  const trimmed = code.trim()
  const normalized = stableCode(trimmed)
  if (normalized) {
    return t(STABLE_KEYS[normalized] ?? CURRENT_KEYS[normalized] ?? normalized)
  }
  return CURRENT_KEYS[trimmed] ? t(CURRENT_KEYS[trimmed]) : trimmed
}

export async function managementErrorMessage(
  error: unknown,
  fallback: string = t('common.requestFailed'),
): Promise<string> {
  const code = codeFromValue(responseData(error))
  if (code) return managementErrorCodeMessage(code)

  const detail = await apiErrorDetail(error)
  const translated = managementErrorCodeMessage(detail)
  if (translated !== detail) return translated
  return detail && detail !== t('common.requestFailed') ? detail : fallback
}
```

`STABLE_MESSAGES.last_active_admin` 这类别名取值已在上面改写成 `STABLE_KEYS.last_active_admin`。其余辅助函数（`asRecord`/`stableCode`/`codeFromValue`/`responseData`）**原样保留**，仅 `stableCode` 内两处表名改名。

字典——追加到 `zh/common.ts` 与 `en/common.ts` 末尾（`} as const` / `}` 之前）：

| key（`common.mgmtError.` 后缀） | zh（原样） | en |
|---|---|---|
| username_reserved | 该用户名已被已删除账号保留，请恢复原账号 | This username is reserved by a deleted account; restore the original account first |
| user_deleted | 用户已删除，请先恢复账号 | User is deleted; restore the account first |
| user_not_active | 用户未启用，无法执行该操作 | User is disabled; the operation cannot be performed |
| last_active_admin | 至少保留一个启用的系统管理员 | Keep at least one active system administrator |
| cannot_delete_self | 不能删除自己的账号 | You cannot delete your own account |
| user_owns_active_kbs | 该用户仍拥有知识库，请先转移所有者 | This user still owns knowledge bases; transfer ownership first |
| owner_target_not_domain_bound | 新所有者必须是已启用且绑定该知识域的用户 | The new owner must be an active user bound to this domain |
| domain_grants_must_not_be_empty | 启用的普通用户至少需要一个知识域 | An active regular user needs at least one domain |
| domain_admin_requires_password | 设为域管理员前必须设置密码 | Set a password before making this user a domain administrator |
| cannot_manage_domain_admin | 域管理员不能修改或移除其他域管理员 | A domain administrator cannot modify or remove other domain administrators |
| mcp_key_must_be_revoked_before_delete | 请先吊销钥匙，再删除记录 | Revoke the key before deleting the record |
| invalid_member_role | 知识库成员角色只能是编辑者或只读用户 | A knowledge base member must be either an editor or a viewer |
| import_validation_failed | 导入校验失败，请修正错误行后重试 | Import validation failed; fix the invalid rows and retry |
| confirm_username_mismatch | 输入的用户名与待删除账号不一致 | The entered username does not match the account to be deleted |
| password_changed_concurrently | 用户密码状态已变化，请刷新后重试 | The password state has changed; refresh and retry |
| initial_password_not_required | 该用户已有密码，无需再次设置初始密码 | This user already has a password; no initial password is needed |
| new_owner_has_same_name_kb | 新所有者在当前域已有同名知识库 | The new owner already has a knowledge base with the same name in this domain |
| owner_target_not_found | 新所有者不存在 | The new owner does not exist |
| owner_target_inactive | 新所有者未启用或已删除 | The new owner is disabled or deleted |
| owner_previous_inactive | 原所有者未启用，不能保留为编辑者 | The previous owner is disabled and cannot be kept as an editor |
| owner_transfer_actor_forbidden | 你没有转移该知识库所有者的权限 | You are not allowed to transfer ownership of this knowledge base |
| user_not_found | 用户不存在或已删除 | User does not exist or has been deleted |
| domain_membership_not_found | 用户不属于当前知识域 | User does not belong to the current domain |
| domain_admin_required | 需要当前知识域管理员权限 | Domain administrator permission is required |
| site_admin_does_not_require_domain_grant | 系统管理员默认全域通行，无需分配域权限 | System administrators can access all domains; no domain grant is needed |
| invalid_domain | 知识域不存在或未启用 | Domain does not exist or is disabled |
| domain_not_bound | 未绑定该知识域，请联系管理员分配 | You are not bound to this domain; contact an administrator |
| import_changed_since_preview | 预检后用户数据已变化，请重新预检 | User data changed after the pre-check; run the pre-check again |
| file_too_large | 文件超过 5 MB 上限 | File exceeds the 5 MB limit |
| unsupported_file_type | 仅支持 CSV 或 XLSX 文件 | Only CSV or XLSX files are supported |
| empty_import | 导入文件没有数据 | The import file has no data |
| missing_required_columns | 导入文件缺少必需列 | The import file is missing required columns |
| too_many_rows | 导入文件超过 5,000 行上限 | The import file exceeds the 5,000-row limit |
| formula_not_allowed | 导入字段不能包含公式 | Import fields must not contain formulas |
| csv_must_be_utf8 | CSV 文件必须使用 UTF-8 编码 | CSV files must be UTF-8 encoded |
| invalid_xlsx | XLSX 文件无效或已损坏 | The XLSX file is invalid or corrupted |
| xlsx_archive_limits_exceeded | XLSX 文件解压规模超过安全限制 | The decompressed XLSX exceeds the safety limit |
| username_required | 用户名不能为空 | Username is required |
| conflicting_display_name | 同一用户名存在冲突的显示名 | The same username has conflicting display names |

- [ ] **Step 8: 转换 `components/retrieval/timeRange.ts`**

加 `import { t } from '@/locales/t'`，`validateCustomRange` 的三个 return 改为：

```typescript
  if (!state.startDate || !state.endDate) return t('records.range.missingDates')
  // ISO 日期字符串的字典序即时间序
  if (state.startDate > state.endDate) return t('records.range.startAfterEnd')
  const spanDays = (new Date(state.endDate).getTime() - new Date(state.startDate).getTime()) / 86400000
  if (spanDays > MAX_CUSTOM_RANGE_DAYS) return t('records.range.maxSpan', { days: MAX_CUSTOM_RANGE_DAYS })
```

字典 `zh/records.ts` / `en/records.ts`：

```typescript
export const zhRecords = {
  'records.range.missingDates': '自定义时间需要选择开始与结束日期',
  'records.range.startAfterEnd': '开始日期不能晚于结束日期',
  'records.range.maxSpan': '自定义时间最长跨 {days} 天',
} as const
```

```typescript
export const enRecords: Record<keyof typeof zhRecords, string> = {
  'records.range.missingDates': 'Select both a start date and an end date for the custom range',
  'records.range.startAfterEnd': 'The start date cannot be later than the end date',
  'records.range.maxSpan': 'The custom range cannot exceed {days} days',
}
```

- [ ] **Step 9: 跑映射 utils 全部相关 spec**

Run: `npx vitest run src/utils/__tests__ src/components/retrieval/__tests__/segmentTerms.spec.ts src/components/layout`
Expected: 全 PASS——`i18n.en.spec.ts` 5 用例绿；`runStatus.spec`/`dashboard.spec`/`dashboardStats.spec`/`docStatus36.spec`/`documentStructureGraph.spec`/`managementError.spec`/`miningWorkflowGraph.spec` 的既有中文断言**未改即绿**（`t()` 无 pinia 回落 zh）

- [ ] **Step 10: 提交**

```bash
cd /d/mywork/AgenticKB && git add kb-ui/src/utils kb-ui/src/components/retrieval/timeRange.ts kb-ui/src/components/mining/workflow/MiningOperatorPalette.vue kb-ui/src/locales && git commit -m "feat(kb-ui): 六个映射utils接t()——调用点零改动/中文匹配键保留/EN求值测试"
```

提交前 `git status --short kb-ui/src/utils` 确认只含本任务 7 个文件（6 源 + docStatus36.spec + 新 spec）。

---

### Task 4: api/store 层错误文案 + 共用件（EmptyState / WordCloud）

**Files:**
- Modify: `src/api/proxyClient.ts`（`return '请求失败'`）、`src/api/serving.ts`（3 条）、`src/api/kb.ts`（1 条）、`src/stores/mining.ts`（1 条）
- Modify: `src/components/common/EmptyState.vue`、`src/components/charts/WordCloud.vue`
- Dictionary: `zh/{kbView,kbManage,records}.ts` 及 `en/` 对应
- Test: `src/utils/__tests__/i18n.en.spec.ts`（追加用例）

- [ ] **Step 1: 追加 EN 用例（先红）**

在 `i18n.en.spec.ts` 末尾（`describe` 内）追加；文件顶部补 import：

```typescript
import { apiErrorDetail } from '@/api/proxyClient'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import EmptyState from '@/components/common/EmptyState.vue'
```

```typescript
  it('api fallback error text', async () => {
    expect(await apiErrorDetail(new Error(''))).toBe('Request failed')
  })

  it('EmptyState follows the language (imported t helper in a template)', async () => {
    const wrapper = mount(EmptyState)
    expect(wrapper.text()).toContain('No data')
    useLocaleStore().setLang('zh')
    await nextTick()
    expect(wrapper.text()).toContain('暂无数据')
  })
```

并补 `import { useLocaleStore } from '@/stores/locale'`。

- [ ] **Step 2: 跑测试确认新增用例失败**

Run: `npx vitest run src/utils/__tests__/i18n.en.spec.ts`
Expected: FAIL（`Expected 'Request failed'` 收到 `'请求失败'`；EmptyState 仍中文）

- [ ] **Step 3: 转换 api/store 四处**

各文件加 `import { t } from '@/locales/t'`。

`api/proxyClient.ts`（原 139 行）：

```typescript
  return t('common.requestFailed')
```

`api/serving.ts`（三处 `new Error(...)`）：

```typescript
      return new Error(t('kbView.search.noActiveBuild'))
```
```typescript
      return new Error(t('kbView.search.kbNotFound'))
```
```typescript
    return new Error(t('kbView.search.forbidden'))
```

`api/kb.ts`（原 344 行）：`throw new Error(t('kbManage.replace.invalidRevision'))`
`stores/mining.ts`（原 80 行）：`error.value = t('kbView.runDetail.partialLoad')`

字典追加：

`zh/kbView.ts` / `en/kbView.ts`：

| key | zh（原样） | en |
|---|---|---|
| kbView.search.noActiveBuild | 所选知识库暂无可检索内容：尚未完成挖掘，或全部文档挖掘失败。请先完成一次成功的挖掘（部分文档成功即可检索）。 | The selected knowledge base has no retrievable content: mining has not completed, or all documents failed to mine. Complete a successful mining run first (partial success is enough to search). |
| kbView.search.kbNotFound | 无法访问所选知识库：库不存在，或当前账号无读取权限（需库主/成员/public/站点管理员）。 | Cannot access the selected knowledge base: it does not exist, or this account cannot read it (owner/member/public/site administrator required). |
| kbView.search.forbidden | 没有访问所选知识库的权限，请确认知识库可见性或联系管理员。 | No permission to access the selected knowledge base; check its visibility or contact an administrator. |
| kbView.runDetail.partialLoad | 任务已加载，但部分进度或文档明细暂时不可用 | The task was loaded, but some progress or document details are temporarily unavailable |

`zh/kbManage.ts` / `en/kbManage.ts`：

| key | zh（原样） | en |
|---|---|---|
| kbManage.replace.invalidRevision | 文件版本信息无效，请刷新后重试 | The file revision is invalid; refresh and retry |

- [ ] **Step 4: 转换 EmptyState 与 WordCloud**

`EmptyState.vue`：

```vue
    <p class="empty-state__text">{{ text || t('common.empty') }}</p>
```

```typescript
import { FolderOpened } from '@element-plus/icons-vue'
import { t } from '@/locales/t'

withDefaults(defineProps<{
  text?: string
  icon?: typeof FolderOpened
}>(), {
  icon: FolderOpened,
})
```

（删除 `text` 的默认值——`withDefaults` 的默认值在组件定义期求值且无响应性，R8；空字符串/未传一律回落 `common.empty`。）

`WordCloud.vue`：`:title="t('records.wordTooltip', { name: w.name, value: w.value })"`，加 `import { t } from '@/locales/t'`。字典 `records` part 追加：

| key | zh（原样） | en |
|---|---|---|
| records.wordTooltip | {name}：{value} 次 | {name}: {value} times |

- [ ] **Step 5: 跑相关 spec**

Run: `npx vitest run src/utils/__tests__/i18n.en.spec.ts src/api src/stores/__tests__/mining.spec.ts src/components/common src/components/charts src/components/retrieval src/components/kb`
Expected: 全 PASS（api 层 spec 的中文兜底断言未改即绿；`i18n.en.spec.ts` 7 用例绿；EmptyState 样板 3 用例绿）

- [ ] **Step 6: 提交**

```bash
cd /d/mywork/AgenticKB && git add kb-ui/src/api/proxyClient.ts kb-ui/src/api/serving.ts kb-ui/src/api/kb.ts kb-ui/src/stores/mining.ts kb-ui/src/components/common/EmptyState.vue kb-ui/src/components/charts/WordCloud.vue kb-ui/src/utils/__tests__/i18n.en.spec.ts kb-ui/src/locales && git commit -m "feat(kb-ui): api/store层前端错误文案接t()+EmptyState/WordCloud共用件"
```

---

## Chunk 2: 分区转换——概览 / 检索记录 / MCP（Task 5–7）

> **Task 5–10 是同一套动作的六次重复**，每个任务照"分区任务动作清单"执行：
>
> 1. **EN 渲染测试先行（红）**：在指定样板 spec 旁新建 `<组件名>.en.spec.ts`，复制样板 spec 的全部 `vi.mock`/挂载代码；`beforeEach` 先 `localStorage.setItem('kb-ui-lang','en')` 再 `setActivePinia(createPinia())`，`afterEach` 清键；断言该组件清单里**一个确定会渲染**的字符串的 en 值（挑选渲染路径最短的那条，如标题/表头/按钮）。
> 2. **写字典 part**：按配套清单逐文件把字符串录入本区 `zh/<part>.ts`（R3 逐字节原样）与 `en/<part>.ts`（R5/R6）；key 前缀见任务。
> 3. **转换文件**：R1–R8 逐条执行；每个文件转完跑 R9 剩余中文检查，逐行归类（注释/匹配数据/不翻项），有漏即补。
> 4. **跑本任务全部 spec**（命令见任务）→ 全绿。
> 5. **提交**（只 add 列出的文件 + 本区字典 part + 新 spec）。
>
> 清单里的行号是转换前行号；一行多句时全部处理。清单可能漏掉跨行模板文本——R9 检查是最终闸门。

### Task 5: 概览区（`dashboard.*`）

**Files:**
- Modify: `src/views/DashboardView.vue`（清单 27 条）、`src/components/dashboard/OpsPanel.vue`（12）、`KbCard.vue`（8）、`QueryList.vue`（1）
- Dictionary: `src/locales/zh/dashboard.ts`、`en/dashboard.ts`（Task 3 已有 3 个 key，追加）
- Test（新建）: `src/components/dashboard/__tests__/OpsPanel.en.spec.ts`（样板：`OpsPanel.spec.ts`）

- [ ] **Step 1:** 动作清单 1（EN 用例断言 OpsPanel 内一条标题/表头的 en 值）
- [ ] **Step 2:** Run: `npx vitest run src/components/dashboard/__tests__/OpsPanel.en.spec.ts` → FAIL
- [ ] **Step 3:** 动作清单 2–3（四文件；注意 `DashboardView.vue:246-247` 从 utils/dashboard 导入的函数无需改动——标签已在函数内翻译）
- [ ] **Step 4:** Run: `npx vitest run src/views/__tests__/DashboardView.spec.ts src/components/dashboard` → 全 PASS（`DashboardView.spec` ~93 行中文断言未改即绿）
- [ ] **Step 5:** 提交：`git add kb-ui/src/views/DashboardView.vue kb-ui/src/components/dashboard kb-ui/src/locales/zh/dashboard.ts kb-ui/src/locales/en/dashboard.ts`，message：`feat(kb-ui): 概览区双语——DashboardView/OpsPanel/KbCard/QueryList`

### Task 6: 检索记录区（`records.*`）

**Files:**
- Modify: `src/views/RetrievalRecordsView.vue`（4）、`src/components/retrieval/RetrievalRecordsAnalysis.vue`（29）、`RetrievalRecordsPanel.vue`（83）
- **不碰**: `components/retrieval/segmentTerms.ts`（语言数据）、`timeRange.ts`（Task 3 已完成）
- Dictionary: `zh/records.ts`、`en/records.ts`（已有 4 个 key，追加）
- Test（新建）: `src/components/retrieval/__tests__/RetrievalRecordsAnalysis.en.spec.ts`（样板：`RetrievalRecordsAnalysis.spec.ts`）

- [ ] **Step 1:** 动作清单 1
- [ ] **Step 2:** Run: `npx vitest run src/components/retrieval/__tests__/RetrievalRecordsAnalysis.en.spec.ts` → FAIL
- [ ] **Step 3:** 动作清单 2–3
- [ ] **Step 4:** Run: `npx vitest run src/views/__tests__/McpAccessRecordsLink.spec.ts src/api/__tests__/retrievalRecords.spec.ts src/components/retrieval` → 全 PASS（`RetrievalRecordsPanel.spec` ~73 行中文断言未改即绿）
- [ ] **Step 5:** 提交：`git add kb-ui/src/views/RetrievalRecordsView.vue kb-ui/src/components/retrieval kb-ui/src/locales/zh/records.ts kb-ui/src/locales/en/records.ts`，message：`feat(kb-ui): 检索记录区双语——列表/分析面板/自定义时间校验`

### Task 7: MCP 接入区（`mcp.*`）

**Files:**
- Modify: `src/views/McpAccessView.vue`（49）、`src/components/mcp/McpKeyConfigPanel.vue`（56）
- Dictionary: `zh/mcp.ts`、`en/mcp.ts`
- Test（新建）: `src/components/mcp/__tests__/McpKeyConfigPanel.en.spec.ts`（样板：`McpKeyConfigPanel.spec.ts`）

- [ ] **Step 1:** 动作清单 1
- [ ] **Step 2:** Run: `npx vitest run src/components/mcp/__tests__/McpKeyConfigPanel.en.spec.ts` → FAIL
- [ ] **Step 3:** 动作清单 2–3（`McpAccessView.vue:140` 的 `apiErrorDetail` 是 `managementErrorMessage` 别名，其文案 Task 3 已译，无需改）
- [ ] **Step 4:** Run: `npx vitest run src/views/__tests__/McpAccessDelete.spec.ts src/views/__tests__/McpAccessRecordsLink.spec.ts src/components/mcp` → 全 PASS（`McpKeyConfigPanel.spec` ~54 行中文断言未改即绿）
- [ ] **Step 5:** 提交：`git add kb-ui/src/views/McpAccessView.vue kb-ui/src/components/mcp kb-ui/src/locales/zh/mcp.ts kb-ui/src/locales/en/mcp.ts`，message：`feat(kb-ui): MCP接入区双语——接入页/钥匙配置面板`


---

## Chunk 3: 知识库区三簇 + 终验（Task 8–11）

> Task 8–10 同样照 Task 5 开头的"分区任务动作清单"五步执行。

### Task 8: 知识库视图（`kbView.*`，5 文件 / 清单 219 条）

**Files:**
- Modify: `src/views/kb/KbListView.vue`（39）、`KbDetailView.vue`（27）、`KbDocPreviewView.vue`（40）、`KbRunDetailView.vue`（60）、`KbRunDocDetailView.vue`（53）
- Dictionary: `zh/kbView.ts`、`en/kbView.ts`（Task 4 已有 4 个 key，追加）
- Test（新建）: `src/views/kb/__tests__/KbListView.en.spec.ts`（样板：`KbListViewPermissions.spec.ts`）

- [ ] **Step 1:** 动作清单 1（断言 KbListView 一条表头/按钮的 en 值）
- [ ] **Step 2:** Run: `npx vitest run src/views/kb/__tests__/KbListView.en.spec.ts` → FAIL
- [ ] **Step 3:** 动作清单 2–3（`KbRunDetailView.vue:281`、`KbRunDocDetailView.vue:336` 的 `miningOperatorLabel()` 已在 Task 3 内部翻译，调用处不动；页面里的 run 状态若直接写死中文而非走 `runStatusLabel`，照常入字典——不得顺手改调用结构）
- [ ] **Step 4:** Run: `npx vitest run src/views/kb` → 全 PASS（`KbRunDetailView.spec`/`KbDocPreviewView.spec` 等中文断言未改即绿）
- [ ] **Step 5:** 提交：`git add kb-ui/src/views/kb kb-ui/src/locales/zh/kbView.ts kb-ui/src/locales/en/kbView.ts`，message：`feat(kb-ui): 知识库视图双语——列表/详情/文档预览/run详情`

### Task 9: 知识库组件·检索质量簇（`kbSearch.*`，8 文件 / 清单 174 条）

**Files:**
- Modify: `src/components/kb/KbSearchPanel.vue`（39）、`KbSearchWorkspace.vue`（4）、`TbTableQueryPanel.vue`（34）、`OnenetRefPanel.vue`（22）、`KbQualityPanel.vue`（9）、`DocumentStructureGraph.vue`（41）、`PipelineFlow.vue`（14）、`PreprocessNotice.vue`（11）
- Dictionary: `zh/kbSearch.ts`、`en/kbSearch.ts`
- Test（新建）: `src/components/kb/__tests__/PreprocessNotice.en.spec.ts`（样板：`PreprocessNotice.spec.ts`）

- [ ] **Step 1:** 动作清单 1
- [ ] **Step 2:** Run: `npx vitest run src/components/kb/__tests__/PreprocessNotice.en.spec.ts` → FAIL
- [ ] **Step 3:** 动作清单 2–3（`DocumentStructureGraph.vue` 消费的 utils 标签已在 Task 3 翻译；组件自身的图例/提示文案照常入字典）
- [ ] **Step 4:** Run: `npx vitest run src/components/kb/__tests__/KbSearchPanel.spec.ts src/components/kb/__tests__/KbSearchPanelDirectory.spec.ts src/components/kb/__tests__/KbSearchPanelScope.spec.ts src/components/kb/__tests__/KbSearchWorkspace.spec.ts src/components/kb/__tests__/TbTableQueryPanel.spec.ts src/components/kb/__tests__/OnenetRefPanel.spec.ts src/components/kb/__tests__/KbQualityPanel.spec.ts src/components/kb/__tests__/DocumentStructureGraph.spec.ts src/components/kb/__tests__/PipelineFlow.spec.ts src/components/kb/__tests__/PreprocessNotice.spec.ts src/components/kb/__tests__/PreprocessNotice.en.spec.ts` → 全 PASS
- [ ] **Step 5:** 提交：`git add kb-ui/src/components/kb/KbSearchPanel.vue kb-ui/src/components/kb/KbSearchWorkspace.vue kb-ui/src/components/kb/TbTableQueryPanel.vue kb-ui/src/components/kb/OnenetRefPanel.vue kb-ui/src/components/kb/KbQualityPanel.vue kb-ui/src/components/kb/DocumentStructureGraph.vue kb-ui/src/components/kb/PipelineFlow.vue kb-ui/src/components/kb/PreprocessNotice.vue kb-ui/src/components/kb/__tests__/PreprocessNotice.en.spec.ts kb-ui/src/locales/zh/kbSearch.ts kb-ui/src/locales/en/kbSearch.ts`，message：`feat(kb-ui): 知识库组件双语·检索质量簇——检索/表格查询/一张网引用/质量/结构图/流水线`

### Task 10: 知识库组件·文件库管理簇（`kbManage.*`，8 文件 / 清单 229 条）

**Files:**
- Modify: `src/components/kb/KbFileManager.vue`（69）、`KbCreateDialog.vue`（15）、`KbExportDialog.vue`（50）、`KbCopyDialog.vue`（17）、`KbReplaceDocumentDialog.vue`（13）、`KbMembersPanel.vue`（15）、`KbSettingsPanel.vue`（25）、`KbMiningPanel.vue`（25）
- Dictionary: `zh/kbManage.ts`、`en/kbManage.ts`（Task 4 已有 1 个 key，追加）
- Test（新建）: `src/components/kb/__tests__/KbReplaceDocumentDialog.en.spec.ts`（样板：`KbReplaceDocumentDialog.spec.ts`）

- [ ] **Step 1:** 动作清单 1
- [ ] **Step 2:** Run: `npx vitest run src/components/kb/__tests__/KbReplaceDocumentDialog.en.spec.ts` → FAIL
- [ ] **Step 3:** 动作清单 2–3（`KbSettingsPanel.vue:152+` 的 `ElMessage.error(managementErrorMessage(...))` 文案已在 Task 3 翻译，调用处不动）
- [ ] **Step 4:** Run: `npx vitest run src/components/kb/__tests__/KbFileManager.spec.ts src/components/kb/__tests__/KbFileManagerReplace.spec.ts src/components/kb/__tests__/KbFileManagerSearch.spec.ts src/components/kb/__tests__/KbReplaceDocumentDialog.spec.ts src/components/kb/__tests__/KbReplaceDocumentDialog.en.spec.ts src/components/kb/__tests__/KbMiningPanelQueue.spec.ts` → 全 PASS
- [ ] **Step 5:** 提交：`git add kb-ui/src/components/kb/KbFileManager.vue kb-ui/src/components/kb/KbCreateDialog.vue kb-ui/src/components/kb/KbExportDialog.vue kb-ui/src/components/kb/KbCopyDialog.vue kb-ui/src/components/kb/KbReplaceDocumentDialog.vue kb-ui/src/components/kb/KbMembersPanel.vue kb-ui/src/components/kb/KbSettingsPanel.vue kb-ui/src/components/kb/KbMiningPanel.vue kb-ui/src/components/kb/__tests__/KbReplaceDocumentDialog.en.spec.ts kb-ui/src/locales/zh/kbManage.ts kb-ui/src/locales/en/kbManage.ts`，message：`feat(kb-ui): 知识库组件双语·文件库管理簇——文件管理/建库/导出/复制/替换/成员/设置/挖掘`

---

### Task 11: 终验——key 引用闸门 / 全量测试 / 构建 / 红线

**Files:**
- Test（新建）: `src/locales/__tests__/keyUsage.spec.ts`
- Modify（如有偏差）: `docs/superpowers/specs/2026-11-22-artifacts-gate-and-four-section-i18n-design.md`

- [ ] **Step 1: 写 key 引用闸门测试（防调用点 key 拼写错误与死 key）**

```typescript
// src/locales/__tests__/keyUsage.spec.ts
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { zh } from '../zh'

// src 目录（locales 与测试目录自身除外）：字典 key 必须以字符串字面量出现在这里
const SRC = fileURLToPath(new URL('../..', import.meta.url))

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) {
      if (name === 'locales' || name === '__tests__') return []
      return sourceFiles(full)
    }
    return /\.(vue|ts)$/.test(name) ? [full] : []
  })
}

describe('dictionary key usage', () => {
  it('every zh key is referenced by a string literal somewhere in src', () => {
    const corpus = sourceFiles(SRC).map(file => readFileSync(file, 'utf8')).join('\n')
    const unused = Object.keys(zh).filter(key => !corpus.includes(`'${key}'`))
    expect(unused, '未被引用的字典 key：调用点拼写错误（界面会直接显示 key）或死 key').toEqual([])
  })
})
```

Run: `npx vitest run src/locales/__tests__/keyUsage.spec.ts`
Expected: PASS。若报出未引用 key：逐个判定——调用点 key 拼写错误（修调用点）或确实多余（从 zh+en 字典同时删除）。

- [ ] **Step 2: 全量剩余中文扫描（R9 终闸）**

```bash
grep -rnP '[\x{4e00}-\x{9fa5}]' src/views/DashboardView.vue src/components/dashboard src/views/kb src/components/kb src/views/RetrievalRecordsView.vue src/components/retrieval src/views/McpAccessView.vue src/components/mcp src/components/common/EmptyState.vue src/components/charts/WordCloud.vue src/utils/runStatus.ts src/utils/dashboard.ts src/utils/documentStructureGraph.ts src/utils/miningWorkflowPresentation.ts src/utils/managementError.ts src/api/proxyClient.ts src/api/serving.ts src/api/kb.ts src/stores/mining.ts | grep -vP '^[^:]+:\d+:\s*(//|\*|/\*\*|<!--)' | grep -vP 'segmentTerms\.ts'
```

Expected: 剩余每一行都能归入 R2 不翻项（注释尾、console、匹配数据如 managementError 的中文键、模板里作为专有名词保留的内容）。发现漏翻 → 回对应分区补齐并单独提交。

- [ ] **Step 3: 全量单测**

Run: `npm test`
Expected: 111+ 文件全绿（较基线新增约 10 个 spec 文件）。高负载下若出现与本批无关的既有 flake（renderMarkdown/HelpView/WikiReaderPanel/ArtifactImportWizard 等），单独重跑确认后记录，不归咎本批。

- [ ] **Step 4: 类型验证 + 构建**

Run: `npm run build`
Expected: vue-tsc -b 零错 + vite build 成功（en 字典的 keyof 对齐在编译期强制）。

- [ ] **Step 5: 提交终验**

```bash
cd /d/mywork/AgenticKB && git add kb-ui/src/locales/__tests__/keyUsage.spec.ts && git commit -m "test(kb-ui): 字典key引用闸门——防调用点拼写错误与死key"
```

- [ ] **Step 6: 红线核查**

```bash
git diff --stat <pre-batch-sha>..HEAD -- . ':(exclude)kb-ui' ':(exclude)docs'
git diff --stat <pre-batch-sha>..HEAD -- kb-ui/package.json kb-ui/package-lock.json
```

Expected: 两条输出均为空（**后端零改动、零新依赖**）。

- [ ] **Step 7: spec 回写**

如有与 spec 的偏差（文件增删、口径补充），在 spec 对应小节补一行"实施期"说明并提交。

- [ ] **Step 8: 交接对抗审查**（计划外流程，用户既定惯例）

三路并行：①转换完整性（R9 终闸复核 + EN 态四区逐页走查漏翻）②回归安全（既有断言零放松抽查、匹配数据未被误翻、红线）③EN 质量（术语表一致性、占位符对应、信息量对等）。发现按严重度裁定修复后，重跑 Step 3–4。

---

## 风险与既知边界

1. **`t()` 助手依赖"渲染期调用"建立响应性**：顶层常量表里提前求值不随语言切换（R8）——EN 渲染用例只抽查每区一个组件，其余靠评审 Step 8 ①走查；若线上发现某处切语言不刷新，按 R8 改为存 key。
2. **清单可能漏跨行模板文本**：R9 终闸兜底。
3. **`documentStructureGraph.ts` 85–89 映射的消费形态未在计划期确认**：Task 3 Step 6 给了两种处理方式，执行者读文件择一。
4. **管理面泄漏**（spec 已裁定接受）：`managementError`/`miningWorkflowPresentation` 的 EN 文案会出现在设置页/挖掘范式编辑器。
5. **手输 URL 仍可达制品页**（spec 拍板：仅菜单隐藏）。
6. 分区任务的 EN 字典由执行者按术语表撰写——质量由 Step 8 ③ 专门审查。


