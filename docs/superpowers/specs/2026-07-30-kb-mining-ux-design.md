# KB 中心化挖掘 UX 重构 — 设计规格

- **日期**：2026-07-30
- **分支**：feat/kb-management
- **状态**：已通过设计评审，待用户复核 → 进入实现计划
- **范围**：`kb-ui`（前端）+ `knowledge_mining/mining/kb`（后端）

---

## 1. 背景与问题

上一轮把全局「挖掘管理」页面删除、把挖掘收进知识库详情页后，暴露 4 个问题：

1. **丢失实时进度与流水线可视化**：`KbMiningPanel` 只用纯文本展示 `current_stage`，3s 轮询仅刷新记录行，记录行不可点；旧的 12 阶段 `PipelineFlow` 可视化、双色进度条、可点任务详情全部丢失。
2. **右上角「挖掘」按钮多余且有 bug**：与挖掘 Tab 重复；且即使已在挖掘 Tab 选好范式，点击仍提示「未选择挖掘范式」。
3. **文档预览状态矛盾且看不到挖掘知识**：预览页同时显示「未挖掘」与「已挖掘」两个 tag；`/knowledge` 接口不返回「关系」，切片/检索单元/实体/关系等挖掘产物看不到；不同范式产出不同资产，前端无感知。
4. **挖掘范围交互冗余**：`KbMiningPanel` 内另起一张勾选表做选择性挖掘，而文件列表 `KbFileManager` 本身不支持多选、无批量操作。

### 1.1 Bug 根因（已定位）

- **Bug A（按钮误报未选范式）**：`KbDetailView.triggerMine()` 读 `kb.value.mining_workflow_id`（来自 `listKbs` 列表快照，仅 mount/domain/kbId 变/设置 Tab 保存时刷新）；范式选择器在 `KbMiningPanel` 内直接 `updateKb` 写后端 + 更新本地 ref，**不回传父组件**。两条状态链割裂 → 父按钮永远读到旧 null。
- **Bug B（双状态同屏）**：预览页两个 tag 来自两个独立源——`doc.status`（`getDocument`，后端派生态）与 `knowledgeMined`（`getDocumentKnowledge` 的 `mined` 字段，且该请求 `.catch(()=>null)` 静默降级为 false）。两源互不收敛，故出现 `status=mined` 配 `knowledgeMined=false`（或反之）的矛盾并排。

### 1.2 现有能力（已确认后端支持，前端未接）

- `POST /api/kb/{kb_id}/mine` 请求体 `{document_ids?: [...]}`：空=整库增量，非空=选择性挖掘。返回 `run_id`。**选择性挖掘后端已具备**。
- `GET /api/kb/{kb_id}/runs`：任务列表，含 `status/current_stage/计数`。
- 全局 `GET /api/runs/{run_id}/progress|stages|trace|documents`：进度百分比、阶段事件、文档结果（KB 创建的 run 同样可达）。
- `GET /api/kb/{kb_id}/documents/{doc_id}/knowledge`：经「KB 最新 validated/published build → snapshot」返回切片/检索单元/实体提及。**不含关系**。
- 范式 = 已发布 `mining_workflow`；`GET /api/mining-workflows/options`；6+1 种子范式，差异体现在 workflow graph 勾选的算子，决定填充哪些资产表。
- 旧挖掘管理三件套（`RunsView`/`RunDetailView`/`RunDocumentDetailView`/`PipelineFlow`）在 commit `bbe8ea7` 可取回。

---

## 2. 目标与非目标

### 目标
1. 挖掘触发统一到**文件列表多选 + 批量操作**；挖掘 Tab 只做「任务列表 + 实时状态 + 可点详情」。
2. 任务详情页恢复**实时进度 + 12 阶段流水线可视化 + 文档结果**（KB 内子路由）。
3. 文档预览页：单一权威状态；按范式/数据动态出知识 Tab；含切片/检索单元/实体/**关系**。
4. 修掉 Bug A、Bug B；删掉多余的右上角挖掘按钮。

### 非目标（本轮不做）
- 人审流程（`/candidates/review`、`/mentions/review`）的深度整合——仅在任务详情页保留横幅与跳转入口，不重建独立页面。
- 范式编辑器、本体评审页面。
- SSE/WebSocket 实时推送——沿用 3s 轮询（后端无推送）。

---

## 3. 总体架构

```
知识库详情 /kb/:kbId
├─ 文件 Tab（KbFileManager）       【改】+ 多选 + 批量栏（挖掘/删除/移动）
│     └─ 点文件 → /kb/:kbId/doc/:docId            （预览页·改）
├─ 挖掘 Tab（KbMiningPanel）       【重构】范式选择器(顶部) + 任务列表(轮询) + 行可点
│     └─ 点任务 → /kb/:kbId/run/:runId            （任务详情·新）
│                  └─ 点文档 → /kb/:kbId/run/:runId/doc/:docId（任务内文档详情·新）
├─ 成员 Tab（不动）
└─ 设置 Tab（不动）
右上角「挖掘」按钮                   【删】
```

**数据流**：文件列表多选 → `POST /api/kb/{id}/mine {document_ids}` → 生成 `mining_run(kb_id)` → 挖掘 Tab 轮询 `GET /api/kb/{id}/runs` → 点任务进详情页 → 详情页轮询全局 `GET /api/runs/{run_id}/progress|stages|trace` → 文档预览读扩展后的 `GET /api/kb/{id}/documents/{doc}/knowledge`。

**资产挂载模型（不变）**：文档资产挂在 `document_snapshot`（经 build 关联），不直接挂文档。`get_document_knowledge` 永远读「KB 最新 validated/published build」对应 snapshot。切换范式重挖后自动指向新 build 的 snapshot，旧 snapshot 保留。

---

## 4. 前端设计

### 4.1 路由（`kb-ui/src/router/index.ts`）

新增：
- `kb-run-detail` → `/kb/:kbId/run/:runId` → `KbRunDetailView.vue`（props: kbId, runId）
- `kb-run-doc-detail` → `/kb/:kbId/run/:runId/doc/:docId` → `KbRunDocDetailView.vue`（props: kbId, runId, docId）

现有不动：`kb`、`kb-detail`、`kb-doc-preview`。

### 4.2 组件改动

#### `KbDetailView.vue`
- **删除右上角「挖掘」按钮**及其 `triggerMine`。
- **范式状态提升到父组件**：父持有 `miningWorkflowId`（初值来自 `getKb`），通过 `v-model:selectedWorkflowId` 下传 `KbMiningPanel`；子组件选择后 `emit('update:selectedWorkflowId', v)`，父更新本地 ref（单一真相源，修 Bug A）。
- 父组件 `kb` 快照改用 `getKb(kbId)` 单查（替代 `listKbs().find()`），范式变更后局部更新 `kb.mining_workflow_id`，避免整表刷新。

#### `KbFileManager.vue`（文件 Tab）
- 新增**多选模式**：每行复选框 + 表头全选；`selectedIds = ref<string[]>([])`。
- 选中 N>0 时浮出**批量操作栏**：
  - `挖掘选中(N)` → 校验 `kb.mining_workflow_id` 已设（未设则 `ElMessage.warning` 引导去挖掘 Tab）→ `kbApi.mineKb(kbId, selectedIds)` → 跳挖掘 Tab 并刷新。
  - `删除` → 二次确认 → 逐个 `deleteDocument`（或后端补批量删除接口，见 §5）。
  - `移动到文件夹` → 复用现有 `moveDocument` 循环。
- 单文件右键菜单（下载/重命名/删除/移动）保留。

#### `KbMiningPanel.vue`（挖掘 Tab，重构）
- **删除「挖掘范围」勾选表与自带「挖掘选中/整库挖掘」按钮**（触发移交给文件列表）。
- **保留范式选择器**（顶部，`v-model:selectedWorkflowId` 接父组件）。
- 任务列表（`el-table`）列：状态徽章 / **双色进度条**（committed=绿 / failed=红 / total；`committed_count` 由后端 `list_kb_runs` 内联，见 §5.2，避免 N+1 轮询）/ `current_stage` / 文档计数(新增·更新·跳过·失败) / 范式版本 / 开始时间 / 耗时。
- **行可点**：`@row-click` → `router.push({ name:'kb-run-detail', params:{kbId, runId: row.id} })`。
- 轮询：存在 `queued`/`running` 任务时每 3s 调 `getKbRuns`；全终态停止。生命周期：挂载即拉一次，running 才 arm，终态/卸载/切域必清 timer。
- 顶部「刷新」按钮保留。

#### `KbDocPreviewView.vue`（文档预览，改）
- **修 Bug B**：header 仅渲染一个状态 tag，来源 `doc.status`（单一权威）。移除独立的「已挖掘/未挖掘」第二 tag。`knowledgeMined` 不再出 tag，仅用于决定是否渲染知识 Tab；`getDocumentKnowledge` 失败时回退用 `doc.status === 'mined'` 推断。
- **知识 Tab 按数据动态出**：Tab 出现当且仅当对应资产数组非空。Tab 顺序：原始预览（恒在）→ 切片分段 → 检索单元 → 实体提及 → 关系图谱。
- **未挖掘文档**（`doc.status !== 'mined'`）：只显示「原始预览」Tab，不请求 `/knowledge`。
- 消费扩展后的 `DocumentKnowledge`（含 `relations`）。

#### `KbRunDetailView.vue`（新，移植自 `bbe8ea7` 的 `RunDetailView`）
- 区块：Meta 卡（Run ID/状态/6 指标）→ 错误横幅 → 进度总览卡（双色进度条+5 stat）→ **`<PipelineFlow>`**（阶段数以组件内硬编码为准）+ 本体线产出统计 → 文档处理结果表（行点击 → `kb-run-doc-detail`）。
- 轮询：挂载 `pollOnce`（非静默）→ status=running 则 `setInterval(pollOnce, 3000)`（静默）→ 非_running 清 timer。并发拉 `progress` + `runDetail` + `trace`。卸载/切域必清。
- 数据源：复用 `@/api/mining` 的全局 run 接口（`getRun/getRunProgress/getRunTrace/getRunDocuments/getRunStages`）。若该 api 文件已删，从 `bbe8ea7` 恢复（见 §4.4）。

#### `KbRunDocDetailView.vue`（新，移植自 `bbe8ea7` 的 `RunDocumentDetailView`）
- 4 Tab：原始分段 / 检索单元 / 关系图谱 / 原始文本（markdown→HTML+DOMPurify / HTML / 纯文本）。各 50/页。
- 非Tab：Info 卡 / 错误横幅 / 跳过原因横幅 / 阶段时间线（垂直）/ 产物摘要三数字。
- 数据源：`@/api/mining` 的 `getRunDocumentSegments/Units/Relations/RawContent/Stages/Artifacts`。
- 注：这里的「关系」是**任务内(run-scoped)**视图（`getRunDocumentRelations`），与文档预览页的**KB 知识(knowledge-scoped)**关系（§5.1 扩展的 `/knowledge`）是两个独立数据面，勿混。

#### `PipelineFlow.vue`（恢复到 `components/kb/`）
- 从 `bbe8ea7` 原样恢复。Props：`stageEvents`、`allDocsSettled`。阶段硬编码（具体阶段数以组件为准）+ 本体线/篇章线归属 + 状态着色 + 呼吸动画 + 孤儿 started 容错（`allDocsSettled` 豁免逐文档阶段，全局尾段不豁免）。

### 4.3 类型（`kb-ui/src/types/kb.ts`）
- `DocumentKnowledge` 增 `relations: KbDocRelation[]`。
- 新增 `KbDocRelation`：`{ source_segment_text, target_segment_text, relation_type, weight, confidence }`。
- 实体提及：复用现有 `canonical_name` 字段（`asset_segment_entity_mentions` 已有此列，`get_document_knowledge` 已 select，前端已类型化渲染），**无需新增字段**。

### 4.4 移植前提（已核实）
- **仍在，直接复用**：`kb-ui/src/api/mining.ts`（含 §4.2 所需全部 run 接口：`getRun/getRunProgress/getRunTrace/getRunDocuments/getRunStages/getRunDocumentSegments|Units|Relations|RawContent|Artifacts`）、`stores/mining.ts`、`api/miningWorkflow.ts`（当前 `KbMiningPanel.vue` 已在用）。
- **需从 `bbe8ea7` 恢复**：`components/StatusBadge.vue`、`components/mining/PipelineFlow.vue`（恢复后迁到 `components/kb/`）、旧 `RunDetailView.vue`/`RunDocumentDetailView.vue`（作为 `KbRunDetailView`/`KbRunDocDetailView` 移植来源）。

---

## 5. 后端设计

### 5.1 扩展 `get_document_knowledge`（`knowledge_mining/mining/kb/db.py:240-294`）
当前返回 `{mined, build_id, document_snapshot_id, segments, retrieval_units, entity_mentions}`。增补：
- **`relations`**：查 `asset_raw_segment_relations`（按 `document_snapshot_id`），返回源段/目标段文本（join `asset_raw_segments` 取 `raw_text`，截断）、`relation_type`、`weight`、`confidence`。
- 空数组兜底：任一类资产无数据返回 `[]`（而非缺键），便于前端「按数据动态出 Tab」。

### 5.2 `list_kb_runs` 内联 `committed_count`（`knowledge_mining/mining/kb/db.py:224-238`）
挖掘 Tab 任务列表的双色进度条需要 `committed_count`（当前 `KbRunRecord` 只有 total/new/updated/skipped/failed，无 committed）。为避免列表每行额外轮询全局 `getRunProgress`（N+1），在 `list_kb_runs` 的 SQL 里把 `committed_count` 一并 select 返回。进度条口径：绿=committed/total、红=failed/total。详细阶段进度仍走任务详情页的全局 `getRunProgress`。

### 5.3 「看不到知识」根因联调（HIGH，实现时第一个验）
`get_document_knowledge` 只读 `status in (validated, published)` 的 build。KB 挖掘 `publish=False`（只 build 不进域级 active release）。
- **待验证**：KB 挖掘的 build 是否到达 `validated`？若 workflow 在 `publish=False` 时短路了 `validate_build`/只到 `assembled`，则知识读不到——这正是点 3「看不到知识」的可疑真因。
- **修法（若成立）**：保证 KB 挖掘流程跑完 `validate_build` 使 build 进入 `validated`（不依赖 publish）。具体看 `mining/kb/routes/mining.py` 的 workflow 提交参数与 build 状态机。

### 5.4 批量删除（可选）
`KbFileManager` 批量删除若逐个调 `deleteDocument` 在 N 大时慢且部分失败难处理。评估是否补 `DELETE /api/kb/{kb_id}/documents`（body `{document_ids}`）批量接口。本轮可先逐个调，接口作为后续优化。

### 5.5 不变项
- `mine` 接口、`runs` 列表接口、全局 `/api/runs/*` 接口均不动。
- 资产表 schema 不动。

---

## 6. Bug 修复总结

| Bug | 根因 | 修法 |
|---|---|---|
| A 按钮误报未选范式 | 范式选择在子组件直写后端不回传父；父读列表快照旧值 | 范式状态提升到 `KbDetailView`（`v-model` 下传，子 emit 上报）；父改用 `getKb` 单查；**删多余按钮** |
| B 双状态同屏 | `doc.status` 与 `knowledgeMined` 双源不收敛，后者静默降级 | 单一权威 = `doc.status` 出 tag；`knowledgeMined` 仅控 Tab 渲染，失败回退 `status==='mined'` |

---

## 7. 测试策略

### 后端（`knowledge_mining/tests/kb/`）
- 扩 `test_documents.py`：`get_document_knowledge` 覆盖 relations 返回、实体 resolved_name、空数组兜底。
- 保持 kb 套件绿（基线 49 passed）。约束：`_test` 库（`PG_DBNAME` 覆盖）、`KB_ALLOW_TEST_TRUNCATE=1`、anaconda python + Library/bin PATH。

### 前端单测（`kb-ui`，Vitest）
- `KbFileManager`：多选切换、批量栏显隐、批量挖掘未设范式时的拦截。
- `KbMiningPanel`：轮询 arm/清时机、行点击跳转。
- `KbDocPreviewView`：单 tag 渲染、未挖掘只出预览 Tab、按数据动态出知识 Tab、`/knowledge` 失败回退。
- `KbRunDetailView`：轮询生命周期（移植旧 `RunDetailView.spec.ts` 契约：running 才 arm、3s 周期、终态即停、卸载必清）。
- `PipelineFlow`：阶段状态判定（completed/running/failed/pending）、孤儿 started 容错。

### E2E（前后端联调主线，用户点名的验证方式）
上传文件 → 文件列表多选 → 挖掘 Tab 选范式 → 批量「挖掘选中」→ 任务列表出现新 run（实时状态推进）→ 点进任务详情看 12 阶段流水线 + 双色进度条实时更新 → 回文件预览页看按范式动态出的知识 Tab（切片/检索单元/实体/关系）→ 切换范式重挖 → 预览页 Tab 随范式变化。

---

## 8. 关键风险与联调验证项

1. **「看不到知识」真因**（HIGH）：KB 挖掘 build 是否到 `validated`——点 3 能否成立的前提。实现第一步用命令直连 PG 查 `asset_builds.status` 与 `mining_runs` 关联验证。
2. **全局 `/api/runs/*` 对 KB run 可达性**：确认 KB 创建的 run 能被全局接口查到（run_id 全局唯一，应可行；实测确认）。
3. **旧依赖存活**（`@/api/mining` 等）：决定移植是「复用」还是「恢复」。
4. **范式感知口径**：Tab 用「数据非空」而非「解析 workflow graph」实现范式感知——更准（反映该文档实际产出）、更简。需与用户预期对齐：切范式后旧 build 的资产仍可读（snapshot 保留），新 build 产出覆盖最新展示。

---

## 9. 实现顺序（摘要，详见后续 writing-plans）

1. **联调验证**：先验 §8.1（build 状态）与 §8.2（全局 run 接口），用命令打通数据链路。
2. **后端**：扩 `get_document_knowledge`（含 relations）+ 测试。
3. **移植**：核查/恢复旧依赖；恢复 `PipelineFlow`；移植 `KbRunDetailView`/`KbRunDocDetailView` 到 KB 路由。
4. **前端改造**：`KbDetailView`（删按钮+范式提升）、`KbFileManager`（多选+批量）、`KbMiningPanel`（重构）、`KbDocPreviewView`（修Bug B+动态Tab）、类型。
5. **测试**：单测 + E2E 联调主线。
6. **清理**：移除移植过程中产生的死代码/旧 import。

---

## 10. 决策记录

- **任务详情载体**：KB 内子路由页（非抽屉/非行内展开）— 空间足、可深链、最接近旧体验。
- **知识资产范围**：扩 `/knowledge` 返回关系+实体（单一接口、预览页自洽）。
- **Tab 范式感知**：按范式动态出 Tab，用「数据非空」实现。
- **范式选择器**：留挖掘 Tab 顶部，只修 bug 不挪窝。
- **右上角挖掘按钮**：删除。
