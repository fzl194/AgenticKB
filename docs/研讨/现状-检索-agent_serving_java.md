# 现状：检索模块（agent_serving_java）

> 逐行核对源码产出。`docs/code_guide.md` 与 `docs/pipeline-0X-*.md` 已过时（pipeline-03 描述的 intent 驱动路由是死代码），不参考。论断标 `(file:line)`。
>
**一句话现状**：检索有**两条独立路径**——内置 `/api/v1/search`（写死的 QU→Router→Orchestrator→Fusion→Rerank→Assembler 管线）和范式执行 `/api/v1/paradigm/**`（v4 移植来的算子/范式系统，19 算子 + 编译器 + 拓扑执行），底层检索逻辑共享但编排层各走各的。内置管线工作稳健（QU/HyDE/MultiQuery 并行、三级 FTS 降级、cascading rerank、3000-token 压缩）。**算子/范式系统设计完整且范式路径无 ThreadLocal bug**，但内置管线的 entity_graph 路由在特定配置下恒空、语义缓存有污染问题。

---

## 1. 检索主线（HTTP → 最终上下文）

入口仅 `POST /api/v1/search`（`SearchController.java:22-40`），**没有** `/api/v1/context`（CLAUDE.md 描述但源码不存在；`ContextPack` 既是上下文也是结果）。

```
SearchController.search
  → SearchService.search(SearchRequest)                          application/SearchService.java:110
     ├─ 1. 域解析 + DomainContext.set + llmClient.setKnowledgeDomain     :117-131
     ├─ 2. ServingDomainProfile = domainPackReader.getProfile             :146
     ├─ 3. 并行三件套（CompletableFuture.supplyAsync，虚拟线程）          :154-187
     │     ├─ QueryUnderstandingEngine.understand    (LLM-first, 规则兜底)
     │     ├─ EmbeddingClient.embedHyDE              (HyDE，失败回落 embed)
     │     └─ MultiQueryExpander.expand              (LLM 改写，失败回落 [q])
     ├─ 4. RetrievalRouter.route（complexity 决定路由表）
     ├─ resolveActiveScope（active release → build → snapshots）
     ├─ TreeNavigator.inferSections（实体→章节命中）
     ├─ 5. SemanticCacheService.lookup（命中即返回，跳过下游全部）
     ├─ 6. RetrievalOrchestrator.execute（并行多路召回）
     ├─ 7. Fusion（weighted_rrf / rrf / identity，据 routes 数自动选）
     ├─ hydrateCandidates（取回 text/source_refs_json 等重列）
     ├─ 8. RerankPipeline.rerank（model→llm→score 级联 + 文本相似度去重）
     ├─ 9. ContextAssembler.assemble（seed/source/expand + 矛盾检测 + 3000t 压缩）
     └─ 9.5 SemanticCacheService.store（best-effort，**空 pack 也写**）   :395
```

**每步是否调 LLM**：QU/HyDE/MultiQuery 调 LLM（都有规则/原 query 兜底）；路由/召回/融合/组装**不调 LLM**；Rerank 是 model reranker → LLM listwise → score 兜底的级联。

---

## 2. 路由实际怎么决策（CLAUDE.md 死代码论断属实）

`RetrievalRouter.java:42-72` 定义了 `BUILTIN_ROUTES`（intent→routes 表），但 `route()` (line 89-132) **只用 `COMPLEXITY_ROUTES`** (line 21-40)，BUILTIN_ROUTES 从未被读取。`docs/pipeline-03-retrieval-router.md` 据此写的描述与代码不符。

实际决策（`RetrievalRouter.java:89-147`）：

1. **基础路由表由 `queryComplexity` 选**（`QueryUnderstandingEngine.deriveComplexity` :462-477）：
   - `simple` → `{entity_exact(1.5), lexical_bm25(1.0)}`
   - `medium` → `{lexical_bm25(1.0), dense_vector(0.9)}` ← **默认，不含 entity_graph**
   - `complex` → `{lexical_bm25, dense_vector, entity_exact(0.7), entity_graph(0.6)}`
2. **profile.routePolicy 覆盖**：域 pack 提供 `intent_strategy`/`route_policy` 时按 intent 取覆盖表。
3. **fusion**：routes>1 用 `weighted_rrf`，否则 `identity`。
4. **rerank**：intent 覆盖 > INTENT_RERANK（troubleshooting/comparison/procedure→cascade）> complex→cascade > needsComparison→cascade > score。
5. **assembly**：所有 tier 都开图扩展；intent 决定扩展关系类型（troubleshooting→causal，comparison→contrasts，procedure→sequences）。

**Complexity 推导**：comparison/troubleshooting/subQueries 非空/needsComparison → complex；command_usage 且有 command 实体/navigational → simple；其他 → medium。

---

## 3. 多路召回的具体通道

| 通道 | Retriever | SQL 关键操作 | 返回 |
|---|---|---|---|
| `lexical_bm25` | FtsRetriever | `search_vector @@ websearch_to_tsquery` + scope `facets_json @>` + section；**三级降级**：tsvector→trigram(`text % query`)→LIKE OR。recall_limit=topK×5 | FtsResultRow，score=ts_rank/trigram/0.0 |
| `dense_vector` | DenseVectorRetriever | pgvector `<=>` 余弦（用 `embedding_vector_vec` 真向量列）；scope+section 三级降级 | EmbeddingRow，score=1-cosine |
| `entity_exact` | EntityExactRetriever | `entity_refs_json::jsonb @> '[{"name":"X"}]'`（GIN-friendly）；空降级 `jsonb_array_elements` | 固定 score=0.95 |
| `entity_graph` | EntityGraphRouteRetriever → EntityGraphRetriever | **本体图召回**：①实体名匹配 `ontology_entities.canonical_name` 或 alias ②递归 CTE 走 `ontology_entity_relations`（无向、confidence 衰减）③join `ontology_evidence_nodes`→`asset_retrieval_units` | score=0.85×decay^hop×conf×(entity_card×1.1) |

**额外两类不在 Retriever 契约里**：
- `tree_navigation`（章节推断，`TreeNavigator.java:29-93`）：单条聚合 SQL，按 entity 命中次数 GROUP BY section_path，规则无 LLM。
- `graph_expand`（关系图扩展，`GraphExpander.java:107`）：ContextAssembler 调，BFS 每层一条 `selectNeighbors`，TOTAL_BUDGET=20 节点硬上限，关系优先级表 RST。**只在 assembly 阶段，不是召回 channel**。

召回总入口 `RetrievalOrchestrator.executeParallel`（`RetrievalOrchestrator.java:153`）用 `DomainContext.wrapRunnable` 把 domain 传到每个 route 的虚拟线程；dense_vector 无 embedding 自动跳过；单 route 时走 sequential 不开销线程池。

---

## 4. 算子/范式系统（v4 移植来的核心新增）

### 4.1 契约（core 包）

- `Operator`（`Operator.java:10-24`）：无状态 Spring 单例接口。`definition()` 返 OperatorDef，`execute(inputs, params, ctx)` 返 SlotValues。
- `OperatorDef`（`OperatorDef.java:22-46`）：type/category/displayName/inputSlots/outputSlots/`paramSchemaJson`（JSON Schema draft-07，前端自动渲染表单）/errorPolicy。
- `SlotType`（`SlotType.java:20-62`）：STRING/INT/DOUBLE/BOOL/VECTOR/STRING_LIST/CANDIDATE_LIST/CANDIDATE_LIST_MULTI(variadic)/SCOPE/QUERY_UNDERSTANDING/CONTEXT_PACK。
- `ExecContext`（`ExecContext.java:16-56`）：per-request 共享态（requestId/domain/channel/debug + attributes + nodeTraces）。
- `ErrorPolicy`：FAIL_FAST / SKIP_WITH_EMPTY（retrieve 默认，单路失败不破融合）/ FALLBACK。

### 4.2 引擎（engine 包）

- `ParadigmCompiler`（`ParadigmCompiler.java:32-349`）：JSON→ParadigmGraph，**七项编译期校验**：①node existence + param schema ②edge slot-type 兼容 ③slot occupancy（非 variadic 入槽至多一条边）④acyclicity（Kahn 拓扑）⑤terminal output 存在 ⑥required-input completeness。错误聚合成 `ParadigmCompileException`。
- `ParadigmExecutor`（`ParadigmExecutor.java:42-205`）：拓扑波次调度，每波 ready 节点并行（虚拟线程），`runNode` **显式 `DomainContext.set(ctx.domain())` + `llmClient.setKnowledgeDomain`**（line 139-140——对照 SearchService 的缺失）。
- `OperatorRegistry`（`OperatorRegistry.java:20-58`）：构造注入 `List<Operator>` 全部 bean，按 type 建索引；**type 重复启动直接抛 IllegalStateException**。

### 4.3 19 个算子

| Category | type | 入槽→出槽 | 职责 |
|---|---|---|---|
| input | `request_input` | ()→query | 暴露请求 query 为 slot |
| query | `query_understanding` | query→understanding | LLM-first QU |
| query | `query_embed` | query→queryEmbedding | embed(query) |
| query | `hyde` | query→queryEmbedding | HyDE 嵌入（与 query_embed 出槽同名可互换）|
| query | `multi_query` | query→variants | ⚠️ **当前无下游消费者** |
| retrieve | `fts` | query+scope→candidates | 三级降级 FTS |
| retrieve | `dense_vector` | queryEmbedding+scope→candidates | textKind 参数过滤 unit_type；直接选 text 不延迟 hydrate |
| retrieve | `entity_exact` | understanding+scope→candidates | JSONB @> 精确匹配 |
| retrieve | `entity_graph` | understanding+scope→candidates | 本体图召回（domain 从 ctx 取）|
| retrieve | `graph_expand` | seeds+scope→candidates | 段落关系 BFS 邻居 |
| fuse | `identity` | candidates→candidates | 透传 |
| fuse | `rrf` | candidates(MULTI)→candidates | 等权 RRF |
| fuse | `weighted_rrf` | candidates(MULTI)→candidates | 加权 RRF |
| rerank | `score_rerank` | candidates→candidates | 分数降序，永不失败 |
| rerank | `model_rerank` | candidates+query→candidates | cross-encoder |
| rerank | `llm_rerank` | candidates+query→candidates | LLM listwise |
| scope | `scope_resolve` | ()→scope | 解析 active release/build/snapshots |
| output | `assemble` | candidates+understanding+scope→contextPack | 复用 ContextAssembler，**生产终点** |
| output | `collect` | candidates→candidates | 截断 maxItems，**测试终点**（MRR/NDCG）|

### 4.4 ENTRY_SLOTS 只有 query（CLAUDE.md 论断属实）

`ParadigmCompiler.java:41-42`：
```java
static final Map<String, SlotType> ENTRY_SLOTS = Map.of("query", SlotType.STRING);
```
注释（line 36-40）明确：`scope` 故意不放进去——executor 只在 entry inputs 塞 `query`，若 scope 也列为 entry slot，required scope 的图会「编译通过但运行时 scope 为 null 静默检索不到东西」。所以 `scope_resolve` 必须显式连一条边把 scope 喂给 retrieve 算子。`checkRequiredInputs` (line 312-329) 是守门员。

### 4.5 ParadigmService（CRUD + 版本）

- 表：`operator_paradigm`（mutable draft + current_version）+ `operator_paradigm_version`（immutable published snapshot，uq(paradigm_id, version)）。
- **publish 流程**（`ParadigmService.java:105-132`）：先 `compiler.compile(draft)` 验证 → 新 version=`max(existing)+1`（不是 `current+1`，避免 rollback 后撞唯一约束）→ insert version + update paradigm。
- **表归非路由 defaultDataSource**：mapper 明说「callers invoke these with no DomainContext set」，DDL 由 `ParadigmSchemaInitializer` 启动时跑。

### 4.6 范式 vs 内置检索：完全独立、互不调用

- `/api/v1/search` → SearchService 写死管线，**完全不经过 operator/engine/paradigm 任何代码**。
- 范式执行 → `/api/v1/paradigm/**` → ParadigmExecutionService → ParadigmExecutor → 各 Operator。
- **底层共享**：operator 复用 application 层现成 bean（QueryUnderstandingEngine/EmbeddingClient/FtsRetriever/EntityGraphRetriever/GraphExpander/ContextAssembler），所以检索逻辑共享，但编排层各走各的。`AssembleOperator` 直接调 `ContextAssembler.assemble`，两条路径产物结构一致。

API：`GET /api/v1/operator/catalog`、`POST /api/v1/paradigm/run`（inline）、`/validate`、CRUD + `/{id}/publish`/`rollback`/`search`/`dryrun`。

---

## 5. 域路由与配置

- **路由机制**：`DomainRoutingDataSource`（`DomainRoutingDataSource.java:19-48`）extends AbstractRoutingDataSource，**完全重写 `determineTargetDataSource()`**：读 `DomainContext.get()`，null→defaultDataSource，否则 `poolManager.getDataSource(domain)`。
- **建池**：`DomainPoolManager`（`DomainPoolManager.java:32-175`）按 domain lazy 建池。registry 的 `database:` 块 `isUsable()`（有 host+dbname，`DatabaseConfig.java:26-29`）就建专用 Hikari 池，**建池即 `getConnection().isValid(3)` 验连**，连不上抛 `domain_database_unavailable`（503）。无 database 块回落 defaultDataSource + schema ensure。
- **reload**：`invalidate()` 比较 `poolSignatures`，只关签名变化的池。
- **配置来源**：`ConfigReloadService.reload()`（line 65-81）**先试 main_control HTTP** `GET {control}/api/v1/serving-config`，失败回落本地文件。两路解析同一组键（`MainControlClient.parseDatabase` 与 `ConfigReloadService.parseDatabase` 字段一一对应），契约由 `MainControlClientTest` 锁。
- **配置生效**：人工热重载端点已下线；修改配置后由“一键重启后台服务”触发启动期重新加载。
- **schema 自动初始化**：范式表由 `ParadigmSchemaInitializer` 在 defaultDataSource 跑；serving 运行时表（query_logs/cache）由 `ServingRuntimeSchemaInitializer` 实现 `DomainSchemaEnsurer`，启动跑一次 + 每个新域池建好后 `DomainPoolManager.ensureSchema` 再跑一次（**按域路由，每个域的库都建**）。

---

## 6. 已知 bug 与坑

### 🟡 语义缓存污染（TODO 文档已记）

- `SemanticCacheService.store`（line 57-68）仅 `queryVector == null` 才早退，**空 pack / 降级 pack 也写缓存**；`SearchService.search:395` 调用点无 guard。
- 命中条件（`SemanticCacheMapper.xml:6-20`）：同 domain + 同 release_id + 未过期 + cosine≥0.92 → 同 query 必然命中。
- `evict(domain)` 已实现但**零调用点**。
- 触发边界：**embedding 可用、整体结果为空**时触发；llm_service 完全挂（embedding 也挂）时 `queryEmbedding==null` → 早退 → 不写 → 不触发。

### 🟡 entity_graph 在 /api/v1/search 恒返回空（特定配置下）

- `EntityGraphRouteRetriever.retrieve`（`EntityGraphRouteRetriever.java:42-50`）第一行 `String domain = DomainContext.get(); if (null/blank) return List.of()`。
- `SearchService.search` 在主线程设了 `DomainContext.set`（line 129），但下游用虚拟线程（line 62）派发，**虚拟线程不继承 ThreadLocal**：
  - 变体检索 `runAsync`（line 294）+ 子查询检索 `runAsync`（line 323）：**都没 `DomainContext.set`/`wrapRunnable`**。
  - RetrievalOrchestrator 的 parallel 分支用 `DomainContext.wrapRunnable`（`RetrievalOrchestrator.java:178-182`）兜底；但 **sequential 分支（routeConfigMap.size()≤1）裸跑无 wrap**。
- **触发条件**：routePolicy 把 entity_graph 配进 ≤2 路方案（走 sequential）时必现；默认 complex 4 路 parallel 分支由 wrapRunnable 兜底，不触发。
- **对照**：`ParadigmExecutor.runNode` 显式 set 了（line 139-140），范式执行**无此 bug**。
- 修复：SearchService 两个 `runAsync` lambda 包一层 `DomainContext.wrapRunnable`。

### 🟡 AssetRawSegmentMapper.selectWithMeta 行放大

- `AssetRawSegmentMapper.xml:23-54` 的 `LEFT JOIN asset_document_snapshot_links` 是 1:N，**无 DISTINCT** → 同 raw_segment id 因多 relative_path 返回多行。
- ContextAssembler 已三处去重（`buildSourceItems` 按 seg.id 跳过已见 / assemble 末尾按 item.id / EvidenceGroup 用 itemIdSet）。契约 `ContextAssemblerTest.ItemDeduplication` 锁住。
- **新消费者须自去重**：当前 GraphExpander 用 `expandedNodes.containsKey(seg.getId())` 变相去重，暂未爆。

### 🟡 multi_query 算子孤立

- `MultiQueryOperator.java:14-16` 注释明说「当前 operator set 没有 STRING_LIST 下游消费者」——引擎层把变体 fan-out 到并行检索是未来 feature，当前挂这个算子没效果。

### 🟡 QU/Rerank 降级信号不阻断缓存写入

- `understanding.source=="rule"` 与 rerank 走 score 兜底时，`SemanticCacheService.store` 不读这些信号，仍写缓存。

### 🔴 BUILTIN_ROUTES 死代码

- `RetrievalRouter.java:42-72` 整段从未被读取，`route()` 只用 COMPLEXITY_ROUTES。

### 🔴 其他死代码

- `ScenarioPackReader` 旧实现被 main_control HTTP 取代（对应 `GlobalExceptionHandler.java:49-54` 的 `scenario_pack_missing` handler 保留但无人抛）。
- `AssetRetrievalEmbeddingMapper.selectWithUnitMeta`（xml:29-51）注释「text_kind filter omitted temporarily」，无调用方。
- `docs/pipeline-0X-*.md` 全套 + `docs/code_guide.md`：过时，部分描述已删除的 v4 `agent_serving_zdy`。

---

## 三色清单

### 🟢 工作正常
- `/api/v1/search` 主流程（QU/HyDE/MultiQuery 并行 → Router → Orchestrator → Fusion → hydrate → Rerank → Assemble）
- LLM-first QU + 规则兜底
- 三级 FTS 降级（tsvector→trigram→LIKE）
- Dense vector 三级 scope/section 降级
- Entity exact JSONB @> 精确匹配
- Tree navigation 章节推断（无 LLM 启发式）
- GraphExpander BFS 关系扩展（RST 优先级 + TOTAL_BUDGET=20）
- Cascading rerank + 文本 Jaccard 去重 + 低价值类型降权
- ContextAssembler seed/source/expand 去重 + tree-nav 排序 + 矛盾检测 + 3000t 压缩
- 域路由（DomainRoutingDataSource + DomainPoolManager 懒建池 + 建池即验连）
- 配置中心化（main_control HTTP → 本地文件兜底）
- **算子范式系统**（19 算子 + 编译器 7 项校验 + 拓扑波次执行 + CRUD/版本/发布）
- 范式执行的 DomainContext 显式 set（无 ThreadLocal bug）
- semantic cache 命中即跳过下游全链

### 🟡 有已知 bug / 降级
- 语义缓存污染：降级/空结果写入 cache，恢复后 24h 内仍命中空结果；`evict` 零调用
- entity_graph 在 /api/v1/search 恒空（特定路由配置下）：变体/子查询 runAsync 漏 DomainContext.set/wrapRunnable
- AssetRawSegmentMapper.selectWithMeta 行放大：依赖消费者去重
- multi_query 算子孤立：STRING_LIST 输出无下游消费者
- QU/Rerank 降级信号不阻断缓存写入

### 🔴 死代码 / 恒失效
- `RetrievalRouter.BUILTIN_ROUTES`：定义后从未被读取
- `ScenarioPackReader` 旧实现、`selectWithUnitMeta`、`scenario_pack_missing` handler
- `docs/pipeline-0X-*.md` + `docs/code_guide.md` 过时
