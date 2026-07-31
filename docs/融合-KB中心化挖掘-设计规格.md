# 融合设计规格：KB 中心化挖掘（本次工作范围）

> 把"挖掘算子化"与"知识库管理"在**功能上融合**到 KB 中心形态。前端 + 后端（挖掘侧 Python）。**检索（Java serving）不在本次。**
> 关联：`docs/融合-数据形态与流转模型.md`（全表数据模型）、`docs/融合-挖掘算子化×知识库管理-现状与决策.md`（早期决策；其中 B3 落点已过时，见下文更正）。

---

## 1. 背景与目标

合并后两个特性正交：挖掘算子化（全局 workflow、无 kb、`/api/runs` 走 workflow）与知识库管理（KB 维度、`/api/kb/{id}/mine` 走 legacy）。本次融合成：**KB 是中心——KB 选范式、KB 内触发挖掘、记录在 KB 内、文件详情看多 tab 知识**。

**成功标准**：用户在一个 KB 内闭环完成"选挖掘范式 → 触发挖掘（选文件/整库增量）→ 看挖掘记录 → 点文件看原始预览 + 知识 tabs"，全程不离开 KB 上下文；挖掘走 workflow 引擎、产物按 KB 归属、**不破坏同域其他 KB 的发布状态**；检索行为不变。

## 2. 需求（对齐）

1. DB 满足已定数据形态 + 合并代码功能融合。
2. 挖掘收进 KB：KB 内触发 + 记录在 KB 内。
3. 砍顶层"知识资产"页；挖过的文件 → 多 tab（原始预览 + 知识 tabs）。
4. 前端"知识图谱"（实为篇章关系）不展示；本体/实体保持 domain 现状，per-KB 后续专项优化。
5. 检索（Java serving）本次不动。

## 3. 范围

**IN**
- 前端：KB 中心化（挖掘进 KB 详情 tab、文件详情多 tab、砍顶层资产/图谱两页）。
- 后端（Python 挖掘侧）：`mine_kb` 走 workflow + 范式绑定 + **build 但不 publish**；`mining_runs.kb_id` 列；KB 挖掘记录 API；文档知识 tabs 数据 API。
- 数据层：`knowledge_bases.mining_workflow_id`、`mining_runs.kb_id`、`asset_builds.kb_id`。

**OUT（deferred，含理由，见 §6）**
- Java serving 检索 kb_ids 接入（scope_resolve / resolveActiveScope / RunArgs / 结果对象 / 语义缓存 key）。
- per-KB release（`asset_publish_releases.kb_id` + active 约束改造）+ B1 的彻底（per-KB release）解法。
- 本体/实体 per-KB（`ontology_*` 加 kb_id + 归一管线改 scope）。
- 实体图谱/本体前端页重构（保留现状）。

## 4. 决策记录

**重大（用户拍板）**
- 本体/实体保持 domain 现状；per-KB 后续专项优化。
- 检索整体延后。
- 前端"知识图谱"（篇章关系）页砍掉。

**次要（我自决，附依据）**

| 决策点 | 选择 | 依据 |
|---|---|---|
| 发布层 | **P2'**：本次 KB 挖掘走 **"build 但不 publish"** 模式；只加 `asset_builds.kb_id`，**不碰 release 表** | ⚠️ 代码事实：`_finalize_run`（`jobs/run.py:1970-2009`）`if not phase1_only` 块**无条件** `assemble_build` + `publish_release`。`mine_kb` 现在不传抑制参数 → KB 挖掘会 auto-publish 到域级 active release → **B1 必触发**。故必须**主动抑制 publish**（不能靠"默认不发"）。检索不做→release 无消费方；build 仍要产（文件 tabs 靠它定位当前 snapshot）；release+B1 根治+serving kb_ids 打包留检索那期 |
| 抑制 publish 的机制 | 给 `mining_run()` / `_finalize_run()` 加 `publish: bool = True` 参数；`mine_kb` 传 `publish=False`；`should_publish = publish and (not has_failures or publish_on_partial_failure)` | 不能用 `phase1_only=True`——它会跳过整个 `if not phase1_only` 块、**连 build 都不产**（文件 tabs 失效）。新参数只关 publish、保留 build |
| 文件详情 tabs | **原始预览 / 切片分段 / 检索单元 / 实体提及** | 对应每快照实际资产 `asset_raw_segments` / `asset_retrieval_units` / `asset_segment_entity_mentions` + 原文；篇章关系不做 tab |
| 范式编辑器 | 留 `/mining/workflows`（admin 全局面） | `WorkflowListView` 明确"全局共享不随域切换"；编辑范式是管理员能力，KB 只选范式 |
| `mining_runs.kb_id` | 从 `metadata_json` 提升为正式列 + 索引 | 按库查挖掘记录高效 |
| KB 挖掘引擎 | `mine_kb` 改走 workflow 引擎 + 范式绑定（解 B2） | 当前 `mine_kb` 不写 `execution_engine` 默认 legacy，workflow 编辑器对 KB 无效 |
| 产物归属（B3，更正） | **核验**（不是新写）workflow 路径 `_prepare_document_states`（`jobs/run.py:888-904`）在 `mine_kb` 无 preflight 场景下按 `storage_path` 正确填 `existing_doc` | 早期决策文档说"workflow 不填 existing_doc、要补 `document_executor`"——**过时**。master 的 workflow 服务层已在 `_prepare_document_states` 填 `existing_doc`（`planned is None` 时调 `get_document_lifecycle_state`）；`document_executor` 只消费不构造。切 workflow 后 kb_id 经 `existing_doc→asset_documents.kb_id` 自然归属，**无需在 executor 补**，只需核验 mine_kb 场景命中正确 |
| 砍哪些顶层页 | 只砍 `/knowledge`（知识资产）+ `/graph`（知识图谱）；保留 `/entities`、`/ontology` | 严格按 Req3/4；实体/本体页未要求砍 |

## 5. 目标形态

### 5.1 前端（grounded 到现状代码）

**侧栏收敛**（`Sidebar.vue:44-58`，现状 13 项平铺）
- 砍：`/knowledge`（知识资产，`DocumentsView`，域级文档表）、`/graph`（知识图谱，`GraphView`，已证为段落篇章关系）。
- 保留：`/entities`（实体图谱）、`/ontology` + `/ontology/graph`（本体，domain 级 admin/探索）；`/mining/workflows`（范式编辑器 admin）；`/mining`（挖掘 run 管理，domain 级）；`/search`、`/paradigm`、`/llm`、`/settings`。
- **数据范围说明**（避免两入口展示分叉）：KB 触发的 run 同时出现在 `/mining`（域级全量）和 KB「挖掘」tab（本 KB 过滤），二者读同一 `mining_runs` 表；`/entities` 是**域级跨库**实体，文件 tab「实体提及」仅该文档 snapshot 的 mentions——范围不同，不冲突。

**KB 详情页**（`KbDetailView.vue`，现状 3 tab：files/members/settings）
- 新增第 4 个 tab「挖掘」（`activeTab` 联合扩四元，`KbDetailView.vue:101`）：
  - 上区：范式选择（读写 `knowledge_bases.mining_workflow_id`，从 `/api/mining-workflows/options` 取已发布范式）+ 触发按钮（选文件 / 整库增量）。
  - 下区：本 KB 的挖掘记录列表（状态/进度/范式版本/时间/失败计数 → 可点进 run 详情）。
- 现有 Header「挖掘」按钮（`KbDetailView.vue:34-44`，当前触发后 `router.push('/mining/${run_id}')` 跳走）改为：触发后**留在本 tab**、刷新记录列表。

**文件详情多 tab**（`KbDocPreviewView.vue`，现状单预览区，路由 `/kb/:kbId/doc/:docId`）
- 外层包 `<el-tabs>`（仿 `KbDetailView.vue:59-74`），现有预览区（`KbDocPreviewView.vue:23-54`）降级为 tab「原始预览」。
- 已挖文件追加 tab：「切片分段」「检索单元」「实体提及」——数据来自该文档当前 snapshot。
- 未挖文件只显示「原始预览」。
- `KbFileManager.vue:197-200` 的跳转、路由不变。

### 5.2 后端（Python 挖掘侧）

- `POST /api/kb/{kbId}/mine`（`kb/routes/mining.py`）改造：
  - 走 workflow 引擎：INSERT 写 `execution_engine='workflow'` + `kb_id`；按 `knowledge_bases.mining_workflow_id` → 取该范式 `current_version` → 解析 workflow 绑定四元组（仿 `/api/runs` 的 `workflow_run_binder.resolve`，注入到 run）。
  - **抑制 publish**：调 `mining_run(..., publish=False)`（新参数）→ 只 build、不 publish 到域级 active release（避免 B1）。
  - 输入模式：**选文件**（请求体带 `doc_ids` 子集）+ **整库增量**（扫 KB 目录，workflow 路径 `_prepare_document_states` 在无 preflight 时按 `storage_path` 走 `get_document_lifecycle_state`，自动判 SKIP/UPDATE/NEW）。两者由 `mining_run_documents.action` 承载。
  - 校验：KB 必须已选范式（`mining_workflow_id` 非空），否则 400/422 明确报错。
- `mining_run()` / `_finalize_run()`（`jobs/run.py`）：加 `publish: bool = True` 参数；`should_publish = publish and (not has_failures or publish_on_partial_failure)`。`/api/runs` 路径默认 `publish=True`（行为不变）。
- **核验**（非新写）workflow 路径 `_prepare_document_states`（`jobs/run.py:888-904`）：确认 `mine_kb` 无 preflight 场景下 `existing_doc` 按 `storage_path` 正确命中 KB 的 `asset_documents` 身份行（带 kb_id）→ 产物经 snapshot_link 自然归属 KB。若核验发现命中不对，再在 `_prepare_document_states` 修（**不是** `document_executor`）。
- 新增 / 改造 API：
  - `GET /api/kb/{kbId}/runs` —— 本 KB 挖掘记录列表（`mining_runs WHERE kb_id=?`）。
  - `GET /api/kb/{kbId}/documents/{docId}/knowledge` —— 文档当前知识（解析链：KB 最新成功 `asset_builds`(kb_id) → `asset_build_document_snapshots` → `document_snapshot_id` → 返回 segments / retrieval_units / entity_mentions）。KB 无 build 时返回空（前端只显原始预览）。
  - `knowledge_bases.mining_workflow_id` 读写（选/换范式；换范式不自动重挖，由用户触发）。

### 5.3 数据层变更（本次）

| 表 | 变更 | DDL 落点 | transactional | 目的 |
|---|---|---|---|---|
| `knowledge_bases` | + `mining_workflow_id TEXT NULL` | `databases/kb/schemas/005_kb_mining_binding.sql` | 是（加列+索引原子） | KB 选挖掘范式（软引用 mining_workflows.id，无 DB FK） |
| `mining_runs` | + `kb_id TEXT NULL` + `idx_mining_runs_kb(kb_id)` | `databases/mining_runtime/schemas/007_mining_run_kb.sql` | 是 | 挖掘记录按库查 |
| `asset_builds` | + `kb_id TEXT NULL` + `idx_asset_builds_kb(kb_id)` | `databases/asset_core/schemas/006_asset_build_kb.sql` | 是 | 文件 tabs 定位 KB 当前 build |
| `asset_publish_releases` | **不动** | — | — | release 延后 |
| `ontology_*` | **不动** | — | — | 本体/实体保持 domain |

`pg_schema.py` 把三个新 DDL 编进 `domain_schema_paths()`（`kb_isolation` 之后、按依赖插入），并把三个文件名加入 `transactional` 集合（现状该集合按文件名硬编码，漏一个会有并发竞态风险）。回填：存量 `mining_runs.kb_id` 从 `metadata_json->>'kb_id'` 回填；`asset_builds.kb_id` 从关联 `mining_run_id→mining_runs.kb_id` 回填。

## 6. 不在本次范围（deferred，含理由）

1. **Java serving kb_ids 接入** —— 检索整体延后（Req5）。含 `ScopeResolveOperator.PARAM_SCHEMA`（`{}`→kb_ids）、`AssetRepository.resolveActiveScope(domain,channel)` 加 kb_ids、`ActiveScope`/`RunArgs`/结果对象加 kb_id、`serving_query_cache` key 纳入 kb_ids。
2. **per-KB release + B1 根治** —— 本次靠"KB 挖掘 `publish=False`"让 B1 **不触发**（KB 挖掘不写域级 active release，同域其他 KB 的发布不受影响）。**彻底解**（每 KB 自己的 active release）留检索那期，与 serving kb_ids 一起做。届时 KB 挖掘从 `publish=False` 切到"publish 到 per-KB release"。
3. **本体/实体 per-KB** —— 用户明确后续专项。`ontology_*` 不加 kb_id，归一管线不改 scope。
4. **实体图谱/本体前端页重构** —— 本次只砍 `/knowledge`、`/graph`；`/entities`、`/ontology` 保留。

## 7. 验收标准

1. **未选范式的 KB 触发挖掘 → 400/422 明确报错**（不产生 run）。
2. KB 内选范式 → 触发整库增量挖掘 → `mining_runs` 写入（`execution_engine='workflow'`、`kb_id` 非空、workflow 绑定四元组非空）→ KB「挖掘」tab 看到该 run。
3. **增量 SKIP 正确**：整库增量挖掘时，未变文件 `action=SKIP`、不重算资产；只新增/变更文件产出新 snapshot。
4. **B1 不触发**：同域两个 KB（KB-A、KB-B）依次完成挖掘后，KB-A 的 `asset_publish_releases` active 状态/检索资产**不被 KB-B 破坏**（因 KB 挖掘 `publish=False`，不写域级 release）。
5. 挖完后点该 KB 任一已挖文件 → 看到 4 tab（原始预览/切片分段/检索单元/实体提及），数据来自该文档当前 snapshot；未挖文件只 1 tab。
6. 同文件换范式重挖 → 产新 snapshot（不同 `graph_hash`），文件 tabs 反映新范式产出；旧 snapshot 保留。
7. 侧栏不再有「知识资产」「知识图谱」；`/entities`、`/ontology`、`/mining/workflows` 仍在。
8. 范式编辑器 `/mining/workflows` 与 KB 解耦，照常可用。
9. 检索（`/search`、serving）行为与合并前一致（未动）。
10. `knowledge_mining/tests/kb/` 全绿；workflow 等价性测试（`test_full_workflow_equivalence` 等）不回归。
11. **B3 不回归**：KB 触发的 workflow run，产物 `asset_documents` 行 `kb_id` 正确非空（无幽灵身份）；`existing_doc` 在 mine_kb 无 preflight 场景正确命中。

## 8. 风险与已知债务

- **auto-publish 默认（已对冲）**：`_finalize_run` 默认 publish。本次用 `publish=False` 对冲 KB 挖掘。**风险**：若遗漏传参或新加挖掘入口忘了传，KB 挖掘会 publish → B1。验收 4 守。`/api/runs` 仍默认 publish（域级，不变）。
- **B3 状态更正（降级）**：workflow 路径**已**在 `_prepare_document_states` 填 `existing_doc`；本次是**核验** mine_kb 场景，非新写。风险从"必现"降为"需验证不回归"（验收 11）。
- **B2 依赖范式绑定**：KB 必须先选范式才能挖；`mine_kb` 加 `mining_workflow_id` 非空校验（验收 1）。
- **KB 知识暂不进 active release**：`publish=False` 意味着 KB 知识本轮**不可被 serving 检索**（只通过文件 tabs 看）。这是"检索延后"的必然代价，验收 9 体现。检索回归时切 per-KB publish 补上。
- **legacy/workflow 双引擎并存**：KB 走 workflow，`/api/runs` 仍可 legacy；债务留后续退役。
- **跨表无 DB FK**：`mining_runs.workflow_*/kb_id`、`knowledge_bases.mining_workflow_id` 均软引用，靠应用层；本次不补 FK。
- ~~`mine_kb` 与 `/api/runs` 同域互斥~~：共用 `_domain_run_lock(domain)`，同域串行、跨域并发，**正确，非风险**（已知约束，无需动作）。

## 9. 演进路线（本次之后）

- **检索回归期**：per-KB release（`asset_publish_releases.kb_id` + active 约束 `(kb_id,domain,channel)`，彻底解 B1）+ KB 挖掘从 `publish=False` 切到 per-KB publish + Java serving kb_ids 全链路 + 语义缓存 key。一个自洽改造包。
- **本体/实体 per-KB 期**：`ontology_*` 加 kb_id + 归一管线按库 scope（若届时仍需库隔离）。
- **legacy 引擎退役期**：`/api/runs` 全面切 workflow，删 `_run_legacy` 路径。

## 10. spec review 处置记录

- **BLOCKER（已修）**：P2"KB 挖掘不 publish"假设错误——代码默认 auto-publish（`jobs/run.py:1970-2009`）。改为 P2'：`mine_kb` 主动传 `publish=False`（build 但不 publish），不能用 `phase1_only`（会连 build 都跳过）。新增验收 4。
- **MAJOR M-1（已修）**：B3 落点过时——workflow 已在 `_prepare_document_states` 填 `existing_doc`，非 `document_executor`。改为"核验非新写"，§4/§5.2/§8 更正，验收 11 措辞改为"不回归"。
- **MAJOR M-2（已修）**：补验收 1（未选范式）、3（SKIP 增量）、4（B1 多库）。
- **MINOR（已修）**：§5.3 三个 DDL 显式标 transactional；§5.1 补 `/mining` 与 KB tab 同源、`/entities` 与文件 tab 范围差异说明；§8 去掉"同域互斥"伪风险。
