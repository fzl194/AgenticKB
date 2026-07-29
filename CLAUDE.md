# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

CoreMasterKB — 面向 5G 核心网的领域知识库：把 3GPP 文档、内部 wiki 等原始资料**挖掘**成结构化知识资产，再对外**检索**。多个服务打包进单容器，由 supervisord 管理。

> 这份文档偏重「照着代码读会踩的坑」和「为什么是这样」，而不是复述目录树。凡是和源码冲突的旧文档，一律信源码。文末有一张文档可信度表。

---

## 架构：两条主线，经数据库耦合

理解这个系统的关键，是看清它有两条主线，**彼此从不调用 HTTP，只通过 PostgreSQL 交接**：

```
挖掘线 (Python)                              检索线 (Java)
knowledge_mining  :8901                      agent_serving_java :8081
  两套引擎，按 run 决定：                       查询理解 → 路由(按复杂度) → 范围解析
   · legacy   固定流水线 StreamingPipeline      → 树导航 → 多查询扩展 → 向量化
   · workflow 算子 DAG + 冻结 manifest          → 语义缓存 → 多路召回 → 融合
  build → publish release                      → 水合 → 重排 → 上下文组装
         │                                                    ▲
         └──── 写 asset_* 表 ──► PostgreSQL ──── 只读 ────────┘
```

`agent_serving_java` 对全部 `asset_*` 表**只读**（7 个 `Asset*` mapper + `OntologyGraphMapper` 全是 `<select>`；13 个 mapper XML 里只有 `ServingQueryLog`/`SemanticCache`/`Paradigm`/`ParadigmVersion` 有写），写方永远是 `knowledge_mining`。serving 自己只写 `serving_query_logs` / `serving_query_cache` / `operator_paradigm*`。

**发布语义**：mining 挖完的内容不会立刻可检索。必须 build 成不可变快照并 `publish` 成 release；`asset_publish_releases` 上有部分唯一索引 `WHERE status='active'` 保证「一个 **(domain, channel)** 至多一个 active release」。serving 只认 active release。这也是 `no_active_release` / `multiple_active_releases` 报错的来源。发布用 `acquire_domain_publish_lock(domain)` 的按域 advisory 锁串行化，`activate_release()` 在同一事务里退旧启新。

### ⚠️ 自上一版文档以来的重大变化（务必先读）

| 旧文档说 | 现状 |
|---|---|
| 挖掘线是固定线性流水线 | **新 run 默认走 workflow 算子 DAG**（`mining_config.py:39` 默认 `workflow`）。legacy 流水线仍在，作为回退引擎，按 run 的 `execution_engine` 列决定，二者永久共存 |
| mining 的 per-domain 分库没接通、inline `database:` 块「零读取」是死代码 | **已接通**：`mining/infra/domain_db.py:resolve_domain_database` 真的读 inline `database:` 块并按 conninfo 建池。真正的死代码是 `pg_config.py:conninfo_from_env`（仅测试引用） |
| `deploy-server.sh --force` 会 `rm -rf main_control_service/` 连带删配置 | **已修复**：`--force` 现在把宿主机 `main_control_service/config` 复制进暂存快照后再换入（`deploy-server.sh:502-506`），**保留配置**。只有 `--force-config`（或 config 目录不存在）才会用镜像版覆盖配置 |

### 其余服务（容器内 6 个 supervisord 程序）

| 服务 | 端口 | 启动优先级 | 职责 |
|---|---|---|---|
| `main_control_service` (control) | 8910 | 10（**最先**） | YAML 配置中心 + 域感知反向代理 |
| `llm_service` | 8900 | 20 | 统一 LLM 运行时，租约式任务队列（`FOR UPDATE SKIP LOCKED`） |
| `knowledge_mining` (mining) | 8901 | 30 | 挖掘线 |
| `agent_serving_java` (serving) | 8081 | 30 | 检索线 |
| `mcp_server` (mcp) | 9000 | 40 | 把检索包装成 MCP tool，**直连 8081，绕过控制面** |
| `kb-ui` (nginx) | 80 | 40 | Vue 3 前端，经 nginx |

启动顺序由 `docker/supervisord.conf` 的 `priority` 决定，`control` 必须最先（它是配置中心）。另有一个 `eval` 服务（域 registry 里配了 `eval_url:8810`，代理层 `SERVICE_MAP` 也认 `eval`）——但它**不在本仓库、不在这 6 个容器程序里**，前端也没用它，是个外部服务占位。

### 前端调用范式（唯一入口）

nginx 只有一条后端路由（`/api/control-plane/` → `127.0.0.1:8910`）。前端所有请求都长这样：

```
/api/control-plane/api/v1/proxy/{domain}/{service}/{真实后端路径}
    └── nginx/vite 剥掉 ──┘ service ∈ {mining, serving, llm}   ← 前端实际只用 3 个
```

`createProxyClient(service)`（`kb-ui/src/api/proxyClient.ts`）建的 axios **没有固定 baseURL**，在**每个请求的拦截器里**重算 `/api/control-plane/api/v1/proxy/{当前域}/{service}`（域从 `useDomainStore()` 实时读），所以切域无需重建客户端。`mining` 服务默认额外注入 `domain` query 参数（`includeDomainQuery:false` 可关，全局 workflow API 就关了它）。

例外：`controlPlane.ts` **直连**控制面（`baseURL=/api/control-plane`，不走 `/proxy/{domain}/{service}` 形状），用于系统配置 / 域 / scenario pack / 日志。reload 按钮则通过 proxy 打 `/proxy/{domain}/{service}/api/v1/admin/reload-config`。

`README.md` 里写的 `/api/mining`、`/api/serving`、`/api/llm` **已不存在**，是早期遗留。加新接口从 `kb-ui/src/api/*.ts` 入手。

### 配置来源（改配置前必读）

**总原则：配置都从 `main_control_service` 获取。** 它是配置中心，所以必须最先启动。

| 组件 | 配置从哪来 | 热重载 |
|---|---|---|
| `llm_service` | **纯 HTTP 拉控制面**：`GET {control}/api/v1/system/llm_service/raw` + `/system/database/raw`。**只读 `CONTROL_PLANE_BASE_URL` 一个环境变量**，其余完全不读 `.env` | `POST /api/v1/admin/reload-config`（只拉 service config，**不碰 db_config**，host/port 也不热切） |
| `agent_serving_java` | **HTTP 拉控制面**：`GET {control}/api/v1/serving-config`；不可达时回落本地文件 | `POST /api/v1/admin/reload-config`；控制面扇出：`POST {control}/api/v1/admin/reload-serving` |
| `knowledge_mining` | 直接读文件系统 `main_control_service/config/`（`domain_pack.py` 硬编码相对路径）；DB 配置走 `.env` 的 `PG_*` | 无，改配置必须重启 |
| `kb-ui` | HTTP 走控制面 | — |

控制面自己只有两个 admin 端点：`POST /api/v1/admin/reload-ip-whitelist` 和 `POST /api/v1/admin/reload-serving`（后者向每个 distinct 的 `serving_url` 扇出 reload-config，best-effort）。**没有 `reload-llm` 扇出**——LLM 的重载由前端自己经 proxy 打到对应服务。

`.env.example` 里整块 `LLM_SERVICE_PROVIDER_*` / `LLM_SERVICE_EMBEDDING_*` / `LLM_SERVICE_RERANK_*` 是**死变量**（只有 `test_live_demo.py` 引用），真配置在 `main_control_service/config/system/llm_service.yaml`。顶层 `EMBEDDING_*` 也不被 llm_service 读。仍活着的 LLM 变量只有 `LLM_SERVICE_URL`（mining 和 Java 调用方用）；`PG_*` 仍是 mining/serving 的库配置。

---

## 挖掘线：双引擎 + 算子化 workflow（本次最大改动）

### 两套引擎，按 run 决定，永不互转

`jobs/run.py` 的 `run()`/`publish()`/`resume()` 按 run 行里**持久化**的 `execution_engine` 列分派（`_persisted_execution_engine`），**不看部署配置**。`execution_engine ∈ {legacy, workflow}`，写进 run 行后不可变——legacy run 永不会被静默升级成 workflow。

- **legacy** = 老的固定线性 `StreamingPipeline`（parse→segment→enrich→entity_extract→resolve→entity_relations→discourse→retrieval_units→embedding→db_write，`mining/pipeline.py`），由 `_run_legacy()` 驱动。它是**回退引擎**，且它的每个 stage 函数被 workflow 的算子 handler 复用（workflow 不是重写，是重新编排）。
- **workflow** = 编译出来的算子 DAG。新 run 默认走这条（`mining_config.mining_run_submission_engine` 默认 `"workflow"`）。切换/回滚只影响**新** run，绝不改已有 run 的引擎/绑定/manifest。

> 灰度切换与回滚的完整规程见 `docs/mining-workflow-rollout-runbook.md`（准确、可信）。核心口令：`MINING_RUN_SUBMISSION_ENGINE=legacy` 回退 + 上传页隐藏选择器。

### workflow 系统的概念

- **算子 (operator)**：类型化节点定义 `MiningOperatorDef`。**16 个内置算子**（`workflow/operators/catalog.py`）：`input_ingest, parse_segment, enrich, discourse_line, contextual_retrieval_enrich, retrieval_unit_build, embedding, entity_extract, entity_resolve, entity_relation_extract, asset_persist, entity_review_gate, ontology_induction, ontology_review_gate, graph_write, mining_finalize`。每个算子带 **zone**（`input`/`document`/`global`）、**编辑策略**（`FIXED`/`PROTECTED`/`EDITABLE`）、**错误策略**（`FAIL_FAST`/`SKIP_DOCUMENT`/`SKIP_WITH_EMPTY`/`FALLBACK`/`PAUSE_FOR_REVIEW`）、类型化输入/输出槽、`requires`/`provides` 能力，参数 schema 来自 `options.py` 的 Pydantic 模型。
- **workflow（图）**：可编辑的图，存**全局控制库**的 `mining_workflows.draft_graph_json`。**workflow 定义是全局的、不带 domain**。
- **编译**：`WorkflowCompiler.compile(graph, mode=draft|publish)` 校验（未知/重复/版本、槽类型、必填输入、zone 转移、能力流、DAG 无环 Kahn 拓扑、算子依赖、输出必须是 `mining_finalize.result`），产出按 zone 分区的 `ExecutionPlan`（input_order / document_order / global_order）。
- **冻结 manifest**：发布时把 draft 编译并冻结成 `compiled_manifest_json`（含 `schemaVersion / catalogVersion / graphHash`、每节点 `paramsHash`、边、执行计划），不可变地存进 `mining_workflow_versions`（唯一 `(workflow_id, version)`）。
- **run 绑定**：提交时 `WorkflowRunBinder.resolve()` 选定已发布版本（默认 `system-full-baseline`），`bind_run_manifest()` 深拷贝 published manifest 再盖一层 `runtimeBinding`（`domain, channel, ontologyVersionId, ontologyApplicable, uploadBatchId, configFingerprint`）和 `runOverrides`，整体写进 `mining_runs.workflow_manifest_json`。**run 只从自己那份冻结 manifest 执行**——运行时 `_verify_and_build_plan` 会**重算 graphHash + 每节点 paramsHash + 节点集/边集/顺序**并拒绝任何漂移，所以编辑/重发布 workflow **绝不影响在飞的 run**。
- **节点 attempt**：每次算子执行是 `mining_workflow_node_events` 一行，`attempt_no` 在 PG advisory xact 锁下单调递增；已完成的 attempt 在 resume/重发布时被**复用**（幂等崩溃恢复）。唯一键 `(run_id, node_id, COALESCE(run_document_id,'__global__'), attempt_no)`。

**运行时**（`workflow/runtime.py`）：input zone → **document zone 在有界线程池并行**（跨文档调度 + 每文档 intra-zone DAG walk，fork/merge `DocumentState`，受 `max_workers` 约束）→ **global zone 串行**。review gate 返回 `PAUSED` → run 进 `awaiting_review` 并记 `pause_step`。

### 控制库 vs 域库（mining 新增的连接分离）

- **全局控制库**：`mining_workflows` + `mining_workflow_versions` 只由 Control 连接访问，**域库里不该出现这两张表**。schema 在 `databases/mining_control/schemas/001`，由 `pg_schema.primary_schema_paths()` 加载。
- **域维度**：run、冻结 manifest、节点事件、审核、资产、build、release 全按 domain 隔离；一次请求选定 domain 后不得跨连接池。

### per-domain 分库：已接通，但生产上仍是一个物理库

`domain_db.resolve_domain_database(entry, default)` 解析顺序：① 环境覆盖 `database_url_env` → ② **inline `database:` 块** → ③ 回落 `.env` 的 `MiningDbConfig().conninfo`。inline 块**现在真被读**（API 侧 `DomainPoolManager._resolve`、job 侧 `_create_dbs` 都走它，池按 conninfo 去重共享）。

**但**：出厂 `domain_registry.yaml` 给 `cloud_core_network` / `civil_engineering` / `odn` 配的 inline `database:` 全指向**同一个** `coremasterkb@121.89.90.178`，`generic` 没配 → 回落 `.env`。所以实际上 mining 仍写一个物理库，靠 `domain` 列隔离——但机制是真的 per-domain-capable，不是死代码。想让某域独立分库，改它的 inline `database:` 块即可。真正的死代码是 `pg_config.py:conninfo_from_env`（无生产调用方）。

### LLM 模板的静默 no-op（仍是坑，细节已更新）

`LlmClient.submit_task()` 对**任何**失败（HTTP 错、模板未知）都只 warning 并**返回 `None`**，调用方当「无任务」静默跳过——不报错、不失败文档。

- `mining-entity-extraction`（`stages/entity_extract`）：**未在任何 pack 声明**，但有 compat 兜底——`build_templates_from_profile()` 从 `mining-segment-understanding` 里的 `entities` 子 schema 合成它。**若 pack 的 `mining-segment-understanding` 没有 `entities` 属性，则实体抽取静默产不出东西。**
- `mining-ontology-induction`（`stages/ontology_induction`）：**未声明、且无 compat 生成器** → 除非有人手工 POST 进 llm_service，否则本体归纳静默产零候选。
- `mining-question-gen`：pack 已正确声明；`llm_templates.py` 有别名 `mining-question-generation → mining-question-gen` 兜底老 key。
- LLM 阶段只在 llm_service `health_check()` 通过时才构造；不可达则整个 LLM dict 为 `None`、抽取器根本不建（外面还有宽泛 `except (ImportError, Exception)` 吞异常）。

**改 pack 的 `template_key` 前先 grep 代码**：`submit_task` 的 key 与 pack/代码不一致时静默失效。`generic` pack **完全没有 `llm_templates` 段** → 该域下所有 mining LLM 阶段降级。`config_library/` 种子库里 odn/civil_engineering/tender_rfp 仍写着错的 `mining-question-generation`（靠上面的别名兜底），但生效的 `main_control_service/config/scenario_packs/` 已修对。

### mining API 表面（`mining/api/`）

routers：health, runs, knowledge, config, builds, uploads, ontology, document_lifecycle, **workflows**。

- **全局 workflow 管理 + 编辑器**（`/api/mining-workflows` + `/api/mining-operators/catalog`）：列表/创建/草稿保存（`expected_revision` 乐观锁，冲突 409 `DraftRevisionConflict`）/校验（publish 模式编译，失败 422）/发布/版本历史/预览/恢复成新草稿/克隆/归档。系统默认 `system-full-baseline` 不可归档。
- **批次上传 → workflow 绑定**：`POST /api/runs/preflight` 把上传批次与已有 snapshot 做 diff；`POST /api/runs` 接 `workflow_id/workflow_version`，engine=workflow 时 run override 仅限 `maxWorkers/executionMode/publishOnPartialFailure`（`SAFE_RUN_OVERRIDES`），显式 workflow 上传**必须**带 `preflight_id` + `document_decisions`（stale preflight → 409 `preflight_stale`）；engine=legacy 却指名 workflow → 503 `workflow_engine_unavailable`。
- **冻结 workflow run trace**：`GET /api/runs/{id}/trace` 只从 run 的 manifest 快照渲染冻结图 + `mining_workflow_node_events` 节点事件 + 抽取的告警。`POST /{id}/resume` 支持 failed/interrupted/崩溃 running 恢复。

---

## Java 检索线：控制面集成 + 按域分库

### 检索流水线：实际 12 个 trace 阶段，路由按复杂度

`SearchService.search()` 发出 **12** 个 trace 阶段：`query_understanding → retrieval_router → resolve_scope → tree_navigation → multi_query_expand → embedding → semantic_cache → retrieve → fusion → hydrate → rerank → assembly`（比文档常写的 8 步多 tree_navigation / multi_query_expand / embedding / semantic_cache / hydrate）。

**路由按 query 复杂度分层，不是 intent 驱动**：`RetrievalRouter.route()` 用 `COMPLEXITY_ROUTES` 按 `queryComplexity()`（simple/medium/complex）选路，intent 只作 tie-breaker 和调 rerank/expansion 旋钮。`BUILTIN_ROUTES`（intent-keyed）是**死代码**——赋值后从未被读，class Javadoc 仍写「Intent-aware」是误导。

### 算子/范式系统：19 个算子

`ParadigmCompiler` / `OperatorRegistry` / `ParadigmExecutor`。恰好 **19 个** `@Component implements Operator`（含 `entity_graph`——文档里漏列它的那份是错的）：

- input(1)：`request_input`
- query(4)：`query_understanding, multi_query, hyde, query_embed`
- scope(1)：`scope_resolve`
- retrieve(5)：`dense_vector, fts, entity_exact, entity_graph, graph_expand`
- fuse(3)：`rrf, weighted_rrf, identity`
- rerank(3)：`score_rerank, model_rerank, llm_rerank`
- output(2)：`assemble, collect`

`ENTRY_SLOTS` 只有 `Map.of("query", STRING)`——`scope` 被**故意排除**。`checkRequiredInputs()` 里，凡需要 `scope` 输入的算子（`fts/dense_vector/entity_graph/graph_expand`）**必须**连到 `scope_resolve` 节点，否则编译报 `missing_required_input`——防止「图编译过、运行时 scope 为 null、静默检索不到东西」。

### 控制面集成（从 v4 移植回来的能力）

- `MainControlClient.fetchServingConfig()` → `GET {baseUrl}/api/v1/serving-config` → `ServingConfigSnapshot`。
- `ConfigReloadService.reload()` 顺序喂给 `DomainRegistry.apply → DomainPackReader.apply → DomainPoolManager.invalidate()`；main_control 失败才回落本地文件（`SCENARIO_PACKS_DIR` / `DOMAIN_REGISTRY_PATH`）。
- base-url：`application.yml` 的 `${SERVING_MAIN_CONTROL_BASEURL:http://localhost:8910}`；`ServingProperties.java` 也有一份默认值，**要改一起改**。
- **`parseDatabase` 被复制了两份**：`MainControlClient.parseDatabase` 与 `ConfigReloadService.parseDatabase` 逐字平行（后者 Javadoc 明说 mirrors 前者）。给一侧加字段必须同步另一侧，否则 HTTP 路径与本地回落路径静默分叉。契约由 `MainControlClientTest` 锁住（它按 Python `get_serving_config()` 输出逐键构造 payload）——**改任何一侧键名，这个测试是唯一能拦住你的东西，编译器拦不住。**

### registry 的 `database:` 块是活的（Java 侧真分库）

`DatabaseConfig.isUsable()`：有 `jdbcUrl`，或 `host`+`dbname` 都非空 → true。`DomainPoolManager.createHikariPool()` 为该域建**专用 Hikari 池**并**建池时就 `conn.isValid(3)` 验连接**——失败直接抛 `IllegalStateException("domain_database_unavailable")`（外层 503），不静默回落默认库。`invalidate()` 用 `signature()`（拼 10 个字段）只重建变了的池。未配 `database:` 的域复用 `defaultDataSource`。想让某域回默认库，删掉它的 `database:` 块即可。

> ⚠️ 因为 Python 侧生产上仍写同一个物理库，若把某域的 Java `database:` 指到别的库，Java 会读不到 mining 写的数据。

### 仍然存在的坑（逐条已复核仍在）

- **虚拟线程不继承 ThreadLocal。** `DomainContext` 是普通 `ThreadLocal`（非 `InheritableThreadLocal`）。`SearchService.search()` 只在请求线程 `set()` 一次，但**所有检索都在 `newVirtualThreadPerTaskExecutor()` 的 `CompletableFuture.*Async` lambda 里跑**，变体循环和子查询循环**都没 `DomainContext.set()`**。后果：`EntityGraphRouteRetriever` 读 `DomainContext.get()` 为 null → `entity_graph` 路由在 `/api/v1/search` **恒返回空**；更深的坑是 `DomainRoutingDataSource` 把 null→default，所以配了 inline `database:` 的域，那些虚拟线程上的检索**静默查了默认库**。`DomainContext` 提供了 `wrapRunnable/wrapCallable/wrapSupplier`，但检索路径没用。
- **`AssetRawSegmentMapper.selectWithMeta` 会按 snapshot 链接数放大行**：`LEFT JOIN asset_document_snapshot_links`（1:N）且无 `DISTINCT`，同一 `raw_segment` id 返回多行。`ContextAssembler` 已按 id 去重、`GraphExpander` 靠 BFS visited 去重。**任何新消费 `selectWithMeta` 的代码都要自己按 segment id 去重**，别假设行唯一。
- **`scenario_pack_missing` 是死代码**：`GlobalExceptionHandler` 映射它、测试也测了，但**无人抛**——`DomainPackReader.getProfile()` 找不到 pack 时回落 `ServingDomainProfile.defaults()`，空 `serving:{}` 与「合法没 override」无法区分。
- **语义缓存污染**（见 `docs/TODO-known-issues.md`）：`SemanticCacheService.store()` 只挡 `queryVector==null`，不挡空/降级结果，降级期的空结果被写进 `serving_query_cache`，恢复后同 query 仍命中返回空（cos≥0.92，TTL 24h）。`evict(domain)` 存在但**无调用点**。修前先看那份台账。

### DDL 归属（Java 只建自己的表）

- **operator 表**（`operator_paradigm*`）：`ParadigmSchemaInitializer` 对**非路由的 `defaultDataSource`** 建，全局控制状态。
- **serving 运行时表**（`serving_query_logs` / `serving_query_cache`）：`ServingRuntimeSchemaInitializer`（实现 `DomainSchemaEnsurer`）在启动时对 default 建、并在**每个域建池时按域路由建进各自的库**（`002` 需 pgvector，失败被吞）。
- `agent_serving_java/src/main/resources/db/init.sql` 是一份基于 SQLite 时代的腐坏副本，**已删除**，无任何代码执行它。旧文档看到它别照建。

---

## 域配置：唯一真相源

**`main_control_service/config/` 是域配置的唯一真相源**，Java 与 Python 共读同一份：

```
main_control_service/config/
  domain_registry.yaml          # 4 个域：cloud_core_network / generic / civil_engineering / odn
  scenario_packs/<pack>/domain.yaml   # 分 ontology: / mining: / serving: 三段
  system/llm_service.yaml, system/database.yaml ...
```

历史上根目录另有一份副本（`./domain_registry.yaml` + `./scenario_packs/`）——已删除，**勿恢复**。（`config_library/site|enterprise/` 是种子库，不是生效配置。）若在旧文档/分支/issue 看到 `../scenario_packs`、`/app/scenario_packs`、`COREMASTERKB_DB_CLOUD_CORE`，那是统一前的描述。

### Java 侧怎么拿到它

Serving **不直接读这些文件**，而是拉控制面聚合的快照：

```
GET {main_control}/api/v1/serving-config
  → {"domains": {<id>: {enabled, default_channel, database, serving}}}
       database: registry 的内联块（或 null → 用默认 DataSource）
       serving : scenario pack 的 serving: 段（ontology:/mining: 不下发——serving 从不读）
  → MainControlClient 解析成 ServingConfigSnapshot
  → ConfigReloadService 原子地喂给 DomainRegistry / DomainPackReader / DomainPoolManager
```

`generic` 域没有 inline `database:` → 快照里 `database: null` → 用默认库。scenario pack 的 `serving:` 段被当根解析（`route_policy`/`query_understanding`/`extractor_rules`/`intent_strategy` 同级，无 ontology/mining 嵌套）。

### mining 侧怎么拿到它

knowledge_mining **直接读文件系统**（`domain_pack.py` 硬编码相对路径 `_REPO_ROOT/main_control_service/config/...`），只读 `ontology:`+`mining:` 两段，不走 HTTP。

---

## 数据库

权威 schema 是 Python 侧 `databases/<store>/schemas/*.sql`，由 `mining/infra/pg_schema.py` 按序执行（逐语句、容忍重复对象）。`domain_schema_paths()` 顺序：asset 002 → runtime 002/003/004/005/**006** → asset 003 → asset **004** → **ontology 001（必须最后**，FK 指向 `asset_*` 和 `mining_runs`）；`primary_schema_paths()` 再追加 `mining_control/001`。

### 表分组（含 workflow 新增）

- **asset_core**：`asset_documents / asset_document_snapshots / asset_document_snapshot_links / asset_raw_segments / asset_raw_segment_relations / asset_retrieval_units / asset_retrieval_embeddings / asset_source_batches / asset_builds / asset_build_document_snapshots / asset_publish_releases`（+ `003` 给各表加 `domain` 列）。
- **mining_control（仅控制库）**：`mining_workflows` / `mining_workflow_versions`。
- **mining_runtime**：`005` 给 `mining_runs` 加 `workflow_id/version/version_id/graph_hash/manifest_json(JSONB)/execution_engine(默认 legacy, CHECK)/active_node_id/active_operator_type/pause_step`，并建 `mining_workflow_node_events`；`006` 加 `mining_runs.preflight_manifest_json`。
- **asset_core/004（snapshot ↔ workflow 绑定）**：给 `asset_document_snapshots` 加 `workflow_*` 列 + 全有或全无 CHECK，并把唯一性**按 workflow 版本拆成两个部分索引**（legacy `(domain, normalized_content_hash) WHERE workflow_graph_hash IS NULL`；workflow 版含 `workflow_id/version/graph_hash`）。**行为变化**：同内容文件在不同 workflow release 下产生**不同 snapshot**。
- **ontology**：`ontology_versions / ontology_node_types / ontology_candidates / ontology_entities / ontology_entity_relations / asset_segment_entity_mentions` 等，DDL 里还 `ALTER mining_runs ADD subloop_stage / ontology_version_id`。

### ⚠️ `reset_db.py` 的 SCHEMA_FILES 已过时

`reset_db.py` 的 `SCHEMA_FILES` **漏了两个盘上存在、pg_schema 会加载的文件**：`asset_core/schemas/004_asset_snapshot_workflow_binding.sql` 和 `mining_runtime/schemas/006_mining_run_preflight.sql`。用 `reset_db.py` 重建的库会**缺** snapshot workflow 绑定列/约束和 preflight 列，与正常初始化的库分叉。（`reset_db.py` 反而独家跑了 `agent_llm_runtime/002`，pg_schema 不跑。）改库结构时以 `pg_schema.py` 的路径列表为准。

### DB 脚本（仓库根）

```bash
python reset_db.py      # 破坏性重建（DROP CASCADE 后重跑 SCHEMA_FILES；见上警告）
python export_db.py     # 只导数据 → backups/export_<ts>.sql（逐行 INSERT）
python import_db.py     # 单条 TRUNCATE(全表) CASCADE 后逐行 INSERT
```

`reset_db.py` 的 DROP 顺序（`ALL_TABLES`）是**子表先**（ontology 组 → agent_llm → mining_runtime → mining_control → asset_core 最后），因为 `DROP ... CASCADE` 只删外键约束、不删引用方的表本身。`db_tables.py` 的 `EXPORT_TABLES` 是**父表先**，`import_db.py` TRUNCATE 时 `reversed()`。`import_db.py` 把所有存在的表拼成**一条** TRUNCATE，少一张表整条都失败——所以 `OPTIONAL_TABLES`（`operator_paradigm*` / `serving_query_logs` / `serving_query_cache`，由 **Java** 建、Python 侧不建）用 `to_regclass` 过滤存在性，改脚本时别去掉这个保护。

`db/migrate_v1_to_zdy.sql` / `db/migrate_v2_semantic_cache.sql` 是人工历史迁移，内容已被幂等升级段覆盖，不自动执行。

---

## 常用命令

### 本地开发

```bash
# 前端（需先起 main_control_service:8910，它是配置中心）
cd kb-ui && npm install && npm run dev        # → localhost:5173

# 各服务单独起（Windows 下 llm_service / knowledge_mining 必须用 -m）
python -m main_control_service.main            # 8910，必须最先
python -m llm_service                          # 8900
python -m knowledge_mining.mining.api          # 8901
python -m mcp_server --transport streamable-http --port 9000
```

Windows 上 `llm_service` 和 `knowledge_mining` 必须走 `python -m`：入口模块会 monkey-patch uvicorn 用 `SelectorEventLoop`，psycopg async 在默认 ProactorEventLoop 上不工作。`pip install -e .` 要在**仓库根**跑，但它**装不全依赖**——`pyproject.toml` 缺 `jieba` 和 `python-multipart`（真依赖以 `docker/Dockerfile` 的 pip 列表 + `knowledge_mining/requirements.txt` 为准，三处已漂移）。缺 `python-multipart` 跑 uploads 路由会 ImportError，缺 `jieba` 中文分词降级。

### 测试

```bash
# llm_service —— 纯单测，不需要 DB，最快
pytest llm_service/tests/ -q

# knowledge_mining —— 强绑真实 PostgreSQL（见下约束）
python -m pytest knowledge_mining/tests/ -v
# workflow 双库验收（只对 _test 结尾的可丢弃库）
KB_RUN_POSTGRES_ACCEPTANCE=1 KB_ALLOW_TEST_TRUNCATE=1 \
  MINING_TEST_DOMAIN_PG_DBNAME=kb_plant_a_test \
  python -m pytest knowledge_mining/tests/test_mining_workflow_postgres.py -m postgres -v

# Java —— 三级分层
cd agent_serving_java
mvn test                          # L1 单测（排除 pg-integration,e2e）
mvn verify                        # L2 + 集成（需 PG）
mvn verify -Pe2e                  # L3 端到端
mvn test -Dtest=QueryUnderstandingEngineTest#方法名
```

**`knowledge_mining` 测试的硬约束**（`tests/conftest.py`）：
1. **autouse 是 `_guard_test_database`**（不是 `_ensure_schema`）：`_assert_disposable_database` **拒绝任何库名不以 `_test` 结尾的库**（不连库就 fail），护住 `.env` 指向的生产库 `coremasterkb`。
2. `_ensure_schema` 是普通 session fixture（**非 autouse**），只有真正 request 它/连接池的测试才触发真 PG 要求。
3. `_truncate_all()` 除非 `KB_ALLOW_TEST_TRUNCATE=1` 否则**硬 no-op**。PostgreSQL 验收还需 `KB_RUN_POSTGRES_ACCEPTANCE=1`，域隔离测试需独立的 `MINING_TEST_DOMAIN_PG_DBNAME`。

**⚠️ 改了 Java 的 record / 构造器签名后，`mvn test-compile` 的 BUILD SUCCESS 是假的。** Maven 增量编译只看源文件时间戳，没动测试源码就 `Nothing to compile` 跳过重编。必须强制全量：

```bash
rm -rf target/classes target/test-classes && mvn -o test
```

（`mvn clean` 在离线环境用不了；手工删目录即可。本地 m2 缓存齐全，`mvn -o` 可离线跑通编译和全部单测。）

### 部署

```bash
bash deploy-build.sh                # docker compose build → docker save → cmkb.tar
bash deploy-server.sh               # 默认：仅补缺，保留服务器本地改动
bash deploy-server.sh --force       # 用镜像代码覆盖代码，但保留宿主机配置
bash deploy-server.sh --force-config # 用镜像覆盖 .env + main_control_service/config
bash deploy-server.sh --apply-config # 不换文件，仅重建容器 + 校验挂载

docker compose exec app supervisorctl status
docker compose exec app supervisorctl restart mining
```

**`--force` 现在保留配置**（`stage_code_from_image` 把宿主机 `main_control_service/config` 复制进暂存快照再换入）——旧文档里「`--force` 会 `rm -rf` 掉配置」的说法**已不成立**。真正会覆盖配置的是 `--force-config`（覆盖 `.env` + `config/`），或 config 目录不存在的兜底补齐。启动按依赖顺序 control → llm → mining → serving → mcp → nginx，逐个 health 检查，并对 `.env` + 3 个 config 文件做 host-vs-container sha256 校验。

**Python 改宿主机文件后 `supervisorctl restart` 即生效**（`docker-compose.yml` bind-mount 了 `.env` / `knowledge_mining` / `llm_service` / `main_control_service` / `mcp_server` / `databases`）。**Java jar 和前端 dist 烤进镜像、无 volume 挂载**，改动必须重新 `deploy-build.sh` + 重部署。

---

## 修改代码时的注意事项

**`llm_service` 有两条完全独立的落库路径。** 异步路径（`/tasks` → Worker → `TaskManager`）失败走 `dead_letter`，`TaskManager.fail()` **没有 `failed` 分支**，任务态永远不会是 `failed`（attempt 行可以是 failed）；同步路径（`/execute`）由 `PersistWriter` 事后写入，status 直接终态且**可能是 `failed`**。查 `agent_llm_tasks` 时把两类混在一起统计必错。

**不要给 `LLMService.execute_chat_attempt` 加 `raise`。** 它 docstring 说会 raise，但**实现刻意在所有失败路径都不 re-raise**——因为 `Worker._execute_task` 的 safety net 会再调一次 `_mgr.fail()`，加了 raise 就复活「每次 attempt 扣 2 次重试额度」的 double-fail bug。看代码注释，别看 docstring。

**LLM 配置键是 `provider_type` 不是 `provider.type`。** 写错（或写成嵌套 `provider.type`）会被静默忽略，`fallback` 到 `openai_compatible`——因为它不在 `_REQUIRED_PATHS` 里，不校验。`system/llm_service.yaml` 里 anthropic 模型条目就靠这个键。

**算子系统的 `scope` 必须显式连线（Java）。** `ParadigmCompiler.ENTRY_SLOTS` **只有 `query`**，`scope` 被故意排除——否则图「编译通过但运行时 scope 为 null，静默检索不到」。所有需要 scope 的检索算子必须连到 `scope_resolve`。这是 `missing_required_input` 的来源。

**虚拟线程不继承 `DomainContext`（Java）。** 任何 `CompletableFuture.runAsync` 提交的任务都要显式 `DomainContext.set()` 或用 `DomainContext.wrapRunnable`。`ParadigmExecutor` 每节点都做了；`SearchService` 的变体/子查询检索**漏了**（已知 bug，`entity_graph` 恒空 + 分库域静默走默认库）。

**新增 Java 算子只需打 `@Component`**，`OperatorRegistry` 靠构造注入自动收集，type 重复启动失败。前端按算子 `paramSchemaJson`（JSON Schema draft-07）自动渲染参数表单，加参数只改后端 schema。

**mining workflow 的 manifest 是运行时强校验、不信任的。** `runtime._verify_and_build_plan` 重算 graphHash + 每节点 paramsHash 并拒绝任何漂移；编辑/重发布 workflow 不会回溯改已绑定的 run。改算子 catalog 或 option 模型会改 `catalogVersion`/`paramsHash`，注意与已发布版本的兼容。

**mining 的 `execution_engine` 是每 run 不可变、从 DB 读的。** legacy run 永不自动升级；`_run_legacy`/`StreamingPipeline` 是活代码（回退引擎），不能当废弃删。切换/回滚引擎绝不改已有 run 的 `execution_engine`/workflow 绑定字段/manifest/节点事件。

**mcp_server 直连 serving、有硬编码远程 IP 兜底。** `BACKEND_URL` 默认 `http://121.89.90.178:8081`，只有 supervisord 把它覆盖成 localhost。它只暴露一个 tool `search_knowledge`（`POST /api/v1/search` 透传），transport 默认 `streamable-http`（模块 docstring 说 stdio 是过时的）。

---

## 一段能解释很多疑惑的历史

`agent_serving_java` **不是**检索服务的第一版。v4 分支上它叫 `agent_serving_zdy`；`268f9f1`「纳入同事 agent_java2/main_control_service2 的工作」用同事那份实现替换了它——带来了算子/范式系统，但**静默丢掉了整个控制面集成**（`MainControlClient` / `ConfigReloadService` / `AdminController` / `DatabaseConfig`，以及控制面侧的 `/api/v1/serving-config` 和 `/api/v1/admin/reload-serving`）。这批能力后来从 v4 移植了回来。

这解释了几件否则说不通的事：

- `docs/code_guide.md` 提到的 `serving.embedding.model` / `serving.rerank.model` 在 v4 真实存在，同事那版没有（模型改由 llm_service 决定）。
- `kb-ui` 的 `ReloadConfigTab.vue` 文案是复数「对应服务的重载按钮」，却只有 LLM 一个按钮——serving 那个按钮是 v4 有、替换时丢的。控制面扇出端点已补回，UI 按钮**仍没补**。

**读 v4 分支时**：Java 模块在 `agent_serving_zdy/`，且那个分支根目录还有已废弃的 `domain_registry.yaml` + `scenario_packs/` 副本，其文件兜底读的是 `database_url_env`（旧 schema），当前实现读内联 `database:` 块——别照抄。

---

## 文档可信度（重要）

文档质量差异极大，照着读会被带偏：

| 文档 | 状态 |
|---|---|
| `docs/mining-workflow-rollout-runbook.md` | ✅ **准确**，挖掘 workflow 灰度/回滚的权威规程，与源码对得上（16 算子、控制库/域库边界、冻结 manifest、`MINING_RUN_SUBMISSION_ENGINE`） |
| `agent_serving_java/docs/ontology-retrieval-explained.md` | ✅ **准确**，与源码逐行对得上 |
| `agent_serving_java/docs/检索范式使用说明.md` | ✅ 质量高。小偏差：算子实际 19 个（漏列 `entity_graph`） |
| `agent_serving_java/docs/TODO-known-issues.md` | ✅ serving 侧「已确认未修复」问题台账（当前：语义缓存污染）。修前先看 |
| `docs/superpowers/plans|specs/*` | ✅ 本次 workflow 改动的设计计划/规格，可作背景参考 |
| `llm_service/README.md` / `ARCHITECTURE.md` / `QUICKSTART.md` | ⚠️ 主体质量高，但**配置章节大面积按理想设计而非实际代码书写**。最致命：键实际是 `provider_type` 不是 `provider.type`。另有「记录已被修复的 bug」的系统性漂移 |
| `agent_serving_java/docs/code_guide.md`、`pipeline-0X-*.md` | ❌ 过时，**部分写的是 v4 的 `agent_serving_zdy`**。pipeline 实际 12 个 trace 阶段不是 10；`pipeline-03` 的 intent 驱动路由是**死代码**（`BUILTIN_ROUTES` 赋值后从未读），实际按 query 复杂度分层 |
| `knowledge_mining/README.md`、`docs/stage-*.md` | ❌ 描述的是**已删除的 SQLite 时代架构**，且完全没写 workflow 算子化——挖掘线以源码 + rollout runbook 为准 |
| `kb-ui/FRONTEND-PLAN.md` | ❌ 描述的架构从未落地，且与现实相反（它设计 `/:domain/mining` 前缀路由 + 三端口直连 CORS，实际是控制面单入口、路由不含 domain 段） |
| `kb-ui/README.md` | ❌ Vite 模板默认文本，零信息量 |
| `databases/README.md` | ⚠️ 声称「逻辑分库必须坚持」，但 ontology DDL 已跨库建 FK，物理合库不可逆；漏列在用的 `ontology/` `serving_runtime/` `mining_control/` |
| 根 `README.md` | ⚠️ 部署部分大体准确；nginx 路由描述已过时（见「前端调用范式」） |

新人最短上手路径：先起 `main_control_service`，挖掘线读 `mining-workflow-rollout-runbook.md`，检索线读 `ontology-retrieval-explained.md` + `检索范式使用说明.md`，其余文档一律对着源码读。`docker/nginx.conf` 里的三行注释比 `FRONTEND-PLAN.md` 和 `kb-ui/README.md` 加起来都准确。
