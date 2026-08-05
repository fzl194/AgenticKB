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
  KB 层：用户/知识库/文件夹/文档管理            查询理解 → 路由(按复杂度) → 范围解析
   · KB 独占写 asset_documents 身份             → 树导航 → 多查询扩展 → 向量化
  挖掘两套引擎，按 run 决定：                     → 语义缓存 → 多路召回 → 融合
   · legacy   固定流水线 StreamingPipeline      → 水合 → 重排 → 上下文组装
   · workflow 算子 DAG + 冻结 manifest                          ▲
  build → publish release                                        │
         │                                                       │
         └──── 写 asset_*/kb_* 表 ──► PostgreSQL ──── 只读 ──────┘
```

`agent_serving_java` 对全部 `asset_*` 表**只读**（7 个 `Asset*` mapper + `OntologyGraphMapper` + `KnowledgeBaseMapper` 全是 `<select>`；14 个 mapper XML 里只有 `ServingQueryLog`/`SemanticCache`/`Paradigm`/`ParadigmVersion` 有写）。**写方不再是「mining 独占全部 asset_\*」**：KB 中心化后，`asset_documents` **文档身份由 `knowledge_mining/mining/kb/` 包独占写**，mining 只**读**文档身份（按 `storage_path`）并产 snapshot 及以下派生资产。serving 自己只写 `serving_query_logs` / `serving_query_cache` / `operator_paradigm*`。详见下文「KB 层」。

**发布语义**：mining 挖完的内容不会立刻可检索。必须 build 成不可变快照并 `publish` 成 release；`asset_publish_releases` 上有部分唯一索引 `WHERE status='active'` 保证「一个 **(domain, channel)** 至多一个 active release」。serving **默认**只认 active release，这也是 `no_active_release` / `multiple_active_releases` 报错的来源；**唯一例外是按 `kbIds` 收窄的检索，它绕过 release 直接从 build 解析**（见「serving 的 KB 感知」）。发布用 `acquire_domain_publish_lock(domain)` 的按域 advisory 锁串行化，`activate_release()` 在同一事务里退旧启新。**⚠️ 但 KB 触发的挖掘 `publish=False`（只 build 不发布）**——见「KB 层」。

### ⚠️ 重大变化（务必先读）

本仓库近期有两波大改动：先是「挖掘算子化」（workflow 引擎），再是「KB 中心化」（知识库管理 + 配置层去 .env）。

| 主题 | 现状 |
|---|---|
| 挖掘执行 | **新 run 默认走 workflow 算子 DAG**（`mining_config.py` 默认 `workflow`）。legacy 固定流水线仍在，作为回退引擎，按 run 的 `execution_engine` 列（不可变）决定，二者永久共存 |
| **配置来源（最大坑）** | **mining 已不读 `.env`**：service + DB 配置改走 HTTP 控制面（`control_plane.py`，`GET /api/v1/system/mining\|database/raw`），与 llm_service/serving 同构。唯一还读文件系统的是 `domain_pack.py` 的域 registry/scenario pack。serving 也去掉了 `.env` 导入。`.env` 已废弃，勿恢复 |
| 库名 | 全线 `coremasterkb` → **`kb_db`**（registry、`system/database.yaml`）。4 个域现在都有 inline `database:` 块、都指向同一个 `kb_db` |
| **DB 地址真相源** | **`main_control_service/config/` 是唯一真相源，Java 侧最后一处硬编码已消除**。serving 的默认数据源改从 `GET {control}/api/v1/system/database` 的 `default` 块构造（`ServingBeans.defaultDataSource`）。`application.yml` 的 `spring.datasource.*` 出厂**留空**，只做控制面不可达时的兜底；两者都没有则启动失败报 `default_datasource_unresolved`——**故意不静默回落**，因为静默连上旧库正是它从前的坑 |
| **文档身份归属** | `asset_documents` 身份由 **KB 包独占写**；UNIQUE 从 `(domain, document_key)` 改成 `(kb_id, document_key)`；mining 的 `upsert_document` 退到只服务 legacy `/api/runs` |
| `deploy-server.sh --force` | **不删配置**：`--force` 把宿主机 `main_control_service/config` 复制进暂存快照再换入（`deploy-server.sh:502-506`），保留配置。只有 `--force-config`（或 config 目录不存在）才覆盖配置 |
| `reset_db.py` 的 `SCHEMA_FILES` | **已与 `pg_schema.py` 对齐**（补回了曾漏的 `004_asset_snapshot_workflow_binding` + `006_mining_run_preflight`，并加了全部 kb schema） |

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

`createProxyClient(service)`（`kb-ui/src/api/proxyClient.ts`）建的 axios **没有固定 baseURL**，在**每个请求的拦截器里**重算 `/api/control-plane/api/v1/proxy/{当前域}/{service}`（域从 `useDomainStore()` 实时读），所以切域无需重建客户端。`mining` 服务默认额外注入 `domain` query 参数（`includeDomainQuery:false` 可关，全局 workflow API 就关了它）。**KB 相关请求（`/api/kb/*`，走 mining service）会额外注入 `X-KB-User` 头**（值取 `VITE_KB_DEFAULT_USER || 'admin'`）作为 Phase-1 身份——服务端 `mining/kb/auth.py` 据此 upsert `kb_users`。这是内网信任头，不是生产级鉴权。

例外：`controlPlane.ts` **直连**控制面（`baseURL=/api/control-plane`，不走 `/proxy/{domain}/{service}` 形状），用于系统配置 / 域 / scenario pack / 日志。reload 按钮则通过 proxy 打 `/proxy/{domain}/{service}/api/v1/admin/reload-config`。

`README.md` 里写的 `/api/mining`、`/api/serving`、`/api/llm` **已不存在**，是早期遗留。加新接口从 `kb-ui/src/api/*.ts` 入手。

### 配置来源（改配置前必读）

**总原则：配置都从 `main_control_service` 获取。** 它是配置中心，所以必须最先启动。

| 组件 | 配置从哪来 | 热重载 |
|---|---|---|
| `llm_service` | **纯 HTTP 拉控制面**：`GET {control}/api/v1/system/llm_service/raw` + `/system/database/raw`。**只读 `CONTROL_PLANE_BASE_URL` 一个环境变量**，其余完全不读 `.env` | `POST /api/v1/admin/reload-config`（只拉 service config，**不碰 db_config**，host/port 也不热切） |
| `agent_serving_java` | **HTTP 拉控制面**：per-domain 走 `GET {control}/api/v1/serving-config`（不可达时回落本地文件）；默认数据源（非路由的 `operator_paradigm*` 等）走 `GET {control}/api/v1/system/database` 的 `default` 块，与 mining 同源。不再读 `.env` `PG_*`，`application.yml` 里也**不再硬编码**地址 | `POST /api/v1/admin/reload-config`（只重载 per-domain；**默认数据源不热切**，改了要重启 serving）；控制面扇出：`POST {control}/api/v1/admin/reload-serving` |
| `knowledge_mining` | **HTTP 拉控制面**（`control_plane.py`）：service 配置 `GET {control}/api/v1/system/mining/raw`、DB 配置 `GET /system/database/raw`，启动时抓一次缓存。**不再读 `.env`/`PG_*`/`MINING_API_PORT`**。**唯一还读文件系统的**是 `domain_pack.py` 加载 `domain_registry.yaml` + `scenario_packs/*/domain.yaml`（域知识，非 service/DB 配置） | 无，改配置必须重启 |
| `kb-ui` | HTTP 走控制面 | — |

控制面自己只有两个 admin 端点：`POST /api/v1/admin/reload-ip-whitelist` 和 `POST /api/v1/admin/reload-serving`（后者向每个 distinct 的 `serving_url` 扇出 reload-config，best-effort）。**没有 `reload-llm` 扇出**——LLM 的重载由前端自己经 proxy 打到对应服务。

**`.env` 已全面废弃**：mining/serving 都改走控制面，`.env.example` 里的 `LLM_SERVICE_PROVIDER_*` / `EMBEDDING_*` / `RERANK_*` / `PG_*` 全是死变量。真配置在 `main_control_service/config/system/`（`llm_service.yaml` / `mining.yaml` / `database.yaml`）。唯一还被读的 bootstrap 环境变量是各服务的 `CONTROL_PLANE_BASE_URL`（默认 `http://localhost:8910`）；`LLM_SERVICE_URL` 仍由调用方读（在 mining.yaml/application.yml 里配）。

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

`domain_db.resolve_domain_database(entry, default)` 解析顺序：① **inline `database:` 块** → ② 回落全局默认库（控制面 `database.yaml` 的 `default`）。原先的 `database_url_env` 环境覆盖分支**已删除**（`environ` 形参只为签名兼容保留，不再使用）。inline 块被 API 侧 `DomainPoolManager._resolve` 和 job 侧 `_create_dbs` 读取，池按 conninfo 去重共享。

**但**：出厂 `domain_registry.yaml` 现在给 **4 个域全部**配了 inline `database:` 块，且都指向**同一个** `kb_db@121.89.90.178`。所以实际上 mining 仍写一个物理库，靠 `domain` + `kb_id` 列隔离——机制是真的 per-domain-capable。想让某域独立分库，改它的 inline `database:` 块即可。（`pg_config.py:conninfo_from_env` 这个旧死代码已随配置层重构删除。）

### LLM 模板的静默 no-op（仍是坑，细节已更新）

`LlmClient.submit_task()` 对**任何**失败（HTTP 错、模板未知）都只 warning 并**返回 `None`**，调用方当「无任务」静默跳过——不报错、不失败文档。

- `mining-entity-extraction`（`stages/entity_extract`）：**未在任何 pack 声明**，但有 compat 兜底——`build_templates_from_profile()` 从 `mining-segment-understanding` 里的 `entities` 子 schema 合成它。**若 pack 的 `mining-segment-understanding` 没有 `entities` 属性，则实体抽取静默产不出东西。**
- `mining-ontology-induction`（`stages/ontology_induction`）：**未声明、且无 compat 生成器** → 除非有人手工 POST 进 llm_service，否则本体归纳静默产零候选。
- `mining-question-gen`：pack 已正确声明；`llm_templates.py` 有别名 `mining-question-generation → mining-question-gen` 兜底老 key。
- LLM 阶段只在 llm_service `health_check()` 通过时才构造；不可达则整个 LLM dict 为 `None`、抽取器根本不建（外面还有宽泛 `except (ImportError, Exception)` 吞异常）。

**改 pack 的 `template_key` 前先 grep 代码**：`submit_task` 的 key 与 pack/代码不一致时静默失效。`generic` pack **完全没有 `llm_templates` 段** → 该域下所有 mining LLM 阶段降级。`config_library/` 种子库里 odn/civil_engineering/tender_rfp 仍写着错的 `mining-question-generation`（靠上面的别名兜底），但生效的 `main_control_service/config/scenario_packs/` 已修对。

### mining API 表面（`mining/api/`）

routers：health, runs, knowledge, config, builds, uploads, ontology, document_lifecycle, **workflows**，外加 KB 层的 4 个 router（kbs / documents / folders / mining，见「KB 层」）。

- **全局 workflow 管理 + 编辑器**（`/api/mining-workflows` + `/api/mining-operators/catalog`）：列表/创建/草稿保存（`expected_revision` 乐观锁，冲突 409 `DraftRevisionConflict`）/校验（publish 模式编译，失败 422）/发布/版本历史/预览/恢复成新草稿/克隆/归档。系统默认 `system-full-baseline` 不可归档。前端叫「挖掘范式」页（`/mining/workflows`），仍在。
- **批次上传 → workflow 绑定（legacy `/api/runs` 入口）**：`POST /api/runs/preflight` 把上传批次与已有 snapshot 做 diff；`POST /api/runs` 接 `workflow_id/workflow_version`。**注意**：前端旧的「全局挖掘任务发起页」（CreateRunView/RunsView）已删除，普通用户挖掘统一走 KB 入口（`/api/kb/{id}/mine`）；`/api/runs` 仍在但不再有前端入口。
- **冻结 workflow run trace**：`GET /api/runs/{id}/trace` 只从 run 的 manifest 快照渲染冻结图 + `mining_workflow_node_events` 节点事件 + 抽取的告警。`POST /{id}/resume` 支持 failed/interrupted/崩溃 running 恢复。

---

## KB 层：知识库中心化（`mining/kb/`，本次最大新增）

「知识库/文档管理」被抽成用户可见的一等公民：用户建 KB → 管理库内文件/文件夹 → 显式触发挖掘 → 看文档状态与知识。这套东西**嵌在 mining 服务里**（不是独立服务），走 mining proxy，路径 `/api/kb/*`。设计需求见 `docs/kb-management-design.md` 等。

### 数据模型与写方铁律

新表 `kb_users` / `knowledge_bases` / `kb_members` / `kb_folders`（`databases/kb/schemas/`），**建在每个域库 + 主控制库**（`pg_schema` 的 domain 和 primary 路径都注册了），所以 `asset_documents.kb_id → knowledge_bases(id)` 是**同库真 FK**（`ON DELETE RESTRICT`）。

- `knowledge_bases`：属于某 `domain`，有 `owner_id`、可见性 `private/shared/public`、软删除（`status='deleted'`）、`UNIQUE(domain, name)`、`mining_workflow_id`（绑定的挖掘范式，软引用控制库的 `mining_workflows`，NULL → `/mine` 报 400）。
- `kb_members`：PK `(kb_id, user_id)`，`role ∈ {viewer, editor}`，仅 `shared` 用。
- `kb_folders`：一等文件夹树（自引用 `parent_id`），`UNIQUE(kb_id, path)`，磁盘目录镜像它。
- `asset_documents` 加列：`kb_id`（真 FK）、`storage_path`、`directory_path`、`owner_id`、`file_size`、`modified_at`；**UNIQUE 从 `(domain, document_key)` 改成 `(kb_id, document_key)`**（legacy 行 `kb_id=NULL` 不冲突）。

**写方铁律（设计铁律 1）**：`asset_documents` 身份**只由 KB 包 `KbDB.insert_document_identity` 写**；mining 只**读**（按 `storage_path`）、只产 snapshot 及派生资产。legacy `AssetCoreDB.upsert_document` 被隔离到只服务 `/api/runs`（且已去掉 `ON CONFLICT(domain,document_key)`）。

### 身份 vs 位置分离（G1/G3，易踩）

- **`document_key`（= `doc:/{磁盘相对路径}`）是冻结身份键**，`mining_run_documents` 和状态派生都 join 它。
- **`storage_path`（含 `{kb_id}` 前缀、全局唯一）是 mining 查身份用的键**（不是 `document_key`）——解决「跨 KB 同 document_key」歧义；legacy 行 `storage_path=NULL` 永不被匹配。
- **⚠️ 移动/改名文件必须与磁盘移动同一事务里改写 `document_key`**（`update_doc_location` 三个字段一起写；`FolderService._relocate_docs` 对被移动子树每个 doc 重算 key）。否则状态派生 join 断裂，文档**永远卡在 `uploaded`**（这就是 `cd1e3fc` 修的 bug）。纯元信息改名（`patch_document`）不碰磁盘、不改 key。

### KB 挖掘 = 只 build 不 publish

`POST /api/kb/{kb_id}/mine`（body `document_ids?` / `force_redo?`）：

- **永远用 workflow 引擎**，绑定 KB 的 `mining_workflow_id` 当前发布版本；`insert_queued_run(execution_engine="workflow")`，metadata 里塞 `{kb_id, publish:False, force_redo, signature, document_ids?}`，后台线程跑。
- **`publish=False` 是刻意的**：KB 挖掘只 build、不发布到域级 active release（避免共享域里跨 KB 互相 retire）。
- **整库 vs 选中**：空 `document_ids` = 整库增量（`input_path` 锁到 `{upload_root}/{kb_id}`，未变文档靠 lifecycle SKIP/RESTORE 免挖）；非空则按 id → storage_path 过滤 ingest。
- **`force_redo`**：用户勾选 **OR** 范式签名（`workflow_id:version:graph_hash`）比上次变了 → 自动 `auto_force_redo`。生效时清空 snapshot 派生资产（单元/向量/关系/切片）并强制 `UPDATE`，绕过内容哈希 SKIP。
- **状态是派生的、不落库**（`db.py` 的 `_STATUS_CASE_SQL`，折进 list/detail SQL 免 N+1）：优先级 `published > failed > mined(committed) > mining > withdrawn > uploaded`。**`committed⇒mined` 这档是 KB 特有的**——因为 KB run 无 active release，否则 committed 文档会掉回 `uploaded`。
- `build` 会写 `kb_id`（`asset_builds.kb_id`）；`get_document_knowledge` 靠它找「**包含该文档的最新 build**」（`b.kb_id=? AND status IN validated/published`），不是 KB 全局最新 build——增量/选择性挖掘下全局最新 build 可能不含该文档。

### 鉴权与前端

- Phase-1 鉴权 = `X-KB-User` 头（`auth.py:current_user` upsert `kb_users`），**内网信任头，可伪造**，Phase-2 只换身份来源、表和权限逻辑不变。
- 权限：读 = owner/public/member；写 = owner/editor。不可见一律映射成 404（不泄露存在性）。
- 前端在 `kb-ui/src/views/kb/`：KB 列表 → 详情（文件/成员/挖掘/设置 4 tab）→ 文档预览（`mined/published` 才挂动态知识 tab）→ KB 内的 run 详情（12 阶段 `PipelineFlow`）+ 单文档详情。路由 `/kb/*`。

---

## Java 检索线：控制面集成 + 按域分库

### serving 的 KB 感知：按 kb_id 收窄检索范围

两条检索入口都接了身份与 KB 范围：

- `POST /api/v1/search` 的 body 收 `kbIds`（`@JsonAlias("kb_ids")`，默认空 = 老的全域行为）。**注意 `SearchRequest` 里 `scope` 和 `kbIds` 无关**：`scope` 过滤文档内部结构（章节等），`kbIds` 决定检索哪个语料库。
- 范式侧由 `scope_resolve` 的 **`kbIds` 节点参数**承载——它是**设计态**属性，冻结进存储的图，不是每请求传。参数 schema 带 `x-widget: "kb-picker"`（JSON Schema 忽略未知关键字，纯 UI 提示），前端 `ParadigmEditorView` 据此渲染知识库选择器。

**身份与鉴权**：`X-KB-User` 头（同 mining 的内网信任头）→ `KbAccessService.authorize()`，**每次执行都校验**，存图不会变成绕过可见性的后门。`KnowledgeBaseMapper.selectAccessibleKbIds` 用 `LEFT JOIN kb_users` 解析用户，无头/未知用户只剩 `public` KB（`mcp_server` 就是这种匿名调用）。任一请求 id 不可读则**整个**请求失败成 `kb_not_found`（400），不做静默子集——返回子集与「那个 KB 没有匹配内容」无法区分；不存在与无权限也共用同一错误，不泄露存在性。

**关键：KB 范围不走 release，走 build。** 因为 KB 挖掘 `publish=False`，域级 active release 里压根没有 KB 内容，走 `resolveActiveScope` 必然落空。`AssetRepository.resolveActiveScope(domain, channel, kbIds)` 在 `kbIds` 非空时改派给 `resolveKbScope()`，从 `asset_build_document_snapshots` 按**每个文档自己 KB 的最新 build** 解析（`AssetBuildDocumentSnapshotMapper.selectLatestKbSnapshots`：`DISTINCT ON (document_id)` 按 `b.created_at DESC`，`b.kb_id = d.kb_id` 防兄弟 KB 的 build 抢答，`selection_status='active'` **必须在外层过滤**否则被后续 build 标 removed 的文档会回落到旧 active 行，`d.domain` 防跨域同名 id）。零命中抛 `no_active_kb_build`。

返回的 `ActiveScope` 有两处刻意的错位：`releaseId` 塞的是 `ActiveScope.kbScopeKey(kbIds)`（让语义缓存按 KB 选择分桶，而不是把所有 KB 混一个桶），`buildId=null`（一个 KB 范围跨多个 build）。`kbIds` 为空时**完全走老路径**，行为逐字不变。

检索 SQL 本身没改（本来就是快照级过滤）。**仍缺的是结果里的 KB 来源标注**——`ContextItem` / `SourceRef` 都没有 `kb_id` 字段，只有 `/api/v1/search` 的 `debug.domain_context.kb_ids` 和范式 trace 的 `kbIds` 属性能看出用了哪些库。

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
- `MainControlClient.fetchDefaultDatabase()` → `GET {baseUrl}/api/v1/system/database` → 取 `default` 块 → `DatabaseConfig`。**启动时**在 `ServingBeans.defaultDataSource` 里调用（5 次重试、间隔 2s，因为 supervisord 的 priority 只保证 control 先启动、不保证它的 HTTP 已 ready）。**刻意不并进 `/serving-config` 快照**：那份快照可热重载，而 Hikari 池的 JDBC URL 建成后不可变，换默认库只能重启 serving。
- `ConfigReloadService.reload()` 顺序喂给 `DomainRegistry.apply → DomainPackReader.apply → DomainPoolManager.invalidate()`；main_control 失败才回落本地文件（`SCENARIO_PACKS_DIR` / `DOMAIN_REGISTRY_PATH`）。**它不碰默认数据源**（与 llm_service 的 reload「不碰 db_config」一致）。
- base-url：`application.yml` 的 `${SERVING_MAIN_CONTROL_BASEURL:http://localhost:8910}`；`ServingProperties.java` 也有一份默认值，**要改一起改**。
- `serving.main-control.default-database-enabled`（默认 true）：默认数据源是否也向控制面要地址。**生产别关**。存在的唯一理由是集成测试——开发机上常开着 main_control(8910)，若让它下发默认库，`ParadigmSchemaInitializer` / `ServingRuntimeSchemaInitializer` 的启动建表 DDL 就会打进生产 `kb_db`。`application-test-pg.yml` 里设成 false，测试只认自己的 `spring.datasource.*`。
- **`parseDatabase` 被复制了两份**：`MainControlClient.parseDatabase` 与 `ConfigReloadService.parseDatabase` 逐字平行（后者 Javadoc 明说 mirrors 前者）。给一侧加字段必须同步另一侧，否则 HTTP 路径与本地回落路径静默分叉。契约由 `MainControlClientTest` 锁住（两个 `@Nested`：`Payload` 按 Python `get_serving_config()` 逐键构造 payload，`DefaultDatabase` 按 `system/database.yaml` 的 `default` 块构造）——**改任何一侧键名，这个测试是唯一能拦住你的东西，编译器拦不住。**（`fetchDefaultDatabase` 复用 `MainControlClient.parseDatabase`，不涉及 `ConfigReloadService` 那份副本。）

### registry 的 `database:` 块是活的（Java 侧真分库）

`DatabaseConfig.isUsable()`：有 `jdbcUrl`，或 `host`+`dbname` 都非空 → true。`DomainPoolManager.createHikariPool()` 为该域建**专用 Hikari 池**并**建池时就 `conn.isValid(3)` 验连接**——失败直接抛 `IllegalStateException("domain_database_unavailable")`（外层 503），不静默回落默认库。`invalidate()` 用 `signature()`（拼 10 个字段）只重建变了的池。未配 `database:` 的域复用 `defaultDataSource`。想让某域回默认库，删掉它的 `database:` 块即可。

> ⚠️ 因为 Python 侧生产上仍写同一个物理库，若把某域的 Java `database:` 指到别的库，Java 会读不到 mining 写的数据。

### 仍然存在的坑（逐条已复核仍在）

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

GET {main_control}/api/v1/system/database          ← 只在启动时拉一次
  → system/database.yaml 原样 JSON，取 default 块
  → MainControlClient.fetchDefaultDatabase() → ServingBeans 建 defaultDataSource
```

`generic` 域没有 inline `database:` → 快照里 `database: null` → 用默认库（即上面那份 `default` 块）。scenario pack 的 `serving:` 段被当根解析（`route_policy`/`query_understanding`/`extractor_rules`/`intent_strategy` 同级，无 ontology/mining 嵌套）。

### mining 侧怎么拿到它

分两块：**service + DB 配置走 HTTP 控制面**（`control_plane.py`，见「配置来源」表）；**域 registry 和 scenario pack 仍直接读文件系统**（`domain_pack.py` 硬编码相对路径 `_REPO_ROOT/main_control_service/config/...`，只读 `ontology:`+`mining:` 两段，不走 HTTP）。默认域来自 registry 顶层 `default_domain`（否则第一个 enabled 域，`get_default_domain()`）。

---

## 数据库

权威 schema 是 Python 侧 `databases/<store>/schemas/*.sql`，由 `mining/infra/pg_schema.py` 按**显式命名的路径常量列表**执行（不是按目录名排序！逐语句、容忍重复对象）。`domain_schema_paths()` 与 `primary_schema_paths()` 都注册 kb schema，所以 kb 表在**每个域库和主库**都建。ontology 仍**必须最后**（FK 指向 `asset_*` 和 `mining_runs`）。

### 表分组（含 workflow + KB 新增）

- **asset_core**：`asset_documents / asset_document_snapshots / asset_document_snapshot_links / asset_raw_segments / asset_raw_segment_relations / asset_retrieval_units / asset_retrieval_embeddings / asset_source_batches / asset_builds / asset_build_document_snapshots / asset_publish_releases`。`003` 加 `domain` 列；`004_kb_isolation` 给 `asset_documents` 加 KB 列并改 UNIQUE；`005_kb_file_meta` 加 `file_size/modified_at`；`006_asset_build_kb` 加 `asset_builds.kb_id`。
- **kb（域库 + 主库都建）**：`kb_users / knowledge_bases / kb_members / kb_folders`；`005_kb_mining_binding` 给 `knowledge_bases` 加 `mining_workflow_id`。
- **mining_control（仅控制库）**：`mining_workflows` / `mining_workflow_versions`。
- **mining_runtime**：`005` 给 `mining_runs` 加 workflow 系列列 + 建 `mining_workflow_node_events`；`006` 加 `preflight_manifest_json`；`007` 加 `mining_runs.kb_id`。
- **asset_core/004_asset_snapshot_workflow_binding（snapshot ↔ workflow 绑定）**：给 `asset_document_snapshots` 加 `workflow_*` 列，唯一性**按 workflow 版本拆成两个部分索引**。**行为变化**：同内容文件在不同 workflow release 下产生**不同 snapshot**。
- **ontology**：`ontology_versions / ontology_node_types / ontology_candidates / ontology_entities / ontology_entity_relations / asset_segment_entity_mentions` 等，DDL 里还 `ALTER mining_runs ADD subloop_stage / ontology_version_id`。

### ⚠️ asset_core 里有两个 `004_`（不是冲突，但迷惑）

`asset_core/schemas/` 下同时有 `004_asset_snapshot_workflow_binding.sql` 和 `004_kb_isolation.sql`。**不是加载冲突**——`pg_schema.py` 按显式命名常量加载，两者在不同时点执行（`004_kb_isolation` 早、`004_asset_snapshot_workflow_binding` 晚），`003_asset_core_domain_isolation` 甚至**故意排在 `004_kb_isolation` 之后**（它要看到 `(kb_id, document_key)` 唯一约束已存在）。所以**编号不再代表加载顺序**，改 schema 时别按文件名数字推顺序，看 `pg_schema.py` 的常量列表。`reset_db.py` 的 `SCHEMA_FILES` 现已与 `pg_schema.py` 对齐（含全部 kb schema、`004_asset_snapshot_workflow_binding`、`006_mining_run_preflight`）。

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
1. **autouse 是 `_guard_test_database`**：`_assert_disposable_database` **拒绝任何库名不以 `_test` 结尾的库**（不连库就 fail），护住生产库 `kb_db`。测试请指向 `kb_db_test` 之类。
2. `_ensure_schema` 是普通 session fixture（**非 autouse**），只有真正 request 它/连接池的测试才触发真 PG 要求（会建全部 schema 含 `databases/kb/`）。KB 测试在 `tests/kb/`，用 async 池（Windows 强制 `WindowsSelectorEventLoopPolicy`）。
3. `_truncate_all()` 除非 `KB_ALLOW_TEST_TRUNCATE=1` 否则**硬 no-op**。PostgreSQL 验收还需 `KB_RUN_POSTGRES_ACCEPTANCE=1`，域隔离测试需独立的 `MINING_TEST_DOMAIN_PG_DBNAME`。

> 本地跑测试的完整环境变量样例见 `docs/开发与发布流程.md`（含 `PG_*` 指向 `kb_db_test` + `KB_ALLOW_TEST_TRUNCATE=1`；虽然运行时不读 `.env`，测试仍用这些环境变量拼 `_test` 库连接）。

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

**虚拟线程不继承 `DomainContext`（Java）。** `DomainContext` 是普通 `ThreadLocal`（非 `InheritableThreadLocal`），而检索都跑在 `newVirtualThreadPerTaskExecutor()` 上。任何 `CompletableFuture.*Async` 提交的任务都必须用 `DomainContext.wrapRunnable/wrapCallable/wrapSupplier` 或显式 `set()`。现有调用点都已包好（`ParadigmExecutor` 每节点、`RetrievalOrchestrator` 的路由扇出、`SearchService` 的变体循环与子查询循环），**新加并行分支时别漏**——漏了不会报错：`DomainRoutingDataSource` 把 null 域静默当 default，配了 inline `database:` 的域会悄悄查默认库，而 `EntityGraphRouteRetriever`（除 `DomainContext` 外没有域来源）会恒返回空。

**serving 启动现在硬依赖 main_control 可达**（或显式配 `SPRING_DATASOURCE_URL`）。`ServingBeans.defaultDataSource` 要先拿到 `system/database.yaml` 的 `default` 块才能建池，拉不到且无兜底就抛 `default_datasource_unresolved` 启动失败。这是**故意的**：以前它硬编码地址，改配置不生效、服务照常起来、悄悄连着旧库，比起不来难查得多。容器里 supervisord 的 priority 已保证 control(10) 先于 serving(30)，另有 5 次 ×2s 重试兜住 control 的 HTTP 尚未 ready。跑 IntelliJ / 裸 `mvn spring-boot:run` 时记得先起 8910，或设 `SPRING_DATASOURCE_URL`。

**新增 Java 算子只需打 `@Component`**，`OperatorRegistry` 靠构造注入自动收集，type 重复启动失败。前端按算子 `paramSchemaJson`（JSON Schema draft-07）自动渲染参数表单，加参数只改后端 schema。

**mining workflow 的 manifest 是运行时强校验、不信任的。** `runtime._verify_and_build_plan` 重算 graphHash + 每节点 paramsHash 并拒绝任何漂移；编辑/重发布 workflow 不会回溯改已绑定的 run。改算子 catalog 或 option 模型会改 `catalogVersion`/`paramsHash`，注意与已发布版本的兼容。

**mining 的 `execution_engine` 是每 run 不可变、从 DB 读的。** legacy run 永不自动升级；`_run_legacy`/`StreamingPipeline` 是活代码（回退引擎），不能当废弃删。切换/回滚引擎绝不改已有 run 的 `execution_engine`/workflow 绑定字段/manifest/节点事件。

**mcp_server 直连 serving、有硬编码远程 IP 兜底。** `BACKEND_URL` 默认 `http://121.89.90.178:8081`，只有 supervisord 把它覆盖成 localhost。它只暴露一个 tool `search_knowledge`（`POST /api/v1/search` 透传），transport 默认 `streamable-http`（模块 docstring 说 stdio 是过时的）。

**改了挖掘算子/范式/域包检索策略后，已挖文档不会自动重生**（内容哈希去重 → SKIP 复用旧 snapshot）。要应用新逻辑：重启 mining（加载新代码）+ 前端勾「强制重挖」（或换范式时自动 force_redo）。常见坑：① **检索单元 `table_row` 爆量**——已发布 workflow 的 manifest 编译期冻结算子参数，但域包 `retrieval_policy.table_row:"off"` 是**域级权威、会覆盖 manifest**（改算子默认值对已发布 manifest 无效）；② **移动文件后状态卡 `uploaded`**——移动/改名必须同步 `document_key`（见「KB 层」）；③ **挖掘请求延迟数秒**——后台线程冷导入 `mining.jobs.run`（~2.5s 占 GIL），已在 lifespan 预热，别再在请求路径里懒导入重模块。完整运维见 `docs/开发与发布流程.md`。

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
| `docs/开发与发布流程.md` | ✅ **准确**，分支/测试/PR（merge commit 不 squash）/部署/挖掘运维的权威流程。改代码前应读（尤其「配置不读 .env」「测试只打 `_test` 库」「改挖掘逻辑要强制重挖」） |
| `docs/kb-management-design.md`、`kb-management-implementation-plan.md`、`kb-filesystem-plan.md` | ✅ KB 中心化的需求/技术设计/实现计划，与已落地代码基本对得上，可作背景。注意仍是设计视角，个别表名/细节以源码为准 |
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

新人最短上手路径：先读 `docs/开发与发布流程.md`（含配置/测试/部署铁律），起 `main_control_service`（配置中心，必须最先）；KB 层读 `kb-management-design.md`，挖掘线读 `mining-workflow-rollout-runbook.md`，检索线读 `ontology-retrieval-explained.md` + `检索范式使用说明.md`，其余文档一律对着源码读。`docker/nginx.conf` 里的三行注释比 `FRONTEND-PLAN.md` 和 `kb-ui/README.md` 加起来都准确。
