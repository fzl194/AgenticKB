下面这份可以直接交给编码 Agent。它按能力契约组织，重点锁定“文档级原子入库、普通挖掘自动增量、状态等于真实可检索状态”。

````markdown
你正在修复 AgenticKB v1.0.1 的知识库挖掘、增量构建和状态展示问题。请直接实施代码、测试和必要的数据兼容逻辑，但不要提交或推送 Git。保留工作区内已有的用户改动，不要覆盖无关文件。

## 一、问题背景

现场知识库运行在内网，共 19,789 个 Markdown 文件，使用默认“标准混合资产”挖掘范式。

现象：

1. 绝大多数文档的解析、结构化数据、切片均成功。
2. 少量 embedding 调用失败。
3. 所有文档在前端看起来都显示“已挖掘”。
4. 最终任务报错：
   `readiness gate blocked asset activation`
5. 检索时报错：
   `one or more knowledge bases were not found`
6. 用户再次点击“挖掘”后，已经处理成功的文档仍然重新执行切片、投影、embedding、persist，未正确跳过。

## 二、已经确认的代码根因

### 1. 整批 readiness 门禁过严

`knowledge_mining/mining/jobs/run.py` 的 `_finalize_run()` 会汇总所有待激活快照。

当前只要一个快照存在：

- readiness 缺失；
- `search_ready=false`；
- 标准混合资产中 `dense_covered < dense_units`；

就会把整个 Run 判为 readiness 失败，不晋升任何文档、不创建 Build。

结果是：19,788 篇成功文档也会被少数 embedding 失败文档连带抛弃。

### 2. 文档过早标记 committed

`asset_persist_handler()` 完成 staging 写入后，就调用 `commit_document()`，将 `mining_run_documents.status` 标为 `committed`。

但此时：

- 文档还没有通过自身 readiness；
- staging 尚未晋升；
- 文档尚未进入 validated Build；
- serving 尚不可检索该文档。

因此当前前端“已挖掘”只是“中间资产已经写入”，不代表真正入库成功。

### 3. KB 增量判定被硬编码为 UPDATE

在：

`knowledge_mining/mining/jobs/run.py`

KB object input 分支中，当前逻辑直接执行：

```python
lifecycle = getattr(doc, "existing_doc", None)
lifecycle_action = "UPDATE"
```

它没有比较：

- 内容哈希；
- storage object revision；
- 当前 workflow ID/version/graph hash；
- 该文档是否进入当前 KB 的 validated Build；
- 上一次挖掘是否失败；
- 文档 readiness 是否完整。

因此 KB 每次点击挖掘都会重新进入文档执行链。

### 4. KB Build 没有使用自己的上一版 Build

`classify_documents()` 和 `assemble_build()` 当前通过：

```python
get_active_build(domain, channel)
```

读取域级 active release。

但 KB 挖掘任务设置了 `publish=false`，KB Build 是 `validated` Build，不会成为域级 active release。

所以：

- KB 上一次成功 Build 不会作为 parent；
- 增量 carry-forward 失效；
- 已有文档经常再次被判成 NEW；
- 不同 KB 还存在错误共享域级父 Build 的风险。

### 5. finalize 状态没有真实透传

`knowledge_mining/mining/workflow/handlers/finalize.py`

当前忽略 `_finalize_run()` 返回的失败状态，无条件返回 `OperatorStatus.SUCCESS`。

这导致：

- Run 实际失败；
- finalize 工作流节点却显示完成；
- 前端状态互相矛盾；
- resume 还可能复用错误的 completed finalize 事件。

### 6. 表格 cells 入库主键碰撞（2026-09-04 现场确认）

`05.学术学位申请审批书.doc`（docx，表单类文档，KB `8fb3d3c2...` / 文档 `03f6c7b6...`）自 2026-09-01 上传起每次挖掘都在 `asset_persist` 阶段崩溃：

```
duplicate key value violates unique constraint "asset_table_cells_staging_pkey"
Key (snapshot_id, table_ref, row_index, column_index)=(snap_..., ...-e-00015-table, 1, -1) already exists
```

根因链（已用库内 `asset_raw_segments` 落库切片原样重放 `project_structure` 确认）：

1. `_header_of`（`segment_compiler/compiler.py`）只取第一个 `is_header` 行做表头。表单/横幅式表格首行常是「跨全表合并的标题横幅」（仅 1 个 cell）或首行数据本身，表头列数远小于网格列数（现场：header(1) vs 网格 10+ 列；header(7) vs 网格 12 列）；
2. `compiler.py` `_emit_table` 的 `row_cells` 通道对 `column_index >= len(header_texts)` 的格子写兜底列名 `col{N}`，真实列号从此丢失；
3. `retrieval_projection/structure_projection.py` 用 `col_idx_of.get(name, -1)` 按列名反查列号：所有表头外列名全部塌缩为 `column_index=-1`，同行 ≥2 个即撞 `(snapshot_id, table_ref, row_index, -1)` 主键，整个快照 persist 事务回滚，`assets_persisted` 能力缺失，finalize 被门禁拒绝。

现场数据：166 个 cell 中 24 个重复键、4 张表受影响，单键最多 5 个 cell 挤在 -1。

回归来源：98050b4（29号 R02）引入精确 `row_cells` 通道。旧文本反解析路径会跳过表头外片段（静默丢列但不崩）；新通道不过滤，表单类文档必崩。该文档自 2026-09-01 上传起每次挖掘均失败，从未成功入库。

库内状态：staging cells/nodes 已正确回滚（0 行），units staging 106 行属更早阶段正常写入，重试即覆盖，无残留阻塞。

修复要求：

- `row_cells` 必须携带真实列号（三元组 `[name, value, column_index]`，读取端兼容二元组）；
- 结构投影优先消费真实列号，禁止把表头外列名塌缩为单一 `-1`；
- 投影层对 `(table_ref, row_index, column_index)` 重复 cell 防御性去重（保留首个并计数告警），一张表的形状问题不得炸掉整篇文档入库；
- 表单类文档（横幅表头/表头短于网格）必须能完整入库，表头外列不丢。

## 三、必须实现的产品语义

### 核心原则

知识资产必须按“文档级”原子入库。

一次挖掘任务允许部分文档成功、部分文档失败。任何单篇文档失败，都不能阻止其他成功文档进入 Build。

“已挖掘”必须严格等于：

> 该文档的快照已经通过自身 readiness、资产已经晋升，并且该快照已被当前 KB 的 validated Build 选中，能够被 serving 检索。

staging 完成不能叫“已挖掘”。

### 不增加独立的“仅补齐向量”功能

不要：

- 新增“仅补齐向量”按钮；
- 新增 dense repair 任务类型；
- 新增独立向量修复队列；
- 要求用户区分普通挖掘和向量补挖。

用户每次点击普通“挖掘”时，系统必须自动执行正确的增量判定：

- 已成功入库且没有变化的文档直接跳过；
- 内容未变但上次挖掘失败、未进入 Build 或 readiness 不完整的文档自动重试；
- 内容或范式变化的文档重新挖掘；
- 新文档正常挖掘。

## 四、文档级 readiness 规则

根据冻结 Workflow Manifest 判断每篇文档的必需能力。

对于标准混合资产，单篇文档入库至少要求：

```text
readiness 行存在
AND search_ready = true
AND dense_units > 0
AND dense_covered = dense_units
```

对于不包含 embedding 算子的轻量关键词资产，不要求 dense 完整，只要求基础搜索资产完整。

可选增强算子失败，例如 query alias 或 hierarchical summary 降级，不应阻止基础文档入库，除非 Workflow 明确把该能力声明为强制能力。

禁止把部分 embedding 的文档作为“完整标准混合文档”入库。

正确行为是：

- 该文档本次不晋升；
- 其他完整文档正常晋升；
- 下次普通增量挖掘自动重新处理该文档。

## 五、增量判定状态机

为每篇 KB 文档执行集合化判定，禁止对 19,789 篇文档做 N+1 查询。

需要同时读取：

- 当前 `asset_documents` 的 storage object ID、content revision、source hash；
- 当前绑定的 workflow ID、version、graph hash；
- 该 KB 最新 validated/published Build 中该文档的 active snapshot；
- active snapshot 的内容哈希、workflow 签名和 readiness；
- 该文档最近一次相同内容、相同 workflow 签名的挖掘结果；
- 最近一次结果是否真正进入 Build。

判定规则：

### NEW

满足任一情况：

- 文档从未进入该 KB 的 Build；
- 没有任何可复用快照；
- 首次上传后从未成功挖掘。

### SKIP

必须同时满足：

- 当前内容哈希或 source object revision 与 serving snapshot 一致；
- workflow ID/version/graph hash 一致；
- serving snapshot 的文档级 readiness 完整；
- 该文档确实存在于该 KB 最新 validated Build；
- 没有比该 Build 更新、但尚未成功入库的同内容同范式失败尝试。

SKIP 文档不能进入 parse、segment、project、embedding、persist。

实施补充（2026-09-04 E2E 追溯，两个易踩坑）：

1. **范式签名不能取自快照行**：`asset_document_snapshots.workflow_version_id/graph_hash` 存的是解析链标识（如 `new-parse-chain@1`），与 `mining_runs.workflow_version_id`（UUID）/`workflow_graph_hash` 是两套体系——按字面比较必然不等，所有文档会被永久判成 `update_workflow_changed`。签名必须从「最近一次 committed 该文档的 Run」取（`mining_run_documents ⋈ mining_runs`，见 `kb_incremental.fetch_kb_committed_signatures`）。
2. **SKIP 文档必须产出 outcome**：增量 SKIP 文档在 ingest 即 committed（带 serving 快照 identity）——不能从文档执行链里静默剔除（`continue` 不产 state），否则「SKIP carry + 其余文档全部失败」的 run 会因全局 capabilities 缺 `assets_persisted` 被 finalize 门禁误拦（`Cannot finalize before capabilities`）。SKIP 走 executor 的 persist marker 快路径：零算子执行、SUCCESS、携带 capability。

### RETRY

内部可以使用 `RETRY` 或继续映射为 `UPDATE`，但必须能够区分原因。

满足任一情况：

- 内容哈希相同，但上一次挖掘失败；
- run_document 曾完成 staging，但没有进入 validated Build；
- readiness 缺失或不完整；
- embedding 失败；
- 之前的 Run 被整批门禁阻断，文档实际未激活；
- 最新尝试晚于当前 serving Build，且最新尝试未成功入库。

RETRY 必须重新进入文档挖掘链。

### UPDATE

满足任一情况：

- 内容哈希或 object revision 变化；
- workflow ID/version/graph hash 变化；
- 当前 active snapshot 与源文档不一致。

### 显式 force_redo

可以保留用户主动勾选的“强制重挖”，但：

- 不允许系统因全局签名比较而自动把所有文档设为 force_redo；
- workflow 签名变化应通过逐文档判定得到 UPDATE；
- 不允许在新 Build 成功前清除当前 serving 使用的正式资产；
- 所有新资产先写 staging，成功后再原子切换。

## 六、文档级入库流程

### 文档执行阶段

文档各算子继续产生 staging 资产。

`asset_persist` 成功后：

- 写入 `document_id` 和 `document_snapshot_id`；
- 记录 `assets_staged=true` 或等价事实；
- 不得把 run_document 状态改为 `committed`；
- 状态继续保持处理中，或使用数据库允许的中间状态；
- crash resume 所需的 persist marker 不能再依赖 `status='committed'`，应依赖：
  - document/snapshot identity 已写入；
  - 对应 asset_persist node event 成功；
  - staging 事实存在。

如果不希望增加新的数据库状态，可以保留 `processing`，通过 identity 和 node event 判断 staging 是否完成。

### finalize 阶段

将本次候选文档划分为：

```text
ready_documents
rejected_documents
skipped_documents
```

#### ready_documents

- 文档级 readiness 完整；
- 将其 snapshot 资产从 staging 晋升到 final；
- 在新 Build 中选择该 snapshot；
- Build 事务成功后将 run_document 标为 `committed`。

#### rejected_documents

包括：

- readiness 缺失；
- lexical 资产缺失；
- 标准混合资产 embedding 不完整；
- 任一必需资产处理失败；
- `asset_persist` 阶段崩溃（如表格 cells 主键碰撞，修复后表单文档不应再触发，但此类崩溃必须按单文档 rejected 处理，不得阻断其他文档）。

处理规则：

- 不晋升该文档的 staging 资产；
- 不选择失败的新 snapshot；
- run_document 标为 `failed`；
- 写入明确错误原因，例如：
  - `readiness_missing`
  - `search_not_ready`
  - `embedding_incomplete`
  - `embedding_failed`
- 如果该文档在 parent Build 中存在旧的成功 snapshot，继续 carry-forward 旧 snapshot；
- 如果是首次挖掘且没有旧 snapshot，则本次 Build 不包含该文档。

#### skipped_documents

- 从 parent Build 原样 carry-forward；
- run_document 状态为 `skipped`；
- 前端显示“已入库，内容未变化”，不要显示失败。

### Build 结果

新 Build 应包含：

```text
parent Build 中仍有效的文档
+ 本次 ready_documents 的新快照
- 已明确删除的文档
```

失败文档不能阻止 Build 创建。

任务状态规则：

- 所有可处理文档成功：`completed`
- 部分成功、部分失败：仍使用现有数据库允许的 `completed`，同时写：
  - `has_failures=true`
  - `partial_success=true`
  - `failed_count`
  - `committed_count`
- 全部失败且没有可继承的 parent Build：`failed`
- 本次全部失败但已有 parent Build：旧 Build 保持可用，Run 显示失败或部分完成，但不能破坏当前检索。

不要为了本需求新增不受数据库约束支持的 Run status。前端可以根据 `status + failed_count + committed_count + metadata` 显示“部分完成”。

## 七、KB 父 Build 修复

为 AssetCoreDB 增加明确的 KB-scoped 查询，例如：

```python
get_latest_validated_kb_build(kb_id: str)
```

要求：

- 只查询指定 `kb_id`；
- Build status 为 `validated` 或 `published`；
- 使用稳定的 `created_at DESC, id DESC` 排序；
- 不得返回同域其他 KB 的 Build。

调整：

```python
classify_documents(...)
assemble_build(...)
```

使其在 `kb_id` 非空时使用 KB 最新 validated Build；只有非 KB 的域级发布任务才继续使用 `get_active_build(domain, channel)`。

不同 KB 即使位于同一个 domain，也不能互为 parent。

## 八、当前 v1.0.1 失败任务的兼容恢复

需要提供兼容逻辑，尽量保住现场已经完成的 19,789 篇处理结果。

当前失败 Run 的问题是：

- run_document 被提前标为 committed；
- staging/readiness 仍然存在；
- 没有创建 Build；
- finalize node 可能错误记录为 completed。

对这种历史失败 Run 执行 resume 时：

1. 必须强制 replay `mining_finalize`，不能复用旧 completed finalize event。
2. 不能直接相信旧 `run_document.status='committed'`。
3. 重新按每篇 snapshot 的 staging/final readiness 做文档级划分。
4. readiness 完整的文档正常晋升并进入 Build。
5. embedding 不完整的文档改为 failed，不进入 Build。
6. 创建包含成功文档的 KB validated Build。
7. 下次用户点击普通“挖掘”时，只重试这些 failed/未入 Build 的文档。
8. 如果 staging 已不存在或被覆盖，应安全失败并提示需要重新执行增量挖掘，不能伪造成功状态。

目标是当前任务无需重新挖掘所有 19,789 篇即可恢复大部分可检索内容。

## 九、前端状态修复

### KB 文档列表

“已挖掘”状态必须基于：

```text
文档存在于该 KB 最新 validated Build
AND selection_status = active
```

不能根据最近一次 run_document 是 committed、存在切片或存在 snapshot 来判断。

建议显示：

- `已入库`
- `已入库（未变化）`
- `挖掘失败，等待重试`
- `处理中`
- `未挖掘`
- `更新失败，当前仍使用上一版本`

### Run 详情

部分成功时显示：

```text
任务部分完成
成功入库：19,780
失败待重试：9
跳过未变化：0
```

不要再只显示泛化英文：

```text
readiness gate blocked asset activation
```

文档失败列表应展示具体失败原因。

### 检索行为

只要 Build 中至少存在一个可检索文档，KB 就应能被 serving 正常解析。

不得因为少数失败文档导致：

```text
one or more knowledge bases were not found
```

如果首次任务全部文档失败、确实没有 Build，则保留 no active build 类错误，但前端应显示可理解的中文原因。

## 十、embedding 错误处理

保留 embedding 对单篇文档完整性的影响，但不能扩大成整批失败。

要求：

- embedding HTTP、超时、认证、限流、返回数量不一致等错误必须有明确日志；
- 不得只返回空数组并吞掉真实异常；
- 可以增加有限重试，例如 3 次指数退避；
- 重试耗尽后，该文档 readiness 判定失败；
- 该文档不入新 Build；
- 其他文档继续执行。

如果一个文档包含多个 embedding 子批，任一必需子批失败，则该文档本次不入库。不要把部分向量文档声明为完整标准混合资产。

## 十一、必须修复的状态透传

修改：

`knowledge_mining/mining/workflow/handlers/finalize.py`

要求：

- 根据 `finalize_mining()` 的真实结果返回 SUCCESS 或 FAILED；
- 不能无条件返回 `OperatorStatus.SUCCESS`；
- 部分文档失败但 Build 成功时返回 SUCCESS，并携带 partial/degraded warning；
- Build 未创建时返回 FAILED；
- `_finalize_run()` 返回体应包含：
  - `status`
  - `build_id`
  - `committed_count`
  - `failed_count`
  - `skipped_count`
  - 文档级 rejection summary
  - 是否 partial success

修复 resume 时 finalize completed 事件被错误复用的问题。

## 十二、性能和事务约束

- 19,789 篇文档的生命周期、Build membership 和 readiness 必须集合查询。
- 禁止循环逐篇查询数据库。
- ready snapshot 晋升和 Build 创建必须位于同一资产事务中。
- Build 事务失败时，不能把 run_document 标为 committed。
- 新 Build 成功前，上一 validated Build 和正式资产必须持续可检索。
- 重挖失败不能删除或破坏上一版成功文档资产。
- selective mining 必须 carry-forward 未选择文档。
- 整库挖掘才允许识别删除文档。
- 保持不同 domain、不同 KB 的数据隔离。

## 十三、测试要求

必须按 TDD 先增加失败测试，再实施。

至少覆盖：

1. 三篇新文档，两篇 embedding 完整、一篇 embedding 失败：
   - 两篇进入 Build；
   - 一篇不进入 Build；
   - Run 部分完成；
   - KB 可检索两篇成功文档。

2. 下一次点击普通挖掘：
   - 两篇成功且未变化文档 SKIP；
   - 上次失败文档重新执行；
   - 成功文档的 parse/segment/project/embed/persist 均不调用。

3. 内容哈希相同，但最近一次相同范式挖掘失败：
   - 不能 SKIP；
   - 必须 RETRY/UPDATE。

4. 已有文档更新时 embedding 失败：
   - 新 snapshot 不入库；
   - parent Build 的旧 snapshot 继续可检索；
   - 前端显示“更新失败，仍使用上一版本”。

5. 内容或 workflow graph hash 变化：
   - 只重新处理受影响文档；
   - 不误跳过。

6. KB 第二次相同内容、相同范式运行：
   - run_document 为 SKIP；
   - Build 使用上一 KB Build 为 parent；
   - 不重复生成 embedding。

7. 同一 domain 的两个 KB：
   - 不能互相使用对方 Build 作为 parent。

8. asset_persist 成功但 Build 事务失败：
   - 文档不能显示 committed；
   - serving 不能把 staging 当正式资产。

9. readiness 缺失、search 不完整、embedding 不完整：
   - 只拒绝对应文档；
   - 不阻断其他文档 Build。

10. 首次任务全部失败：
    - 不创建空的可检索 Build；
    - Run 正确失败；
    - 前端不显示已挖掘。

11. 历史 v1.0.1 Run：
    - 文档状态错误地全为 committed；
    - finalize event 已 completed；
    - resume 能重新按 readiness 划分；
    - 成功文档直接建库；
    - 失败文档转为待重试；
    - 不重新执行成功文档的挖掘链。

12. 前端测试：
    - 已入库状态来自 Build membership；
    - 部分成功统计正确；
    - 失败文档原因正确；
    - 普通“挖掘”请求不携带全局 force_redo；
    - 不增加“仅补齐向量”入口。

13. 表单类文档（横幅表头/表头短于网格，可取 `05.学术学位申请审批书.doc` 的真实形态做 fixture）：
    - `asset_persist` 不再撞 `asset_table_cells_staging_pkey`；
    - 表头外列（`col{N}` 兜底名）保留真实列号入库，不塌缩为 -1；
    - 同行多个表头外列可全部入库；
    - `row_cells` 三元组与既有二元组（legacy 落库行）读取端兼容；
    - 恶意/异常形状（同键重复 cell）触发防御去重而非崩溃。

测试覆盖率保持 80% 以上。运行 Python 单元、集成测试以及相关前端测试。数据库相关测试至少增加一个小规模 PostgreSQL E2E，验证文档级晋升、KB parent Build 和下一轮增量 SKIP。

## 十四、禁止的错误修复

不要采用以下做法：

- 简单删除 readiness gate；
- embedding 失败后仍把该文档声明为完整成功；
- 增加“仅补齐向量”按钮或独立任务；
- 每次普通挖掘都 force_redo；
- 继续以 asset_persist 成功代表文档已入库；
- 继续使用域级 active release 作为 KB parent；
- 为了部分成功而提前删除当前正式资产；
- 只修改前端文案而不修复 Build 和状态语义；
- 手工 promote 全部 staging；
- 失败时回滚或丢弃其他成功文档；
- 使用逐文档 N+1 SQL。

## 十五、建议实施顺序

1. 先修复表格 cells 列号通道（根因 6，表单文档入库前置），补 RED 测试。
2. 补测试，锁定文档级入库和增量状态机。
3. 增加 KB-scoped latest Build 查询。
4. 修复 KB 生命周期判定，移除硬编码 UPDATE。
5. 将 asset_persist 的“staged”与最终 committed 分离。
6. 将 finalize 改为文档级 readiness 分区和部分 Build。
7. 修复 Build parent/carry-forward。
8. 修复 finalize 状态透传和 resume replay。
9. 修复前端状态来源和部分成功展示。
10. 加入 v1.0.1 失败 Run 的兼容恢复测试。
11. 跑完整验证并执行代码审查。

## 十六、交付说明

完成后请输出：

- 根因与修复摘要；
- 修改文件列表；
- 新的文档状态机；
- Build 组装和 parent 选择规则；
- 当前 v1.0.1 失败任务如何恢复；
- 执行过的测试及结果；
- 覆盖率；
- 尚存风险。

不要只报告“测试通过”。必须明确证明：

1. 单篇 embedding 失败不再阻止其他文档入库；
2. 已成功且未变化文档在下一次普通挖掘中确实不会再次执行；
3. 同哈希但上次失败的文档确实会自动重试；
4. 前端“已挖掘”与 serving 实际可检索状态一致。
````