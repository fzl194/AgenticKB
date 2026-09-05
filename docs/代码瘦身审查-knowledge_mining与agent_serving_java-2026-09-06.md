# Knowledge Mining 与 Agent Serving 代码瘦身审查

> 审查日期：2026-09-06
>
> 审查基线：`master`，HEAD `a74b940`
>
> 审查范围：`knowledge_mining`、`agent_serving_java`，并交叉核对 `kb-ui`、`mcp_server`、`main_control_service` 和部署脚本中的真实消费者。
>
> 本次只做静态审查。未修改、移动或删除任何源码、配置、数据库文件和生成物。

## 1. 执行结论

当前生产主链已经比较明确：

- 挖掘侧正式目录为 9 个算子：`input_ingest`、`document_parse`、`segment_compile`、`retrieval_unit_project`、`embedding`、`query_expansion_generate`、`hierarchical_summary_generate`、`asset_persist`、`mining_finalize`。
- 检索侧正式目录为 8 个算子：`scope_resolve`、`query_embed`、`fts`、`dense_vector`、`rrf`、`model_rerank`、`evidence_hydrate`、`assemble`。
- 新增知识库挖掘只走 workflow；检索只走已发布范式。实体、本体和旧固定检索链没有进入正式产品入口。

但代码仓库尚未围绕这两条主链完成收口，主要有五类冗余：

1. **确定不可达的代码**：没有挂载的 FastAPI Router、没有注册的 Operator、没有调用者的类和函数。
2. **框架仍会加载、但没有消费者的代码**：Spring Bean、MyBatis Mapper 和启动期配置缓存仍在创建，但没有请求链使用。
3. **已经被新链替代的并行实现**：两套文件管理、两套 FTS/Dense 检索器、旧全文下钻和新 Evidence 读取同时存在。
4. **只能在完成数据迁移后删除的兼容层**：历史 legacy Run、旧 workflow、旧本地 `storage_path`、旧 cursor/ref。
5. **本地生成物和打包残留**：`__pycache__`、Java `target`、打包 staging、旧压缩包和疑似凭据副本。

最值得优先处理的不是体积最大的 legacy 引擎，而是：

- 无任何生产入口的叶子代码；
- 仍被 Spring 创建但不提供产品能力的旧 Bean；
- 没有 KB 资源权限的旧全局 API；
- 与生产 DDL 不一致的代码内 schema 副本。

Legacy Run、文件迁移、旧 ref/cursor 等代码虽然名称陈旧，但当前仍承担历史数据恢复职责，不能在第一批直接删除。

## 2. 判定方法与置信度

本次不是简单执行“全文搜索零引用即删除”，而是依次核对：

1. Docker 和 Supervisor 的真实启动命令；
2. FastAPI `include_router`；
3. workflow catalog、template、compiler 和 handler registry；
4. Spring component scan、`List<Operator>` 自动注册；
5. MyBatis `@MapperScan`、XML namespace 和 XML statement；
6. UI、MCP、主控服务和部署脚本调用；
7. 动态 import、配置驱动和历史 Run 分流；
8. 测试专用实现与生产 Protocol 的关系。

报告使用三种置信度：

| 等级 | 含义 | 建议 |
|---|---|---|
| A：高置信 | 生产入口不可达、无动态注册、无仓内消费者 | 可进入第一批删除变更 |
| B：高置信空转 | 框架会加载或接口可访问，但没有第一方消费者 | 先移除装配或注销入口，再删除实现 |
| C：需验证 | 可能有外部 API、历史数据或运维用途 | 查访问日志和数据库后再处理 |

## 3. 生产可达主链

### 3.1 挖掘

```text
docker/supervisord.conf
  → python -m knowledge_mining.mining.api
  → mining/api/app.py:create_app + lifespan
  → KB mine 路由
  → WorkflowRunBinder
  → Domain Run Queue
  → jobs.run（按持久化 execution_engine 分流）
  → MiningWorkflowRuntime
  → builtin_handler_registry
  → 9 个正式算子
```

新建 KB Run 固定写入 `execution_engine="workflow"`。`jobs/run.py` 仍保留 legacy 分支，是为了恢复、发布或结束历史 Run，而不是新业务继续使用旧链。

### 3.2 检索

```text
MCP / Web
  → ParadigmController
  → 已发布 Paradigm
  → ParadigmCompiler
  → ParadigmExecutor
  → OperatorRegistry
  → 8 个正式 Operator
  → EvidenceResponse
```

`OperatorRegistry` 通过 Spring 注入 `List<Operator>` 自动收集 Bean，因此带 `@Component` 的 Operator 不能仅凭“没有 new”判断为死代码。

## 4. Knowledge Mining 高置信清理候选

### 4.1 未挂载的本体、实体和图 HTTP Router

候选：

- `knowledge_mining/mining/api/routes/ontology.py`

证据：

- 文件定义了 `/api/ontology/*`、`/api/mentions/*`、`/api/graph/*` 等 14 个端点；
- `mining/api/app.py` 没有 import，也没有 `include_router`；
- 只有专属测试手工挂载；
- 另有产品面测试明确要求生产应用不存在这些路由。

建议：删除 Router 和只验证该 Router 的测试。如果需要保留研究能力，应迁到独立 research 工作区。不要因此连带删除 `ontology_store`、ontology DDL 和 Java ontology Mapper：历史 legacy Run 和 Java 研究代码仍可能使用这些底层资产。

置信度：A。

### 4.2 未切流的并行 File Management 实现

候选：

- `knowledge_mining/mining/file_management/router.py`
- `knowledge_mining/mining/file_management/file_service.py`
- `knowledge_mining/mining/file_management/service.py`
- 对应的纯 Router/Service 测试

合计约 1,552 行。

证据：

- Router 文件自身注明未挂载生产；
- 生产 `app.py` 不包含该 Router；
- Service 只被未挂载 Router 和测试调用；
- 当前产品实际入口是 `kb/routes/documents.py → kb/services/document_service.py`。

边界：不能整包删除 `file_management`。其中 contracts、PG repositories、StorageObject 和 DocumentCurrentContent 实现仍被新解析链、对象存储和快照读取使用。应先拆除 upload-session/FileManagementService 专属部分。

置信度：A。

### 4.3 没有路由装饰器的孤儿函数

候选：

- `api/routes/runs.py:get_run_document_relations()`
- `api/routes/knowledge.py:list_relations()`

证据：函数没有 `@router.*`，也没有 Python 调用者；相关测试反而断言旧关系路径不存在。

置信度：A。

### 4.4 已被 Java `doc_` 通道替代的 Mining MCP 端点

候选：

- `POST /api/kb/mcp-tools/get-document`

证据：端点仍在 `kb/routes/mcp_tools.py` 注册，但 `mcp_server/tools.py` 已明确注明不再调用。当前文档读取走 Java `/api/internal/document/{ref}`。

建议：删除该端点及专属分支；保留 `list-kbs`、`list-documents` 和 `upload`。

置信度：A。

### 4.5 正式 registry 不可达的 workflow research 壳

候选：

- `workflow/operators/research.py`
- `workflow/handlers/research.py`
- `workflow/handlers/global_nodes.py` 中仅由 research 导出的 handler

这些文件约 211 行。

证据：

- `builtin_catalog()` 不包含实体、本体和 graph_write；
- `builtin_handler_registry()` 不注册 research map；
- 新 workflow compiler 会把这些类型作为未知算子拒绝；
- 只有隔离契约测试直接 import。

建议：从生产 runtime 包移走。若未来还要继续研究，保留在独立 research 目录或分支，不要让正式产品包承担维护成本。

置信度：A。

### 4.6 空范式模块和纯兼容空壳

候选：

- `workflow/paradigms.py`：只有退役说明，没有任何符号；
- `workflow/templates.py:builtin_templates_v2()`：没有调用者，仅转发 `builtin_templates()`；
- `workflow/normalizer.py:required_protected_types()`：恒返回空；compiler 仅保留未使用 import。

置信度：A。

### 4.7 无消费者的旧 Stage Registry 与 Eval

候选：

- `stages/eval.py`
- `stages/__init__.py` 中 `_auto_discover()`、`get_stage()`、`list_stages()` 和对应 registry/decorator

证据：

- Eval 使用旧 SQLite `AssetCoreDB(path)/open()` 形态；
- 无生产调用，当前也没有有效测试消费者；
- `/api/config/stages` 返回硬编码列表，并不读取该 registry；
- auto-discover 会在 import 任一 stage 时额外加载旧 parse/enrich/relation/eval/publishing 模块。

建议：先删除 Eval 和 auto-discover，再逐个移除只为 registry 存在的 decorator。不要因此删除正式 workflow handler 直接使用的实现。

置信度：A。

### 4.8 未接入生产的旧 ParserRouter

候选：

- `file_inspector/router.py`
- `file_inspector/__init__.py` 中 `ParserRouter/RouteDecision` re-export

生产解析实际由 `parse_adapters.factory` 和 `new_chain_services` 选择 Adapter；`parse_operator` 只直接使用 `FileInspector`，没有调用该 Router。

若不存在仓库外 Python 调用方，可以连同专属测试删除。

置信度：A/B。

### 4.9 已被当前预检实现替代的独立 `preflight.py`

候选：

- `knowledge_mining/mining/preflight.py`

其中 `TargetWorkflow`、`classify_preflight_matches()`、`build_run_preflight()` 全仓无调用。当前 Run 使用持久化 `preflight_manifest_json` 和新的 KB 增量判定逻辑，并不 import 此模块。

置信度：A。

### 4.10 与生产 DDL 不一致的 Python schema 副本

候选：

- `retrieval_projection/schema.py` 中 `ASSET_SCHEMA_V2_STATEMENTS`
- `ensure_asset_schema_v2()`

问题：

- 只被测试/开发辅助使用；
- 生产 schema 真相源已经是 `databases/asset_core/schemas/013*.sql`；
- 代码副本中 `asset_raw_segments(snapshot_id...)` 与生产列 `document_snapshot_id` 不一致。

建议：删除 statement 集与 deprecated helper；保留生产运行仍使用的 schema/tokenizer 版本常量和 promote 列定义。

置信度：A。

## 5. Knowledge Mining 需要迁移后删除的范围

### 5.1 Legacy execution engine

涉及：

- `jobs/run.py` 中 `_run_legacy/_publish_legacy/_resume_legacy`；
- `pipeline.py`；
- `snapshot/`；
- 旧 stages：parse、segment、enrich、relations、retrieval_units、image_caption、entity、resolve、graph_write、ontology_induction；
- legacy `infra/docx_parser.py`、`infra/pdf_parser.py` 和部分目录 ingestion。

不能立即删除的原因：`jobs.run` 会按数据库中的 `execution_engine` 恢复历史 Run，API 的 publish/resume 仍支持这些存量记录。

删除门槛：

1. 所有域不存在 queued/running/awaiting_review/interrupted legacy Run；
2. 决定历史 legacy Run 是否只读保留；
3. 执行并审计 `workflow/v2_cutover.py`；
4. `demo_run.py` 改走 workflow 或删除；
5. 完成历史发布、恢复和删除场景回归。

置信度：C。

### 5.2 多注册的 `parse_segment` handler

当前 catalog 已不包含 `parse_segment`，WorkflowService 也拒绝创建旧图，但 `DOCUMENT_HANDLERS` 仍注册它，导致正式 handler 模块顶层 import 整个 legacy `pipeline.py`。

删除门槛：所有 active/draft workflow 和冻结 Run manifest 均已升级为 schema 2.0，且不再包含 `parse_segment`。

之后可删除：

- `parse_segment_handler`；
- handler registry entry；
- `ParseSegmentOptions`；
- 仅覆盖旧 handler 的测试。

### 5.3 启动期 `v2_migration.py`

`app.lifespan` 每次启动仍执行 `upgrade_active_workflows_to_v2()`，说明迁移尚未被正式宣布完成。

所有 active/draft workflow 和冻结 manifest 完成清点后，再删除启动迁移及相关兼容测试。

### 5.4 本地 `storage_path` 与 `file_migration/`

`file_migration/` 没有生产组合入口，但它仍是本地文件迁移到对象存储的一次性工具。`DocumentService` 的下载、元数据、删除和文件夹移动仍保留 `storage_path` 回退。

删除门槛：

- 所有存量文档都有 `storage_object_id`；
- 运行期没有本地 fallback 命中；
- 已完成并验收一次性迁移；
- 下载、删除、恢复和移动均只使用对象存储。

### 5.5 `/api/knowledge` 旧全局读取面

`/api/knowledge/stats` 被系统状态页面使用，必须保留。

其余 documents、batches、segments、units 等接口没有第一方 UI/MCP 消费者，部分仍读取旧 `asset_retrieval_units`，而正式链只写 `asset_retrieval_units_v2`。这些接口虽然已注册，但已不符合当前 KB 隔离模型。

建议：查询生产日志和外部报表客户端。无调用后注销；仍有调用则迁移到 KB-scoped v2 API。

### 5.6 `/api/config/stages`

端点返回硬编码 legacy 阶段，包括正式 catalog 已不存在的 enrich、discourse、旧 retrieval builder。它既不读取 stage registry，也不反映当前 9 个算子。

建议：若无外部调用直接退役；若保留，则后续改为读取正式 catalog。当前任务不新增功能，因此本轮只列为删除候选。

### 5.7 `architecture.html`

文件描述旧 Mining Pipeline v2 和 GraphRAG stage registry，但 `knowledge_mining/README.md` 仍链接它。应先移除或替换 README 链接，再删除该 HTML，避免文档断链。

## 6. 应优先退役的 Mining 并行 API

这些代码不是不可达，而是“仍可访问、没有第一方消费者、权限模型落后”。风险高于普通死代码。

### 6.1 全局 Document Lifecycle Router

文件：`api/routes/document_lifecycle.py`

端点以 `domain/document_id` 操作下载、删除和批次撤回，没有按照 KB membership/owner 做资源授权；当前 KB 文档路由已经提供带 ACL 的等价入口。

建议顺序：

1. 从 FastAPI app 注销；
2. 查询生产访问日志；
3. 验证 KB 删除、restore、批次撤回的替代路径；
4. 再删除 Router，底层生命周期 Service 暂保留。

置信度：B，高优先级。

### 6.2 `/api/builds`、`/api/releases`、`/api/config`

仓内无 UI/MCP 消费者：

- build/release 接口会暴露域内文档和 snapshot ID；
- config 接口会暴露内部主机、数据库名和服务地址。

删除前必须检查运维脚本和外部 API 客户。如果仍保留，应限制为管理员使用，而不是普通认证用户可读。

置信度：B/C。

## 7. Agent Serving 高置信清理候选

### 7.1 完全没有生产或测试引用的叶子类

以下类没有 Spring 注解、没有 Bean、没有 MyBatis XML 引用，也没有生产调用者：

- `domain/ServingConstants.java`
- `domain/TreeNavigation.java`
- `infrastructure/PgConfig.java`
- `repository/SchemaAdapter.java`
- `rerank/ServiceReranker.java`
- `mapper/result/ExpandedSegmentRow.java`
- `entity/AssetDocument.java`
- `entity/AssetRawSegment.java`
- `entity/AssetRawSegmentRelation.java`
- `entity/AssetRetrievalEmbedding.java`
- `entity/AssetRetrievalUnit.java`

`domain/SourceRef.java` 也没有生产引用，只有一条自证 record 默认值的测试，可以连同该测试删除。

以上 12 个文件约 648 行。

置信度：A。

### 7.2 明确退役的资源

候选：

- `src/main/resources/prompts/query-understanding-system.txt`：没有资源加载者，仍硬编码旧意图和云核心网实体；
- `src/main/resources/db/migrate_v2_semantic_cache.sql`：没有执行者，runtime initializer 已注明语义缓存随固定链删除。

置信度：A。

### 7.3 无调用的 Mapper 方法和 XML statement

高置信候选：

- `ParadigmMapper.selectDefaultByDomain/clearDefaultForDomain/updateBinding`；
- `AssetRetrievalUnitMapper.fetchDetailsByIds` 及无 scope 的 XML resultMap；
- `AssetRawSegmentMapper.selectSectionPathsByEntities`；
- `AssetRepository.resolveSegmentsByIds/getRelationsForSegments/getDocumentSources/getNeighbors`；
- `AssetRawSegmentRelationMapper.java+xml`、`RelationRow`、`NeighborRow`；
- `AssetDocumentMapper.selectDocumentSources` 和 `DocumentSourceRow`；
- `SegmentWithMetaRow`、`ExpandedSegmentRow` 等仅服务旧 relation expansion 的结果类型。

当前 Evidence hydrate、structure navigate 和 document read 已经使用 `EvidenceSourceV2Mapper` 与 `StructureToolMapper`。

删除时必须同步修改 Java interface 与 XML，避免留下 namespace 合法但 statement 悬空的半套 Mapper。

置信度：A。

## 8. Agent Serving 被生产装配但没有消费者的代码

### 8.1 旧 v1 FTS/Dense Retriever Bean

`ServingBeans` 仍创建：

- `FtsRetriever`
- `DenseVectorRetriever`
- `EntityExactRetriever`

其中 Fts/Dense 没有注入消费者；正式 `FtsOperator` 和 `DenseVectorOperator` 已经直接调用 `AssetRetrievalUnitV2Mapper`。

建议成组删除：

- Fts/Dense Retriever；
- 对应 `@Bean`；
- 只服务旧 Dense 的 `AssetRetrievalEmbeddingMapper.java+xml` 和 `EmbeddingRow`；
- 只服务旧 FTS 的 stopwords 资源；
- 对应旧 IT。

`AssetRetrievalUnitMapper` 不能整文件删除，`AssetRepository` 和旧全文兼容面仍使用其中部分方法。应先做方法级瘦身。

置信度：B，高。

### 8.2 实体研究线隔离不彻底

- `EntityExactOperator`、`EntityGraphOperator` 没有 `@Component`，不进入正式 8 算子目录；
- 但 `EntityExactRetriever` 被 `ServingBeans` 显式创建；
- `EntityGraphRetriever` 仍带 `@Component`；
- `OntologyGraphMapper` 和 XML 因 MapperScan 进入生产装配。

它们没有请求消费者，却仍增加启动依赖并让“research 已隔离”的事实不完整。

建议：先移除 Bean、`@Component` 和生产 Mapper 扫描入口。若仍需研究，将源码和测试迁到 research profile/source set；不要仅隐藏 Operator 而保留完整运行时装配。

置信度：B，高。

### 8.3 写入但从不读取的旧 Domain Profile 缓存

`ConfigReloadService` 每次启动和 reload 都调用 `DomainPackReader.apply()`，但整个 production main 没有调用 `getProfile()`。`ServingDomainProfile` 中 route policy、extractor rules、query understanding、intent strategy 只被写入缓存，不参与当前范式执行。

建议按顺序处理：

1. 从 `ConfigReloadService` 移除 `DomainPackReader` 依赖和 apply；
2. 删除 `DomainPackReader`、`ServingDomainProfile` 及专属测试；
3. 瘦身 `ServingConfigSnapshot.DomainConfig.serving`；
4. 删除 local fallback 中 `loadServingBlock()`。

保留 `DomainRegistry`、`DomainPoolManager`、`DomainRoutingDataSource`，它们仍是数据库按域路由的核心。

置信度：B。

## 9. Agent Serving 需确认外部消费者后退役

### 9.1 旧全文和原件 HTTP API

仍可访问：

- `POST /api/v1/segments/fulltext`
- `GET /api/v1/documents/{documentId}/raw`

仓库内现状：

- fulltext 没有 UI/MCP 调用者；
- raw 只有 `kb-ui/api/serving.ts:downloadRawFile()` 封装，但没有组件调用；
- 当前网页证据展开走 `/api/v1/evidence/{ref}`；
- 当前 MCP 走 `/api/internal/evidence` 和 `/api/internal/document`；
- KB 文件下载走 Mining KB 文档接口。

因为它们是公开 HTTP 路由，可能存在仓外调用，不能直接按死代码删除。应先查访问日志并发布退役窗口，再成组删除：

- `FullTextController`；
- `FullTextService`；
- `RawFileService`；
- FullText Request/Response、SegmentWindow、DocumentFileRow 等 DTO；
- `AssetRepository` 和 Mapper 中仅服务该链的方法；
- 对应 WebMvc、Service 和 PG IT。

置信度：C。

### 9.2 旧 `AssetRetrievalUnitMapper` 搜索面

`searchByFts/Trigram/Like/*WithScope/searchByEntityExact` 只服务旧 Retriever 或旧 IT。待旧 FTS、EntityExact 和 FullText 链退出后，将 Mapper 缩到仍在使用的方法，或由 V2 Evidence/Structure Mapper 完全替代。

不要提前删除 `fetchDetailsByIdsInScope`，当前 FullTextService 仍使用它。

### 9.3 旧范式域绑定字段和 DDL

业务代码已不调用旧域绑定方法，但：

- Paradigm 查询仍读取 `bound_domain/is_default/bound_at`；
- Entity 仍承载字段；
- SchemaInitializer 启动时仍执行 `002_paradigm_domain_binding.sql`。

应先清理 Mapper select 列、Entity 字段和旧方法，再停止加载 DDL，最后执行数据库列/索引迁移。不能先删除数据库列。

### 9.4 QueryUnderstanding DTO 族

正式查询理解算子已删除，但 `ModelRerankOperator` 为调用 `LlmServiceReranker` 仍人工构造 `QueryUnderstanding/EvidenceNeed`，使旧 DTO 和 `QUERY_UNDERSTANDING` SlotType 继续滞留。

先把 reranker 接口收敛为 `rerank(query, candidates)`，再删除或迁出 `QueryUnderstanding`、`EntityRef`、`SubQuery`、`EvidenceNeed` 和相关 SlotValues 入口。`ScoreChain` 仍被正式融合使用，必须保留。

### 9.5 SearchRequest

当前只由 `QueryLogAspect` 构造后传给 `QueryLogService`，不再是 HTTP Search DTO。建议日志服务改收 `RunArgs` 或专用日志输入后再删除。

### 9.6 自定义 HealthController

项目已经依赖 Spring Actuator，但又手工实现 `/actuator/health`。部署与 UI 依赖该 URL，需先验证标准 Actuator 响应、安全策略和测试等价，再删除自定义 Controller。

### 9.7 旧 LLM 模板注册副作用

当前重排使用 `LlmServiceReranker → /api/v1/models/rerank`，不使用模板执行；但 `ServingBeans` 仍启动后台线程注册 `serving-reranker` 模板。`LlmClient.execute()` 的唯一生产调用者是已死的 `ServiceReranker`。

删除顺序：

1. 删除 `ServiceReranker`；
2. 删除 `LlmClient.execute/ensureTemplates*`；
3. 删除 `ServingTemplates`；
4. 删除启动期 template-register 线程；
5. 调整旧 integration test。

## 10. 前端与 MCP 交叉发现

以下 wrapper 只有定义，没有生产调用：

- `useServingApi.search()`：仍请求已经不存在的 `/api/v1/search`；
- `useServingApi.downloadRawFile()`；
- `useKbApi.uploadZip()`；
- `useMiningApi.getRunArtifacts()`；
- `useOperatorApi.getMcpCatalog()`；
- `useOperatorApi.validateDraft()`；
- `useOperatorApi.dryRun()`，页面的“试运行”实际调用 `runInline()`；
- LLM UI 的 `getTaskRequest/getTaskResult/getTaskAttempts/getTaskEvents()`。

这些前端 wrapper 可以在对应后端退役批次一并删除。LLM 后端端点仍可能被 Mining 调用，不能因为 UI wrapper 未使用而删除后端。

以下不是死代码：

- `EvidenceApiController`：`KbSearchPanel → getEvidenceFull()` 正在使用；
- `/api/internal/evidence/document/inspect/navigate/structured-query`：MCP 正在使用；
- Paradigm mcp-catalog/resolve/search：MCP 正在使用；
- KB list/document/upload MCP 内部端点仍在使用，只有旧 get-document 已退出；
- `inspect/navigate/structured-query` 在本地 MCP 日志中存在真实调用记录。

## 11. 必须保留的核心范围

### Knowledge Mining

- `app.py` 当前挂载且仍有明确消费者的 Router；
- workflow catalog、templates、presets、compiler、runtime、executor；
- 9 个正式 handler 及 `new_chain_services`；
- `document_parse`、`segment_compile`、retrieval projection、embedding、persist、finalize；
- `parse_adapters`、`parse_quality`、`parse_reconciler`、`shadow_parse`、`snapshot_store`；
- 新链需要的 PG repository、object store 和 contracts；
- Memory repository 和 FakeObjectStore：虽然不是生产入口，但用于验证生产 Protocol，不能作为普通测试垃圾删除；
- `v2_cutover.py` 和 `file_migration/`：相关迁移完成前保留。

### Agent Serving

- 8 个正式 Operator；
- `OperatorRegistry`、`ParadigmCompiler`、`ParadigmExecutor`、SlotBinder；
- Paradigm Controller、Run Controller、Catalog Controller；
- Paradigm Service、核心 Mapper、Version Mapper、Seeder、SchemaInitializer；
- Evidence API；
- Internal Structure Controller 及 Evidence/Inspect/Navigate/StructuredQuery/Ref Service；
- `StructureToolMapper`、`EvidenceSourceV2Mapper`；
- `ScopeResolver`、`KbAccessService`、ActiveScope；
- `AssetRepository` 中仍在使用的 scope/fulltext 部分；
- `DomainRegistry`、`DomainPoolManager`、RoutingDataSource；
- `AssetRetrievalUnitV2Mapper`；
- `LlmServiceReranker`、`LlmClient.rerank()`、`EmbeddingClient`；
- Query Log、运行时 schema 和日志 Mapper；
- `runtime/agent_serving.jar`：容器实际运行制品。

## 12. 临时文件与生成物

### 12.1 可清理生成物

本次确认存在：

- 65 个 `__pycache__` 目录；
- 810 个 `.pyc`，约 12.24 MB；
- `agent_serving_java/target/`；
- 9 个 `.cmkb-pack-stage.*` 残留目录，约 112 MB；
- `nul`，48 字节；
- `sync-20260901-113324.tar.zst`，0 字节。

这些均不是源码。本次未删除。

注意：虽然 Java `target/` 可重建，但发布脚本会从 `target` 复制最新 jar。删除 target 后若直接执行同步，脚本可能继续沿用旧 `runtime/agent_serving.jar`。正确顺序是先 Maven 构建，再同步。

### 12.2 需要安全处置的历史包和凭据副本

确认存在：

- `.env copy`，1,015 字节；
- `AgenticKB.zip`，约 456.7 MB；
- `cmkb-1.0.0.tar.gz`，约 456.6 MB；
- `cmkb.tar`，约 407.8 MB。

这些文件可能包含历史配置或凭据，不能作为普通缓存随手删除，也不应继续分发。建议先确认当前部署不再读取 `.env copy`，检查压缩包内容并轮换可能暴露的凭据，再准备干净回滚包和安全销毁方案。

### 12.3 不能按生成物删除

- `.cmkb-sync-last/`：同步依赖变化基线；
- `runtime/agent_serving.jar`：当前运行制品；
- `kb-ui-dist/`：前端运行制品；
- `releases.json`：发布版本真相源；
- `kb-ui/auto-imports.d.ts`、`components.d.ts`：生成但已跟踪的编译类型入口；
- `logs/`：应按留存策略轮转，不应直接清空。

## 13. 建议清理批次

### 批次 A：纯叶子和生成物

范围：

- 无引用 Java POJO、常量、空壳和旧资源；
- 空 `paradigms.py`、孤儿函数、纯兼容 alias；
- `preflight.py`、`stages/eval.py`；
- `__pycache__`、`.pyc`、`target`、pack staging 和 0 字节临时包。

特点：不改变正式产品行为。

### 批次 B：注销不可达或空转生产装配

范围：

- 未挂载 ontology Router；
- 未挂载 File Management Router/Service；
- 旧 FTS/Dense Retriever Bean；
- Entity Retriever 的生产 Bean/Mapper 装配；
- stage auto-discovery；
- 重复 Python DDL；
- 已替代的 Mining MCP get-document。

特点：需要启动上下文、catalog 和 Mapper XML 回归，但不应改变正式功能。

### 批次 C：并行 API 退役

范围：

- 全局 Document Lifecycle；
- `/api/knowledge` 中除 stats 外的读取面；
- `/api/builds`、`/api/releases`、`/api/config`；
- Java fulltext/raw；
- 无第一方消费者的 Paradigm 辅助端点。

门禁：生产访问日志、外部客户端清单、弃用窗口、ACL 和回归测试。

### 批次 D：配置与 DTO 收口

范围：

- DomainPackReader/ServingDomainProfile；
- 旧 domain binding 字段和 DDL；
- QueryUnderstanding DTO；
- SearchRequest；
- LLM 旧模板注册；
- 自定义 HealthController。

### 批次 E：Legacy 数据与运行时拆除

范围：

- legacy Run 分支；
- parse_segment handler 和 v2 startup migration；
- local storage_path；
- file_migration；
- legacy pipeline、stage、parser 和 ontology 写入链。

这是最后一批，必须以真实数据库审计结果为前提。

## 14. 每批删除的验收门槛

每个清理批次至少需要：

1. `rg`、Python AST、Spring 注解和 MyBatis XML 四类静态检查；
2. FastAPI 生产路由快照测试；
3. Mining 9 算子 catalog、四套模板和 handler 闭合测试；
4. Spring Context 启动测试；
5. Serving Operator Catalog 必须仍恰好为正式 8 算子；
6. MyBatis interface/XML statement 一致性测试；
7. KB 上传、挖掘、发布、检索和证据展开回归；
8. MCP search/get/upload 与结构导航回归；
9. 生产访问日志零调用证据；
10. Legacy Run、旧文件和旧数据库字段迁移审计。

## 15. 推荐的下一步

下一步只做代码优化，不新增功能。建议先启动“批次 A + 批次 B”，但分成两个独立变更：

1. **纯叶子清理**：删除绝对无引用代码、空壳、旧资源和生成物；
2. **生产装配收口**：注销旧 Bean、未挂载 Router、research Mapper 和 stage auto-discovery。

暂不进入：

- legacy Run 全链删除；
- ontology 底层表和 store 删除；
- storage_path 回退删除；
- 对外 HTTP API 直接删除；
- 数据库列和迁移脚本删除。

在第一批开始前，应先固化两条机器可验证的不变量：Mining catalog 仍为 9 个正式算子，Serving catalog 仍为 8 个正式算子。之后所有清理都围绕这两条主链进行，避免在多轮删除中再次产生新的孤儿代码。
