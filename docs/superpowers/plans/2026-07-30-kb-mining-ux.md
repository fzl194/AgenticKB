# KB 中心化挖掘 UX 重构 — 实现计划

> **For agentic workers:** 用 superpowers:executing-plans 实现。步骤用 `- [ ]` 跟踪。

**Goal:** 把挖掘触发移到文件列表多选、挖掘 Tab 还原实时任务流+12阶段流水线可视化、文档预览按范式动态出知识 Tab（含关系），修掉范式按钮与双状态两个 bug。

**Architecture:** 见 spec `docs/superpowers/specs/2026-07-30-kb-mining-ux-design.md`。4 个交互面：文件列表(多选+批量) / 挖掘 Tab(任务流+轮询) / 任务详情子路由(移植旧 RunDetailView+PipelineFlow) / 文档预览(单状态+动态Tab)。后端两处扩展：`list_kb_runs` 内联 committed_count、`get_document_knowledge` 增 relations。

**Tech Stack:** Vue3 + Element Plus + Vitest（前端）；FastAPI + psycopg + PostgreSQL（后端）；旧组件源 commit `bbe8ea7`。

---

## 文件结构

**后端（knowledge_mining）**
- Modify `mining/kb/db.py` — `list_kb_runs` 加 committed_count；`get_document_knowledge` 加 relations
- Modify `mining/kb/routes/mining.py` — （若 T3 成立）保证 KB 挖掘 build 到 validated
- Test `tests/kb/test_kb_db.py` — committed_count 断言
- Test `tests/kb/test_documents.py` — knowledge relations 断言

**前端（kb-ui）**
- Modify `src/types/kb.ts` — DocumentKnowledge.relations、KbDocRelation、KbRunRecord.committed_count
- Restore from `bbe8ea7` → `src/components/kb/PipelineFlow.vue`、`src/components/kb/StatusBadge.vue`（或复用现有）
- Create `src/views/kb/KbRunDetailView.vue`（移植 RunDetailView）
- Create `src/views/kb/KbRunDocDetailView.vue`（移植 RunDocumentDetailView）
- Modify `src/router/index.ts` — 加两条 run 路由
- Modify `src/views/kb/KbDetailView.vue` — 删按钮 + 范式状态提升
- Modify `src/components/kb/KbMiningPanel.vue` — 重构为任务流
- Modify `src/components/kb/KbFileManager.vue` — 多选 + 批量栏
- Modify `src/views/kb/KbDocPreviewView.vue` — 修 Bug B + 动态 Tab + relations
- Modify `src/api/kb.ts` — 类型对齐（getKbRuns 字段）

---

## Chunk 1: 后端扩展（独立可测）

### Task 1: `list_kb_runs` 内联 committed_count

**Files:**
- Modify: `knowledge_mining/mining/kb/db.py` (`list_kb_runs`, 约 224-238 行)
- Test: `knowledge_mining/tests/kb/test_kb_db.py`

- [ ] **Step 1: 写失败测试** — 在 test_kb_db.py 加测试，断言 `list_kb_runs` 返回的每条 run 含 `committed_count` 键。
- [ ] **Step 2: 跑测试确认失败** — `python -m pytest knowledge_mining/tests/kb/test_kb_db.py -k committed -v` → KeyError。
- [ ] **Step 3: 实现** — `list_kb_runs` SQL 的 SELECT 列加 `committed_count`，行 dict 补该键。
- [ ] **Step 4: 跑测试确认通过**。
- [ ] **Step 5: 同步前端类型** — `types/kb.ts` 的 `KbRunRecord` 加 `committed_count: number`。
- [ ] **Step 6: 提交** — `feat(kb): list_kb_runs 内联 committed_count 支持任务列表进度条`

### Task 2: `get_document_knowledge` 增 relations

**Files:**
- Modify: `knowledge_mining/mining/kb/db.py` (`get_document_knowledge`, 约 240-294 行)
- Test: `knowledge_mining/tests/kb/test_documents.py`

- [ ] **Step 1: 写失败测试** — 断言 knowledge 返回含 `relations` 数组，每项有 `relation_type/source_segment_text/target_segment_text/weight/confidence`；空数据返回 `[]`。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现** — 在取 segments/units/mentions 后，按 `document_snapshot_id` 查 `asset_raw_segment_relations`，join `asset_raw_segments`（源/目标）取截断 `raw_text`，组装 relations；无数据 `[]`。返回 dict 加 `relations` 键。
- [ ] **Step 4: 跑测试确认通过**。
- [ ] **Step 5: 同步前端类型** — `types/kb.ts` 加 `KbDocRelation`，`DocumentKnowledge.relations: KbDocRelation[]`。
- [ ] **Step 6: 提交** — `feat(kb): get_document_knowledge 返回关系资产`

### Task 3: 调查并修「KB 挖掘 build 未到 validated」

**Files:**
- Investigate: `knowledge_mining/mining/kb/routes/mining.py`、workflow build 阶段、`asset_builds.status` 状态机
- Verify: 直连 PG 查 `SELECT id, status, kb_id FROM asset_builds WHERE kb_id=<kb> ORDER BY created_at DESC LIMIT 5;`

- [ ] **Step 1: 直连 PG 查现有 KB build 的 status** — 确认是否卡在 assembled/validated。
- [ ] **Step 2: 读 mining.py 的 workflow 提交参数 + workflow build/validate/publish 阶段** — 定位 `publish=False` 是否短路了 `validate_build`。
- [ ] **Step 3: 若确认未到 validated** — 调整提交参数或 build 状态机，保证 KB 挖掘跑完 `validate_build` 使 build 进 validated（不依赖 publish）。
- [ ] **Step 4: 联调验证** — 重新触发一次 KB 挖掘，查 build status=validated，且 `/knowledge` 返回 `mined:true`。
- [ ] **Step 5: 提交**（若有改动）— `fix(kb): 保证 KB 挖掘 build 进入 validated 使知识可读`

---

## Chunk 2: 移植旧组件 + 路由

### Task 4: 恢复 PipelineFlow + StatusBadge

- [ ] **Step 1: 确认 StatusBadge 是否已存在** — `ls kb-ui/src/components/StatusBadge.vue`；不在则 `git checkout bbe8ea7 -- kb-ui/src/components/StatusBadge.vue`。
- [ ] **Step 2: 恢复 PipelineFlow** — `git show bbe8ea7:kb-ui/src/components/mining/PipelineFlow.vue > kb-ui/src/components/kb/PipelineFlow.vue`（迁到 kb 目录）。
- [ ] **Step 3: 检查 import 路径** — PipelineFlow 内部若 import `@/components/StatusBadge` 等保持不动；修正迁目录后的相对引用。
- [ ] **Step 4: build 冒烟** — `npm run build`（或 dev 起）确认无 import 报错。
- [ ] **Step 5: 提交** — `feat(kb-ui): 恢复 PipelineFlow/StatusBadge 到 components/kb`

### Task 5: 移植 KbRunDetailView

- [ ] **Step 1: 取旧文件** — `git show bbe8ea7:kb-ui/src/views/mining/RunDetailView.vue > kb-ui/src/views/kb/KbRunDetailView.vue`。
- [ ] **Step 2: 改 props 与路由** — props 改 `{ kbId: string; runId: string }`；所有 `runId` 来源改为 props；去掉全局 `/mining/:runId` 跳转，改 KB 内跳转。
- [ ] **Step 3: 数据源核对** — 沿用 `@/api/mining` 的 `getRun/getRunProgress/getRunTrace/getRunDocuments/getRunStages`（仍在）；`<PipelineFlow>` import 改 `@/components/kb/PipelineFlow.vue`。
- [ ] **Step 4: 文档结果表行点击** — 改跳 `kb-run-doc-detail`。
- [ ] **Step 5: 返回按钮** — 回 `/kb/:kbId`（挖掘 Tab）。
- [ ] **Step 6: 提交** — `feat(kb-ui): 移植 KbRunDetailView（任务详情+流水线）`

### Task 6: 移植 KbRunDocDetailView

- [ ] **Step 1: 取旧文件** — `git show bbe8ea7:kb-ui/src/views/mining/RunDocumentDetailView.vue > kb-ui/src/views/kb/KbRunDocDetailView.vue`。
- [ ] **Step 2: 改 props** — `{ kbId, runId, docId }`；数据源沿用 `getRunDocumentSegments/Units/Relations/RawContent/Stages/Artifacts`。
- [ ] **Step 3: 返回按钮** — 回 `kb-run-detail`。
- [ ] **Step 4: 提交** — `feat(kb-ui): 移植 KbRunDocDetailView（任务内文档资产）`

### Task 7: 路由

- [ ] **Step 1: 加路由** — `router/index.ts` 加 `kb-run-detail`（`/kb/:kbId/run/:runId`）和 `kb-run-doc-detail`（`/kb/:kbId/run/:runId/doc/:docId`），`props:true`。
- [ ] **Step 2: build 冒烟**。
- [ ] **Step 3: 提交** — `feat(kb-ui): 加 KB 内任务详情路由`

---

## Chunk 3: 前端交互改造

### Task 8: KbDetailView 删按钮 + 范式状态提升

- [ ] **Step 1: 删右上角「挖掘」按钮** 及 `triggerMine`。
- [ ] **Step 2: 范式状态提升** — 父持 `miningWorkflowId` ref（初值 `kb.mining_workflow_id`），`<KbMiningPanel v-model:selectedWorkflowId="miningWorkflowId">`；子 emit `update:selectedWorkflowId`。
- [ ] **Step 3: 父 kb 快照** — `reload` 改用 `getKb(kbId)`；范式变更后局部更新 `kb.mining_workflow_id = miningWorkflowId`。
- [ ] **Step 4: build 冒烟**。
- [ ] **Step 5: 提交** — `fix(kb-ui): 删多余挖掘按钮，范式状态提升修 Bug A`

### Task 9: KbMiningPanel 重构

- [ ] **Step 1: 删「挖掘范围」表 + 自带触发按钮**。
- [ ] **Step 2: 范式选择器改 v-model** — `props.selectedWorkflowId` + `emit('update:selectedWorkflowId')`，去掉本地独占 ref。
- [ ] **Step 3: 任务列表升级** — 列加双色进度条（committed=绿/failed=红/total）、状态徽章、current_stage；`@row-click` 跳 `kb-run-detail`。
- [ ] **Step 4: 轮询保留** — 3s，running 才 arm，终态/卸载清。
- [ ] **Step 5: build 冒烟**。
- [ ] **Step 6: 提交** — `refactor(kb-ui): KbMiningPanel 改为任务流+可点详情`

### Task 10: KbFileManager 多选 + 批量栏

- [ ] **Step 1: 多选** — 每行复选 + 全选；`selectedIds = ref<string[]>([])`。
- [ ] **Step 2: 批量栏** — N>0 浮出：`挖掘选中(N)` / `删除` / `移动到文件夹`。
- [ ] **Step 3: 批量挖掘** — 校验范式已设 → `mineKb(kbId, selectedIds)` → 跳挖掘 Tab 刷新；未设范式提示去挖掘 Tab。
- [ ] **Step 4: 批量删除/移动** — 二次确认后逐个 `deleteDocument`/`moveDocument`。
- [ ] **Step 5: build 冒烟**。
- [ ] **Step 6: 提交** — `feat(kb-ui): KbFileManager 多选+批量挖掘/删除/移动`

### Task 11: KbDocPreviewView 修 Bug B + 动态 Tab + relations

- [ ] **Step 1: 修 Bug B** — header 单 tag（`doc.status`）；删第二 tag；`knowledgeMined` 仅控 Tab 渲染，失败回退 `status==='mined'`。
- [ ] **Step 2: 动态 Tab** — 原始预览恒在；切片/检索单元/实体/关系 Tab 当且仅当对应数组非空才出现。
- [ ] **Step 3: 未挖掘只出预览** — `status!=='mined'` 不请求 `/knowledge`。
- [ ] **Step 4: 加关系 Tab** — `el-table` 展示 source/target 文本、relation_type、weight、confidence。
- [ ] **Step 5: build 冒烟**。
- [ ] **Step 6: 提交** — `fix(kb-ui): 文档预览单状态+动态知识Tab+关系`

---

## Chunk 4: 测试、构建、清理

### Task 12: 后端测试套件
- [ ] `python -m pytest knowledge_mining/tests/kb/ -v` 全绿（_test 库 + KB_ALLOW_TEST_TRUNCATE=1 + anaconda PATH）。

### Task 13: 前端构建 + 关键单测
- [ ] `npm run build` 绿。
- [ ] 关键单测：KbDocPreviewView 单状态/动态 Tab、KbMiningPanel 轮询+行点击、PipelineFlow 阶段判定（移植旧 spec）。Vitest 环境若 fork-timeout 则读测例逻辑确认。

### Task 14: 清理
- [ ] grep 旧 import / 死代码（`/mining/` 路由残留、未用组件），清理。

---

## Chunk 5: 联调验证（用户看效果）

### Task 15: 前后端联调
- [ ] 用户按序重启：main_control → mining → (serving 重建) → 前端 dev。
- [ ] 点击流：上传 → 文件列表多选 → 选范式 → 批量挖掘 → 挖掘 Tab 看任务实时推进 → 点任务看 12 阶段流水线 → 回预览页看按范式动态出的知识 Tab（含关系）→ 切范式重挖看变化。
- [ ] 诚实记录任何残留问题。

---
