# AI 时代知识管理与知识工程调研包

> 生成日期：2026-07-28  
> 项目：CoreMasterKB / AgenticKB  
> 范围：基于 `docs/` 全量阅读、当前代码结构快速核验，以及公开工业实践调研。  
> 目标：把项目下一阶段从“文档型 RAG 系统”推进到“可编排、多形态、可审计、Agent-ready 的知识工程平台”。

---

## 0. 执行摘要

CoreMasterKB 的基础不是普通 RAG demo，而是已经具备生产系统骨架的知识工程平台：Python mining 负责文档解析、切片、实体抽取、检索单元、向量、build/release；Java serving 负责多路召回、重排、上下文组装和算子/范式执行；两侧通过 PostgreSQL 的 `asset_*` / `ontology_*` 资产契约交接。

当前最关键的战略转向已经在 `docs/` 中形成共识：

1. **KB 管理一等公民化**：文档身份从 mining run 中抽离，变成用户可管理的 KB/文档资产；挖掘退化为文档上的可重复动作。
2. **检索与挖掘双侧算子化**：不同业务场景对应不同知识形态，不再试图用一个固定 pipeline 通吃所有检索问题。
3. **知识形态从 F1 扁平切片扩展到 F2-F6**：实体图、社区报告、RAPTOR 语义树、PageIndex 目录树、LLM-wiki 条目。
4. **Agentic 入口上移**：Agent 不直接调 embed/retrieve/rerank 这类底层算子，而是调 `search / fetch / expand` 等粗粒度工具；闭环 loop 在 Agent 层，底层检索范式保持静态 DAG。

外部实践与项目规划高度一致：Microsoft GraphRAG 使用实体图、社区层级和 community report 支撑 global/local/DRIFT search；RAPTOR 用递归聚类摘要树解决长文档层级理解；STORM/Co-STORM 证明了“多视角研究→大纲→带引用 wiki 长文”的知识合成路线；OpenAI/Anthropic/LlamaIndex/LangGraph 的工具和编排实践也都指向“数据资产化 + 检索工具化 + 编排可恢复 + 引用可追溯”。

本项目真正要补的不是“再做一个 RAG”，而是四个工程闭环：

1. **资产治理闭环**：KB/文档/快照/发布/权限/来源回溯。
2. **知识形态闭环**：F1 已有，F4/F5 可先做，F2 本体线需修，F3/F6 依赖 F2。
3. **范式编排闭环**：检索算子已在位，挖掘算子化待落地；新增资产要通过 AssetStore/AssetTypeRegistry 或等价抽象进入检索。
4. **评测与运维闭环**：缓存、trace、质量评估、模板注册检查、provenance、CI 门禁。

---

## 1. 内部现状：项目已经具备的能力

### 1.1 总体架构

项目是 All-in-One 多服务架构：

| 服务 | 职责 | 现状判断 |
|---|---|---|
| `knowledge_mining` | 文档解析、挖掘、资产写入、发布 | 主链稳健；KB 管理已开始实现 |
| `agent_serving_java` | 检索、范式执行、上下文组装 | 内置检索稳健；算子系统完整但还未完全替代内置 |
| `llm_service` | LLM 任务、模板、embedding | 统一 LLM 运行时 |
| `main_control_service` | 域配置、控制面代理 | domain registry 是配置真相源 |
| `kb-ui` | 前端 | 后续 KB 管理和范式画布入口 |
| `mcp_server` | Agent/MCP 接入 | 适合作为 Agentic search 工具层入口 |

### 1.2 数据资产主链

核心数据链路：

```text
asset_documents
  -> asset_document_snapshots
  -> asset_document_snapshot_links
  -> asset_raw_segments
  -> asset_retrieval_units
  -> asset_retrieval_embeddings
  -> asset_builds / asset_build_document_snapshots / asset_publish_releases
```

这个模型有三个强点：

1. **document / snapshot / link 三层解耦**：身份、内容、摄取事件分离。
2. **hash 去重**：同内容复用 snapshot，天然适合跨 KB 去重。
3. **发布语义明确**：serving 只读 active release，挖完不等于可检索，必须 build/publish。

这些设计与现代企业知识管理中的“内容寻址、不可变版本、可回溯发布”一致，是后续扩展新知识形态的好基础。

### 1.3 KB 管理当前状态

文档中 KB 需求/设计已经完整，代码中也已经出现：

| 路径 | 状态 |
|---|---|
| `knowledge_mining/mining/kb/db.py` | 已有 async repository |
| `knowledge_mining/mining/kb/services/kb_service.py` | 已有 KB CRUD、权限、软删逻辑 |
| `knowledge_mining/mining/kb/routes/kbs.py` | 已有 `/api/kb` CRUD + members 路由 |
| `knowledge_mining/tests/kb/` | 已有 KB route/db/schema 测试 |

这说明项目已经进入 KB 管理 P2 阶段附近，但文件管理、文档上传入库、KB 触发 mining、文档状态派生、serving 范式按 KB 收窄仍需继续完成。

### 1.4 检索侧当前状态

Java 检索侧有两条路径：

| 路径 | 说明 | 问题 |
|---|---|---|
| 内置 `/api/v1/search` | 写死 pipeline，功能完整 | 战略上要下架；有语义缓存污染、特定 entity_graph ThreadLocal bug |
| 范式 `/api/v1/paradigm/**` | 19 算子 + 编译器 + 拓扑执行 + 版本化 | multi_query fan-out、语义缓存、资产抽象、KB scope 还需补 |

最有价值的已有资产是“静态 DAG + 不可变范式版本”。外部框架普遍重视工作流编排，但 CoreMasterKB 的范式版本化更适合企业内审计、回滚、灰度和复现。

### 1.5 挖掘侧当前状态

挖掘主链可工作：

```text
parse -> segment -> enrich -> entity_extract -> resolve
-> discourse -> retrieval_units -> embedding -> db_write
-> graph_write -> review gates -> build -> publish
```

主要缺口：

| 缺口 | 影响 |
|---|---|
| `mining-ontology-induction` 模板未注册 | 本体类型自动归纳 no-op |
| 多数 domain 默认未引种 ontology | F2/F3/F6 本体线短路 |
| `entity_relations` 不在流式阶段，只在 finalize 重聚合 | 容易误读，也限制实时关系产出 |
| 挖掘算子化框架尚未真正落地 | 新知识形态产出仍难以配置化 |
| 新资产无法自然进入 release 闸口 | F3/F4/F6 发布链路需要设计 |

---

## 2. 外部实践：AI 时代知识管理正在收敛到什么

### 2.1 从“文件夹 + 搜索”变成“知识资产操作系统”

OpenAI 的 vector store / file search API 把文件处理成可搜索资产，支持 chunking strategy、metadata、file status、过期策略和基于属性的过滤。这代表了现代 AI 知识库的基础产品形态：文件不是静态附件，而是有处理状态、属性、检索索引和生命周期的资产。来源：OpenAI Vector Stores API 文档。

对 CoreMasterKB 的含义：

| 外部做法 | 项目对应 |
|---|---|
| vector store 管理 processed files | KB 管理中的文档身份 + storage_path + 状态派生 |
| metadata / attributes filter | KB、目录、owner、visibility、domain、document metadata |
| file status completed/failed/in_progress | uploaded/mining/failed/mined/withdrawn |
| chunking strategy | mining 的 segment policy 和未来 workflow 参数 |

### 2.2 GraphRAG：从片段检索转向图谱 + 社区摘要

Microsoft GraphRAG 官方文档将流程拆成 Index 与 Query：Index 阶段从文本切片中抽取 entities、relationships、claims，做 Leiden 社区聚类并自底向上生成 community reports；Query 阶段提供 Global Search、Local Search、DRIFT Search 和 Basic Search。Global Search 用 community reports 做 map-reduce，适合整库主题/全局问题；Local Search 适合具体实体及邻域问题。

对 CoreMasterKB 的含义：

| GraphRAG 能力 | 项目对应形态 |
|---|---|
| TextUnits | F1 `asset_retrieval_units` |
| Entities / Relationships | F2 `ontology_entities` / `ontology_entity_relations` |
| Community hierarchy | F3 `asset_communities` |
| Community reports | F3 `asset_community_reports` |
| Global Search map-reduce | `global_map_reduce` 检索算子或上层 generator |
| Local / DRIFT Search | `entity_graph` + `community_recall` |

结论：项目文档中的 F2/F3 方向与 GraphRAG 工业路线完全一致。当前优先级应是先修通本体线，再做社区资产。

### 2.3 RAPTOR：长文档需要层级摘要树，不只是更大的 top-k

RAPTOR 论文/官方实现提出 Recursive Abstractive Processing for Tree-Organized Retrieval：对文档块递归聚类并摘要，形成树状层级，检索时可以在不同抽象层召回信息。它解决的是传统 top-k chunk 对长文档/全局叙事理解不足的问题。

对 CoreMasterKB 的含义：

| RAPTOR 能力 | 项目对应 |
|---|---|
| 叶子节点 = 原始 chunks | `asset_raw_segments` |
| 内部节点 = 聚类摘要 | 新增 `asset_tree_nodes` |
| tree search / collapsed retrieval | 新增 `tree_walk` |
| 递归摘要构建 | 新增 `tree_build` mining 算子 |

结论：F4 语义层级树是最适合优先落地的新形态，因为它不依赖本体线，只依赖已有 segment + embedding。

### 2.4 STORM / Co-STORM：知识工程从“抽取事实”升级到“生成可引用知识作品”

Stanford STORM 是 LLM-powered knowledge curation system：通过检索、多视角提问、大纲生成和引用写作，生成类似 Wikipedia 的完整报告。其 GitHub 说明显示 2024 年支持用户文档 grounding，Co-STORM 支持人机协同知识整理。

对 CoreMasterKB 的含义：

| STORM 能力 | 项目对应 |
|---|---|
| multi-perspective question asking | query planning / research 原子 |
| outline generation | `wiki_compose` 子步骤 |
| full-length report with citations | F6 `asset_wiki_articles` |
| grounding on user documents | 通过 `source_segment_ids` / evidence / snapshot 链回溯 |
| collaborative curation | ontology draft + review candidates + future wiki editor |

结论：用户提出的“LLM-wiki 本体演进”不是偏离主线，而是知识工程的高阶形态。它应建立在 F1 + F2 可用、provenance 完整、review workflow 可用的基础上。

### 2.5 Agentic RAG：Agent 调的是工具，不是底层检索算子

LlamaIndex 将 Agent 定义为能分解问题、选择工具、规划任务并使用记忆的决策引擎；其 Agentic RAG 用于在数据上构建能处理复杂研究任务的上下文增强助手。LangGraph 的 persistence/checkpoint 支撑 human-in-the-loop、memory、time travel debugging 和 fault tolerance。

对 CoreMasterKB 的含义：

| 外部实践 | 项目建议 |
|---|---|
| Agent 使用工具完成任务 | 暴露 `kb_search / kb_fetch / kb_expand` |
| 工作流可暂停/恢复 | 范式 executor 增加 checkpoint / interrupt_before |
| 复杂任务用 Agentic loop | S5 作为入口模式，不作为新资产形态 |
| 简单查询不应全走 Agent | Adaptive router：简单走固定范式，复杂走 Agent loop |

结论：项目文档坚持“Agent loop 上移，底层算子保持无状态/静态 DAG”是正确的。不要把检索底座改成无界 Agent 运行时。

### 2.6 Provenance / Lineage：知识可信度来自可追溯链路

W3C PROV 将 provenance 建模为 Entity、Activity、Agent，以及 `wasGeneratedBy`、`wasDerivedFrom` 等关系；OpenLineage 则把运行中的 job/run/dataset 作为 lineage 元数据标准。AI 知识工程中，引用、来源、生成活动、模型版本、prompt 版本都应进入 provenance。

对 CoreMasterKB 的含义：

当前已经有：

```text
evidence -> segment -> snapshot -> link -> document -> kb_id
```

建议补齐：

```text
generated_asset -> activity(mining_run / operator / prompt_version / model)
                -> used(source segments / entities / prior wiki article)
                -> agent(system / model / human reviewer)
```

这对 F6 wiki、F3 community report、F4 tree summary 尤其关键，因为它们不是原文，而是模型生成的二级知识资产。

---

## 3. 关键判断：CoreMasterKB 的目标态

### 3.1 不应定位为“文档问答系统”

更准确的定位：

> CoreMasterKB 是面向企业/领域资料的知识工程平台：把文档转化为多形态知识资产，并通过可编排范式和 Agentic 工具提供可审计、可组合、可引用的检索与知识服务。

### 3.2 三层产品心智

| 层 | 用户心智 | 系统实体 |
|---|---|---|
| 资料层 | 我的知识库、文件、目录、成员、权限 | `knowledge_bases` / `asset_documents` |
| 知识层 | 这些资料被挖掘成什么知识 | snapshot / segment / unit / entity / tree / community / wiki |
| 使用层 | 用什么方式查、问、导航、合成 | retrieval paradigm / agent tools / MCP |

### 3.3 六种知识形态应保留，但建设顺序要调整

| 形态 | 建设判断 |
|---|---|
| F1 flat chunk | 已有，继续作为基座 |
| F4 RAPTOR 语义树 | 优先做，独立性最好 |
| F5 PageIndex 目录树 | 优先做，复用 `section_path`，成本低 |
| F2 实体图 | P0 修本体线后继续增强 |
| F3 社区摘要 | 依赖 F2，作为 GraphRAG global 能力 |
| F6 wiki 条目 | 依赖 F1+F2+provenance+review，作为高阶知识合成 |

---

## 4. 与当前规划的差距分析

### 4.1 KB 管理差距

| 能力 | 当前状态 | 缺口 |
|---|---|---|
| KB CRUD | 代码已开始实现 | 需接入主 FastAPI app、完善错误语义 |
| 成员/权限 | 已有基础 | 需全链路 read/write enforcement |
| 文档上传入 KB | 设计完成 | 代码待补 |
| zip 解压保留目录 | 设计完成 | 代码待补 |
| 文档状态派生 | 设计完成 | 代码待补 |
| `/api/kb/{id}/mine` | 设计完成 | 代码待补 |
| serving 按 KB 范围检索 | 设计完成 | Java 侧待补 |

### 4.2 检索底座差距

| 能力 | 当前状态 | 建议 |
|---|---|---|
| 19 算子 + 范式版本化 | 已有 | 保留静态 DAG |
| dense 算子绕过 Retriever | 已知破例 | 演进 RetrieverOptions |
| `rrf` / `weighted_rrf` 重叠 | 已知 | 合并参数化 |
| multi_query fan-out | 孤立 | 引入 channel/reducer 或 fan-out 机制 |
| 语义缓存进范式路径 | 缺失 | 接入 checkpoint/cache |
| 资产抽象层 | 缺失 | AssetStore / AssetTypeRegistry |
| KB scope | 缺失 | `scope_resolve.params.kb_ids` |
| Agentic tool | 缺失 | MCP 暴露 search/fetch/expand |

### 4.3 挖掘底座差距

| 能力 | 当前状态 | 建议 |
|---|---|---|
| F1 产出 | 已有 | 稳定维护 |
| 本体引种 | 人工 | 增加自动/默认引种策略 |
| ontology induction | no-op | 补模板 + CI 检查模板存在 |
| mining workflow 算子化 | 设计态 | 先包装现有 9 个 stage |
| F4/F5 新资产 | 无 | 作为第一批新形态 |
| F3/F6 新资产 | 无 | 等 F2 修通后推进 |
| release 闸口 | 强绑 document_snapshot | 决策 A/B/C，长期建议通用 build_items |

---

## 5. 推荐目标架构

### 5.1 资产层

```text
KB / Document
  -> Snapshot(content-addressed)
     -> F1 Text Units
     -> F2 Entity Graph
     -> F4 Tree Nodes
     -> F5 Doc Tree
     -> F3 Communities / Community Reports
     -> F6 Wiki Articles
```

资产必须具备：

1. `domain`
2. `kb/document/snapshot` 回溯路径
3. `release/build` 可见性控制
4. `source_refs` / evidence
5. `generation_activity`：模型、prompt、operator、run、human reviewer

### 5.2 挖掘层

```text
input_ingest
-> parse_segment
-> enrich
-> ontology_entity_line
-> retrieval_unit_build
-> embedding
-> tree_build
-> doc_tree_build
-> community_build
-> community_report_build
-> wiki_compose
-> asset_persist
-> mining_finalize
```

第一版不要拆太细，先把每个高价值阶段做成可配置算子。后续按消融价值拆分。

### 5.3 检索层

```text
request_input
-> query_understanding
-> scope_resolve(kb_ids)
-> retrieve(assetKind=...)
-> fuse
-> rerank
-> assemble / cite_aware_assemble
```

新增资产不应新增一堆专用 retrieve 算子，而应通过 AssetStore 注册进入 generic retrieve。

### 5.4 Agent 层

```text
kb_search(query, paradigm_id or intent, top_k)
kb_fetch(document_id, segment_id?, mode)
kb_expand(seed_id, kind=entity|segment|tree, depth)
```

Agent loop 做：

```text
plan -> search -> grade -> rewrite -> search/fetch/expand -> synthesize -> cite
```

底层范式只负责稳定、可复现、可审计的子任务。

---

## 6. 推荐路线图

### Phase A：先把 KB 管理闭环打通

目标：用户能创建 KB、上传文件、显式挖掘、看到状态、按 KB 范式检索。

1. 完成 KB DDL 和 `pg_schema.py` 注册。
2. 完成 KB CRUD 接入主 app。
3. 完成文档上传/zip/目录/下载/软撤回。
4. 完成 `/api/kb/{id}/mine`。
5. Java `scope_resolve` 加 `kb_ids`。
6. 结果来源标注 `kb_id/kb_name`。

### Phase B：修现有高杠杆故障

目标：让 F2 本体和范式替代的基础稳定。

1. 注册 `mining-ontology-induction` 模板。
2. 增加模板存在性 CI 检查。
3. 修 `/api/v1/search` 特定 entity_graph ThreadLocal 问题。
4. 修语义缓存污染。
5. 收敛 pyproject/requirements/Dockerfile 依赖分裂。

### Phase C：检索范式底座正交化

目标：新增检索策略不改核心框架。

1. 演进 Retriever 接口，修 dense 破例。
2. 合并 `rrf` / `weighted_rrf`。
3. 增加角色接口和编译期排序不变量。
4. 引入 AssetStore / AssetTypeRegistry。
5. 接入语义缓存到范式路径。

### Phase D：先落地 F4/F5

目标：用低依赖新形态验证“新资产→新范式→可检索”闭环。

1. `asset_tree_nodes` + `tree_build`。
2. `asset_doc_trees` + `doc_tree_build`。
3. `tree_walk` / `tree_browse` / `drill`。
4. 范式模板：长文综述、章节导航、下钻问答。

### Phase E：再落地 F2/F3 GraphRAG

目标：实体图 + 社区摘要支持 local/global 图谱问答。

1. 本体默认引种策略。
2. ontology induction 自动候选。
3. `community_build` / `community_report_build`。
4. `community_recall` / `global_map_reduce`。
5. GraphRAG local/global/drift 范式。

### Phase F：最后做 F6 LLM-wiki

目标：从“检索答案”升级到“可引用知识作品”。

1. `asset_wiki_articles`。
2. `wiki_compose`：research → outline → compose → cite。
3. `wiki_link`：边写边链，产 relation candidates。
4. `wiki_recall` + raw_recall 双轨 RRF。
5. wiki 编辑/评审/发布工作流。

---

## 7. 关键决策建议

| 决策 | 建议 | 理由 |
|---|---|---|
| KB 范围绑定 | 范式设计态死绑 `kb_ids` | 与当前需求一致，权限简单 |
| 新资产 release 闸口 | 短期 A/C，长期 B `asset_build_items` | F4/F3 跨 snapshot，需要通用闸口 |
| Agentic loop 位置 | 上层工具层 | 保持底层 DAG 可复现 |
| F4 vs F3 先做谁 | 先 F4/F5 | 不依赖本体，最快验证新资产链路 |
| LLM map-reduce 在哪做 | 倾向上层 generator 或专用 output 算子 | 避免 retrieve 算子承担生成职责 |
| 内置 `/api/v1/search` | 改薄壳跑默认范式 | 向后兼容 |
| provenance | 按 W3C PROV 扩展 | wiki/community/tree 都需要生成活动可追溯 |

---

## 8. 可执行 Backlog

### P0：必须先做

1. 完成 KB 管理 P1-P3：DDL、路由、文档上传。
2. 修 `ontology_induction` 模板缺失。
3. 修语义缓存污染。
4. 给模板注册加测试/CI。
5. 补 serving `scope_resolve(kb_ids)`。

### P1：高价值底座

1. RetrieverOptions。
2. AssetStore / AssetTypeRegistry。
3. fan-out / reducer。
4. 范式路径缓存。
5. 来源标注 `kb_id/kb_name`。

### P2：新知识形态试点

1. F4 tree nodes。
2. F5 doc tree。
3. tree 检索范式。
4. 前端目录导航/范式选择。

### P3：GraphRAG / LLM-wiki

1. F2 本体增强。
2. F3 community reports。
3. F6 wiki articles。
4. Agentic search MCP tools。

---

## 9. 风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 同时推进 KB、范式、挖掘新资产导致范围失控 | 三条线都大 | 以 KB 闭环 + F4 试点为第一里程碑 |
| 本体线不修，GraphRAG/wiki 都会空转 | F3/F6 依赖 F2 | ontology induction / bootstrap 列 P0 |
| 新资产进不了 active release | serving 看不到 | 先用 sidecar/伪 document，尽快决策通用 build_items |
| Agentic 成本过高 | 复杂 loop LLM 调用多 | Adaptive router，简单查询固定范式 |
| 生成资产不可审计 | wiki/community/tree 是二级生成物 | PROV 活动、模型、prompt、source refs 必填 |
| 范式数量爆炸 | KB 组合死绑 | 模板 + clone + 命名规范 |

---

## 10. 资料来源

### 项目内部

1. `docs/知识库管理-需求文档.md`
2. `docs/kb-management-design.md`
3. `docs/kb-management-implementation-plan.md`
4. `docs/检索与挖掘-场景驱动需求文档.md`
5. `docs/检索算子底座-演进框架.md`
6. `docs/下一阶段-算子化统一规划.md`
7. `docs/研讨/现状-数据库-database.md`
8. `docs/研讨/现状-挖掘-knowledge_mining.md`
9. `docs/研讨/现状-检索-agent_serving_java.md`
10. `docs/研讨/现状-本体-ontology.md`

### 外部资料

1. Microsoft GraphRAG docs: https://microsoft.github.io/graphrag/
2. GraphRAG Query Overview: https://microsoft.github.io/graphrag/query/overview/
3. GraphRAG Global Search: https://microsoft.github.io/graphrag/query/global_search/
4. GraphRAG Indexing Overview: https://microsoft.github.io/graphrag/index/overview/
5. Microsoft Research GraphRAG project: https://www.microsoft.com/en-us/research/project/graphrag/
6. RAPTOR official implementation: https://github.com/parthsarthi03/raptor
7. STORM official repository: https://github.com/stanford-oval/storm
8. OpenAI Vector Stores API: https://platform.openai.com/docs/api-reference/vector-stores
9. OpenAI Vector Store Files API: https://platform.openai.com/docs/api-reference/vector-stores-files
10. LlamaIndex Agents docs: https://llamaindex.openml.io/python/framework/use_cases/agents/
11. LlamaIndex Workflows docs: https://docs.llamaindex.org.cn/en/stable/understanding/workflows/
12. LangGraph Persistence docs: https://docs.langchain.com/oss/python/langgraph/persistence
13. W3C PROV-O: https://www.w3.org/TR/prov-o/
14. W3C PROV-DM: https://www.w3.org/2012/10/prov-dm
15. OpenLineage: https://openlineage.io/

