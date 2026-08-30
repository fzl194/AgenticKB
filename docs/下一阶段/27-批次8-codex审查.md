结论：批次 8 不能判定为“全部完成”。目前属于“主框架和基础检索链已经落地，但结构化数据闭环、readiness 发布门禁、实验增强算子、产品收口仍存在关键缺口”。

尤其是你最初强调的“充分利用结构化数据”，当前代码实际上没有闭环：表格行在 PostgreSQL 落库前被兼容投影重新压成 `table`，官方预置又默认不拆表格行，最终 `table_cells=0`，导致 `query_structured_asset` 虽然写出来了，但标准生产链通常没有可查询的数据。

本次只审视，没有修改任何代码。

## 一、阻塞验收的问题

| 优先级 | 问题 | 实际影响 | 代码证据 |
|---|---|---|---|
| P0 | 表格结构在生产链中丢失 | `table_row` 被投影成 `table`；结构化查询基本查不到行/cell | [projection.py](D:/mywork/AgenticKB/knowledge_mining/mining/segment_compiler/projection.py:29)、[repositories_pg.py](D:/mywork/AgenticKB/knowledge_mining/mining/segment_compiler/repositories_pg.py:68) |
| P0 | 官方挖掘预置默认 `tableView=whole` | 即使移除兼容投影，默认链也不生成逐行资产 | [options.py](D:/mywork/AgenticKB/knowledge_mining/mining/workflow/operators/options.py:232)、[templates.py](D:/mywork/AgenticKB/knowledge_mining/mining/workflow/templates.py:45) |
| P0 | readiness 只计算、不持久化、不参与发布 | `mining_finalize` 仍按 `assets_persisted` 和文档失败数发布，可能把结构不完整资产发布为 ready | [persist.py](D:/mywork/AgenticKB/knowledge_mining/mining/retrieval_projection/persist.py:86)、[persist handler](D:/mywork/AgenticKB/knowledge_mining/mining/workflow/handlers/persist.py:104)、[finalize.py](D:/mywork/AgenticKB/knowledge_mining/mining/workflow/handlers/finalize.py:13)、[run.py](D:/mywork/AgenticKB/knowledge_mining/mining/jobs/run.py:2582) |
| P0 | 显式 hard filter 没有真正兑现 | `relative_path_prefix/date_range/structure_ref/include_descendants` 被静默忽略，可能返回超出过滤范围的数据 | [ScopeFilterPushdown.java](D:/mywork/AgenticKB/agent_serving_java/src/main/java/com/coremasterkb/serving/operator/operators/retrieve/ScopeFilterPushdown.java:23)、[ParadigmRequests.java](D:/mywork/AgenticKB/agent_serving_java/src/main/java/com/coremasterkb/serving/operator/api/ParadigmRequests.java:64) |
| P0 | `doc_`、`st_` public ref 没有在搜索过滤前解析 | MCP 传入公开 ref，SQL 却直接与内部 `document_ref/target_ref` 比较；常见结果是过滤后零命中 | [MCP 参数说明](D:/mywork/AgenticKB/mcp_server/server.py:204)、[ScopeFilterPushdown.java](D:/mywork/AgenticKB/agent_serving_java/src/main/java/com/coremasterkb/serving/operator/operators/retrieve/ScopeFilterPushdown.java:57)、[projector.py](D:/mywork/AgenticKB/knowledge_mining/mining/retrieval_projection/projector.py:48) |
| P0 | 挖掘范式创建 UI 仍使用旧 7 套模板 | UI 默认提交 `full`，后端现在只接受 `lexical_assets/hybrid_assets/query_alias_assets/longdoc_assets`，新建范式会失败 | [WorkflowListView.vue](D:/mywork/AgenticKB/kb-ui/src/views/mining/WorkflowListView.vue:97)、[templates.py](D:/mywork/AgenticKB/knowledge_mining/mining/workflow/templates.py:86) |
| P0 | MCP 配置 UI 仍是旧六件套 | 仍提交已经删除的 `get_segment_fulltext`，没有四个新结构工具；保存配置可能直接被后端判为未知工具，或把新工具全部关闭 | [McpAccessView.vue](D:/mywork/AgenticKB/kb-ui/src/views/McpAccessView.vue:150)、[mcp_access_service.py](D:/mywork/AgenticKB/knowledge_mining/mining/kb/services/mcp_access_service.py:29) |

因此，实施报告中“三波次全部实施、部署并完成端到端验收”的表述过度乐观：[26号实施报告](D:/mywork/AgenticKB/docs/下一阶段/26-批次8-clean-break-实施与端到端验收报告-2026-08-30.md:6)。

## 二、挖掘侧算子逐项审视

| 算子 | 审视状态 | 结论 |
|---|---|---|
| `input_ingest` | 完成 | 正式注册、输入初始化和 workflow 接入基本符合需求 |
| `document_parse` | 基本完成 | 已改为 ParseIR → `MiningDocumentBundle`，handler 不再回投 `DocumentContext` |
| `segment_compile` | 有严重 bug | 编译器本身支持 `whole/rows/both`，但 PG store 仍通过 legacy projection 写入，`table_row` 信息丢失 |
| `retrieval_unit_project` | 部分完成 | 类型化表示框架已建；但没有生成 `document` representation，`section` 默认关闭，且生产链看不到 `table_row` |
| `embedding` | 部分完成 | 已按 representation 类型选择策略并支持覆盖/fallback；但 embedding 返回数量不足时用 `zip` 静默吞掉，仍可写入无向量记录并错误声明 dense ready |
| `query_expansion_generate` | 未形成可用能力 | catalog、handler、facade 已有，但生产组合根没有注入 service，正式预置运行时只会 fallback |
| `hierarchical_summary_generate` | 未形成可用能力 | 同样没有生产 wiring；现实现也不是严格的自底向上层级摘要，只按精确 heading path 独立摘要 |
| `asset_persist` | 部分完成 | 三面 writer 已建，但 representations/embeddings 在此前已经写最终表，因此它不是唯一入库者，也不是完整三面原子事务 |
| `mining_finalize` | 不符合核心需求 | 没有依据四类 readiness 决定能否发布 |
| `enrich` | 目录隐藏，未 clean break 删除 | 正式 catalog 不再展示，但 Options 和 legacy pipeline 仍存在 |
| `discourse_line` | 目录隐藏，未 clean break 删除 | 正式链已退役，但 legacy `discourse_stage` 仍可被 legacy engine 执行 |
| `contextual_retrieval_enrich` | 目录隐藏，残留代码 | 不再正式注册，但旧参数/逻辑没有完全清理 |
| `retrieval_unit_build` | 正式替换完成，旧残留存在 | 正式被 projector 替代，但 `RetrievalUnitOptions` 等兼容定义仍保留 |
| `entity_extract` | 正式 catalog 隐藏，但产品未完全隐藏 | 研究代码保留符合决策；API、路由、运行详情仍暴露 |
| `entity_resolve` | 同上 | 未注册正式 workflow，但旧运行线/API 仍在 |
| `entity_relation_extract` | 同上 | compiler 仍有专门依赖分支残留 |
| `entity_review_gate` | 同上 | 产品仍有实体审核页和运行暂停展示 |
| `ontology_induction` | 同上 | 产品路由和 API 仍可访问 |
| `ontology_review_gate` | 同上 | UI 仍有评审页面 |
| `graph_write` | 同上 | 正式 catalog 隐藏，但产品/legacy 线没有完全隔离 |

正式 catalog 当前确实只剩 9 个算子，这一层完成得比较干净：[catalog.py](D:/mywork/AgenticKB/knowledge_mining/mining/workflow/operators/catalog.py:28)。

但 clean break 没有完成：

- 旧 Options 仍存在：[options.py](D:/mywork/AgenticKB/knowledge_mining/mining/workflow/operators/options.py:52)。
- legacy engine 仍可执行 `enrich/discourse/retrieval_units`：[run.py](D:/mywork/AgenticKB/knowledge_mining/mining/jobs/run.py:2395)。
- 正式 `asset_persist` 甚至仍暴露 `ontologyAssets` slot：[catalog.py](D:/mywork/AgenticKB/knowledge_mining/mining/workflow/operators/catalog.py:72)。

## 三、挖掘资产层的具体缺口

### 1. Representation 契约不完整

已经完成：

- prose/table/table_row/list/code/formula/figure_caption 类型矩阵；
- canonical target；
- source refs；
- lexical/dense/returnable 三类 eligibility；
- embedding policy 与 representation 关联。

仍缺：

- 没有 `document` representation；
- `section` representation 默认关闭；
- `source_refs/parent_ref/context_group_id` 没有完整持久化到 v2 单元表；
- section ref 直接拼标题路径，重复标题、斜杠标题可能冲突；
- facets 只有 document/content type/section path，无法支持当前 MCP 宣传的路径、日期等过滤。

### 2. 两个实验 LLM 算子不只是“缺接线”

`query_expansion_generate` 还有以下实现问题：

- 用字符数而不是 token 做资格门；
- LLM 可自己返回 `source_text`，answer span 校验会信任该字段，失去“答案必须来自原文”的意义：[query_expansion.py](D:/mywork/AgenticKB/knowledge_mining/mining/retrieval_projection/query_expansion.py:152)；
- `maxAliasesPerTarget` handler 校验后，没有真正控制 facade；
- facade 调用 `replace_for_snapshot`，如果与主 representation store 共用，会覆盖基础表示；如果另设 store，当前 `asset_persist` 又不会读取它。

`hierarchical_summary_generate`：

- 没有把子章节摘要作为父章节输入；
- 文档摘要只会处理无 heading path 的段落；
- 缺 prompt/model/source hash 版本和增量祖先重建。

所以这两个算子应判定为“接口壳和单测样例已完成，业务能力未完成”，不能仅记作普通 wiring 遗留。

### 3. readiness 存在错误判定

[readiness.py](D:/mywork/AgenticKB/knowledge_mining/mining/retrieval_projection/readiness.py:52) 中：

- `dense_ready` 只要求任意一个 representation 有 embedding record，不校验向量非空，也不校验覆盖率；
- `structured_query_ready` 只看表头和 readiness 字段，不要求有真实数据行/cell；
- `structure_navigate_ready` 强制要求 order edge，单段文档可能错误判定不可导航；
- 类型注解使用了未定义的 `RetrievalRepresentation`，真实导入名称是拼写错误的 `RetrieRepresentation`。

更关键的是这些事实没有写入 snapshot/build manifest，只留在 bundle diagnostics。

## 四、检索侧算子逐项审视

当前 Spring 正式注册 8 个算子，这一点符合目标目录：

| 算子 | 状态 | 审视结论 |
|---|---|---|
| `query_embed` | 部分完成 | 确实每 query 只嵌入一次；但官方 hybrid 图没有把 scope 接给它，维度兼容检查不会执行 |
| `scope_resolve` | 有严重 bug | 授权和活动 Build 解析完成；hard filter 只是透传，缺 schema 校验、public ref 解码和完整执行 |
| `fts` | 核心完成、过滤不完整 | CJK 分词、v2 表、canonical 聚合已实现；受 hard filter 缺陷影响 |
| `dense_vector` | 核心完成、降级语义有偏差 | v2 embedding 和 canonical 聚合已实现；维度不匹配在正式图中会退化为空 dense，而不是前置明确失败 |
| `rrf` | 完成 | 多通道 canonical 融合、权重、稳定顺序基本符合需求 |
| `model_rerank` | 基本完成 | 专用模型失败保持 RRF 顺序；但仍构造旧 `QueryUnderstanding` DTO，且 alias 命中时重排文本实际是生成问题，不是源证据摘要 |
| `evidence_hydrate` | 基本完成 | 类型化回源、邻窗/父章节/整文预算、批量读取、表头回填设计较完整 |
| `assemble` | 完成度较高 | 公共 EvidenceResponse、opaque refs、预算、去重、剥离内部 ID 均符合要求 |

官方 hybrid 图缺少 `scope → query_embed` 边：[ParadigmService.java](D:/mywork/AgenticKB/agent_serving_java/src/main/java/com/coremasterkb/serving/operator/paradigm/ParadigmService.java:74)。

已决定删除的 `request_input/query_understanding/multi_query/hyde/graph_expand/identity/weighted_rrf/llm_rerank/score_rerank/collect` 正式算子基本已删除。`entity_exact/entity_graph` 也没有 Spring 注册，属于研究隐藏状态，这部分完成得较好。

## 五、结构化检索与 MCP 闭环

### 已完成

- `structure_navigate` 后端服务；
- `structured_query` schema-bound DSL；
- 白名单操作符和参数绑定；
- typed errors；
- `get_evidence/get_document/inspect_knowledge/navigate_structure/query_structured_asset`；
- 搜索响应已经收敛为 `query/evidence/has_more`；
- 普通响应不再暴露 unit ID、score、图边等内部信息；
- public ref 在结构工具中会重新做用户、KB、活动快照授权。

### 仍有问题

1. 结构化查询代码存在，但默认生产资产没有 table cells，因此能力是“工具已建，数据面不可用”。

2. `inspect_knowledge` 没有读取冻结 readiness，而是现场数表推断。这会与挖掘发布时的能力事实漂移。

3. public ref 解析是枚举授权范围内最多 20 万候选，再逐个 HMAC 匹配：

   - 上限定义：[StructureRefService.java](D:/mywork/AgenticKB/agent_serving_java/src/main/java/com/coremasterkb/serving/structure/StructureRefService.java:35)
   - 查询直接 `LIMIT` 且没有稳定排序：[StructureToolMapper.xml](D:/mywork/AgenticKB/agent_serving_java/src/main/resources/mapper/StructureToolMapper.xml:90)

   对千文档知识库，如果 representations 超过 20 万，部分合法 ref 会不可解析，而且具体遗漏对象可能不稳定。

4. MCP 中 `debug=true` 的描述说会返回诊断，但 Python client 只取 `evidenceResponse`，serving 返回的 trace 被丢掉。

5. FastMCP 自定义 instructions 仍未注入，这一点实施报告已承认。

6. MCP README 仍描述旧两件套和 `get_segment_fulltext`：[README.md](D:/mywork/AgenticKB/mcp_server/README.md:10)。

## 六、产品隐藏要求实际上没有完成

虽然侧边栏删掉了实体/本体菜单，但并不等于产品不呈现：

- 实体、本体、审核页面路由仍能直接访问：[router/index.ts](D:/mywork/AgenticKB/kb-ui/src/router/index.ts:84)；
- ontology/graph API 仍正式挂载在 `/api`：[app.py](D:/mywork/AgenticKB/knowledge_mining/mining/api/app.py:234)；
- Run 详情仍显示本体线、实体数量、审核入口；
- 文档预览仍显示实体提及；
- Dashboard 仍显示实体资产；
- 挖掘范式创建页仍直接展示“篇章增强、实体图谱、本体演化、全量知识构建”。

因此，用户决策“代码保留研究，但产品不呈现”只完成了侧边栏隐藏，没有完成 API/UI/runtime 产品隔离。

## 七、验证结果

实际运行结果如下：

| 验证项 | 结果 |
|---|---|
| 批次 8 挖掘定向测试 | 116 passed，3 skipped |
| MCP 测试 | 50 passed |
| UI 测试 | 254 passed |
| UI production build | 成功，存在大 chunk 警告 |
| Java 批次 8 定向测试 | 174 executed，1 failure |
| Java 全量测试 | 397 executed，5 failures、2 errors、1 skipped |
| 挖掘全量测试 | 1351 passed、2 failed、165 errors、17 skipped |

Java 定向失败正好反映双 schema 问题：测试仍期待 `snapshot_id`，当前 mapper 已被改回 legacy `document_snapshot_id`：[EvidenceSourceV2Mapper.xml](D:/mywork/AgenticKB/agent_serving_java/src/main/resources/mapper/EvidenceSourceV2Mapper.xml:148)。

Python 的 165 个错误大部分是当前 PostgreSQL 测试实例不可达，不能直接视为功能失败；但 2 个 object-storage workflow 测试确实失败。无论原因如何，当前环境无法复现实施报告所称的“Java 397 green”和挖掘全绿。

## 最终判定

可以确认完成的部分：

- 新旧算子目录主体收口；
- `MiningDocumentBundle`；
- 类型化 representation 基础框架；
- representation 级 embedding policy；
- FTS/dense/RRF/model rerank 主链；
- evidence hydrate；
- 极简 EvidenceResponse；
- 9 个 MCP tool 的代码主体；
- deterministic navigate/structured-query 后端服务。

存在 bug、不能按需求验收的部分：

- 表格行/cell 生产闭环；
- readiness 持久化与发布门禁；
- hard filter/public ref 过滤；
- 默认 hybrid embedding 维度校验；
- MCP 配置页和挖掘范式创建页；
- 产品实体/本体隐藏；
- public ref 大规模解析；
- representation/source/parent 持久化契约；
- embedding 输出完整性校验。

被忽略或只有壳的部分：

- `query_expansion_generate` 生产能力；
- `hierarchical_summary_generate` 生产能力；
- document representation；
- 真正层级摘要；
- 开发验收用真实样本质量指标；
- FastMCP 动态 instructions；
- clean-break 旧代码/API/UI 全量清理。

总体上，基础文本 RAG 链已经可用；但批次 8 的核心价值——“统一搜索表示之上，真正消费结构化资产并形成 Agentic 反馈闭环”——目前尚未完成。