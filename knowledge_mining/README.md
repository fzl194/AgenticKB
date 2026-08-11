# Knowledge Mining — 架构文档

## 支持的知识库输入文件

- Word：`.doc`、`.docx`。旧版 `.doc` 通过本机 LibreOffice（Windows 开发环境可回退到 Word COM）转换为 `.docx` 后进入结构化解析。
- Excel：`.xls`、`.xlsx`。工作簿会确定性转换为 Markdown 中间态，再复用现有 Markdown 解析、分段和入库流程。
- 其他现有格式：Markdown、文本、HTML、PDF、ZIP、CHM、HDX。

Excel 会保留工作表顺序、隐藏状态、合并单元格上下文和可用的公式缓存值，并按工作表/表格区域生成 Markdown。公式没有已保存结果时会产生可定位到工作表和单元格的告警。当前忽略图表、图片和宏，不支持密码保护文件。

预处理结果使用 `success`、`partial`、`failed` 状态；错误码、错误详情、告警与 Excel 摘要通过运行文档列表/详情 API 返回。失败诊断写入现有 `metadata_json`，不需要数据库结构变更。离线安装说明见 [Word 与 Excel 离线部署依赖](../docs/deployment/offline-document-dependencies.md)。

## 挖掘 Workflow 算子化（v3）

当前实现同时保留 `legacy` Pipeline 和新的 Workflow Runtime。Workflow 定义、草稿、不可变发布版本存放在全局 Control 数据库；Run、冻结 Manifest、节点事件、审核状态、资产、Build 和 Release 仍存放在所选 Domain 数据库。Workflow 本身不区分 Domain，运行时绑定、审核和资产严格区分 Domain。

上传执行链为：上传文件形成批次 → 选择已发布 Workflow 的精确版本 → 创建绑定冻结 Manifest 的 Run → 执行文档 DAG 与全局 DAG → 审核（如适用）→ Build/Release 发布。若新提交处于 `workflow` 模式且没有传 Workflow，服务端默认绑定 `system-full-baseline.current_version`；不会在运行中追随后续发布的新版本。

新提交默认使用 `workflow` 模式，无需环境变量。只有应急回退时才需要显式配置 `MINING_RUN_SUBMISSION_ENGINE=legacy`。

- `legacy`：兼容旧请求的 `input_path`；若显式传 Workflow 字段，返回 `workflow_engine_unavailable`，且不创建 Run。
- `workflow`：兼容旧请求不传 Workflow，此时默认 FULL；新请求可使用 `upload_batch_id + workflow_id + workflow_version` 精确绑定。
- 配置只影响新 Run。已创建 Run 由数据库中的 `execution_engine` 决定分发，禁止在回滚时重写该字段、Workflow 版本、Manifest 或节点事件。

16 个内置算子及 FULL、篇章线、本体线、最小化四个模板由 `mining/workflow/` 提供。管理 API 支持草稿乐观锁、服务端校验、发布不可变版本、复制、归档、历史预览与恢复为新草稿。详细上线步骤见 [挖掘 Workflow 灰度发布手册](../docs/mining-workflow-rollout-runbook.md)。

> 离线知识挖掘引擎：原始文档 → 结构化知识资产
> 版本：v2.0（三层架构重构）| 数据库：asset_core + mining_runtime | Pipeline：9 阶段 | 152 tests

## 1. 系统定位

`knowledge_mining` 将原始技术文档（Markdown、纯文本等）经过 9 阶段 Pipeline 转化为结构化知识资产（`asset_core.sqlite`），供 `agent_serving` 在线检索。

**核心价值：** 把非结构化技术文档变成 Agent 可直接消费的结构化知识。

## 2. 架构设计

### 2.1 三层模块架构

Mining v2.0 采用严格单向依赖的三层架构：

```
contracts/  ←  infra/  ←  stages/  ←  pipeline.py  ←  jobs/run.py
   │              │           │
   │              │           └── 每个 stage 实现 contracts 中的 Protocol
   │              └── 使用 contracts 中的 models
   └── 零外部依赖（纯 dataclass + Protocol）
```

禁止反向依赖：stages/ 不 import contracts/，infra/ 不 import stages/。

```
knowledge_mining/mining/
├── contracts/                    # Layer 1: 纯数据模型 + Protocol 接口
│   ├── models.py                 # 12 个 frozen dataclass，对齐 SQL schema
│   └── protocols.py              # 8 个 Protocol（Stage, Segmenter, Enricher, ...）
│
├── infra/                        # Layer 2: 共享基础设施
│   ├── db.py                     # AssetCoreDB + MiningRuntimeDB 双库适配器
│   ├── llm_client.py             # LLM 服务 HTTP 客户端（submit → poll）
│   ├── llm_templates.py          # LLM prompt 模板构建
│   ├── domain_pack.py            # DomainProfile 加载（场景知识外置）
│   ├── embedding.py              # Zhipu Embedding-3 生成器
│   ├── extractors.py             # EntityExtractor / RoleClassifier 实现
│   ├── text_utils.py             # CJK 分词、归一化、相似度
│   ├── hash_utils.py             # SHA256 快照去重
│   └── structure/                # markdown-it → SectionNode 树
│
├── stages/                       # Layer 3: Pipeline stage 实现
│   ├── __init__.py               # Stage registry + auto-discovery
│   ├── parse.py                  # 文档解析（Markdown/Text/Passthrough）
│   ├── segment.py                # 结构化分块（9 block_type + 11 semantic_role）
│   ├── enrich/                   # 实体增强（规则 v1 + LLM v2）
│   ├── relations/                # 关系构建（structural + discourse RST）
│   ├── retrieval_units/          # 检索单元生成（4 种载体）
│   ├── eval.py                   # 数据质量评估
│   └── publishing.py             # Build + Release 发布
│
├── pipeline.py                   # Pipeline 引擎（MiningPipeline + StreamingPipeline）
├── ingestion/                    # 文件扫描
├── snapshot/                     # 共享快照管理
├── runtime/                      # 运行时状态跟踪 + 断点续跑
└── jobs/run.py                   # 编排入口
```

### 2.2 Stage Registry（热插拔版本选择）

每个 stage 通过 `stage_name` + `stage_version` 注册：

```python
from knowledge_mining.mining.stages import get_stage, list_stages

# 查看所有已注册的 stage
list_stages()
# {'parse': {'1': ParserStage}, 'segment': {'1': DefaultSegmenter},
#  'enrich': {'1': RuleBasedEnricher, '2': LlmEnricher}, ...}

# 获取最新版本的 enrich stage
enrich_cls = get_stage('enrich')       # → LlmEnricher (v2)

# 获取特定版本
rule_enricher = get_stage('enrich', '1')  # → RuleBasedEnricher (v1)
```

### 2.3 Protocol 合并

8 个 Protocol 统一定义在 `contracts/protocols.py`：

| Protocol | 职责 |
|----------|------|
| `Stage` | 基础 Protocol：`stage_name`, `stage_version`, `execute()` |
| `Segmenter` | `segment(tree, profile) -> list[RawSegmentData]` |
| `RelationBuilder` | `build(segments) -> (relations, seg_ids)` |
| `Enricher` | `enrich_batch(segments) -> list[RawSegmentData]` |
| `QuestionGenerator` | `generate(segment) -> list[str]` |
| `Contextualizer` | `contextualize(text, context) -> str` |
| `EntityExtractor` | `extract(text, entities) -> list[dict]` |
| `RoleClassifier` | `classify(text, block_type) -> str` |

## 3. 两阶段 Pipeline

### Phase 1: Document Mining（文档级，可并行）

```
ingest → parse → segment → enrich → build_relations → build_retrieval_units → select_snapshot
```

### Phase 2: Build & Publish（全局串行）

```
assemble_build → validate_build → publish_release
```

## 4. 九阶段详解

| 阶段 | 输入 | 输出 | 核心逻辑 |
|------|------|------|---------|
| S1 Ingest | 文件夹路径 | `list[RawFileData]` | 递归扫描，双重 hash，跳过未变文件 |
| S2 Parse | RawFileData.content | SectionNode 树 | Markdown/Text/Passthrough 工厂 |
| S3 Segment | SectionNode + DocumentProfile | `list[RawSegmentData]` | Heading 独立成段，9 block_type + 11 semantic_role |
| S4 Enrich | Segments | 增强后 Segments | 实体提取 + 角色分类 + 表格元数据（v1 规则 / v2 LLM） |
| S5 Relations | Segments + ID 映射 | `list[SegmentRelationData]` | 结构关系 + 语篇 RST 关系（24 种标签） |
| S6 Retrieval Units | Segments + 可选 LLM | `list[RetrievalUnitData]` | raw_text + contextual_text + entity_card + generated_question |
| S7 Snapshot | 文档内容/配置 | (doc_id, snapshot_id) | 三层模型 + SHA256 去重 |
| S8 Build | Snapshot 决策列表 | build_id | 自动 full/incremental 判断 |
| S9 Release | build_id | release_id | 激活新 release，退役旧 release |

## 5. 数据库边界

| 数据库 | 表数 | 职责 | 写入方 |
|--------|------|------|--------|
| `asset_core.sqlite` | 11 | 知识资产：documents, snapshots, segments, relations, units, embeddings, builds, releases | Mining 写，Serving 只读 |
| `mining_runtime.sqlite` | 3 | 过程状态：runs, run_documents, stage_events | Mining 写 |

**CQRS 边界：** Mining 是写入端，Serving 只通过 `asset_publish_releases` 找到 active release 后只读查询。两个子系统不互相 import，仅通过 SQL schema 对接。

## 6. LLM 集成架构

```
Mining Pipeline
  → llm_client.py (submit batch → poll results)
  → LLM Service (:8900, SQLite-backed worker pool)
  → Provider (DeepSeek / OpenAI / Zhipu)
```

LLM 集成点：

| 阶段 | LLM 用途 | 模式 | 状态 |
|------|---------|------|------|
| retrieval_units | 问题生成（generated_question） | Batch async | 已实现 |
| retrieval_units | 上下文增强（Contextual Retrieval） | Batch async | 已实现 |
| enrich | 语义实体提取 + 角色分类 | Batch async | 已实现 (v2) |
| relations | 语篇关系提取（24 RST labels） | Batch async | 已实现 |

**降级策略：** LLM Service 不可用时自动降级为 NoOp，不阻塞主流程。所有 LLM 调用通过 `idempotency_key` 防止重复。

## 7. Domain Pack（场景知识外置）

`domain_packs/<domain>/domain.yaml` 承载场景特定知识：

```yaml
entity_types: [command, network_element, parameter, ...]
strong_entity_types: [command, network_element]
retrieval_policy:
  max_questions_per_segment: 2
  generated_question_enabled_roles: [concept, procedure_step, parameter]
skip_block_types_for_question: [heading]
```

DomainProfile 在 Pipeline 启动时一次性加载，所有 stage 通过 `cfg.domain_profile` 访问。

## 8. Shared Snapshot 模型

```
document (逻辑身份) → snapshot (内容快照, hash 唯一) → link (映射)
```

- 归一化：CRLF → LF → 去尾空白 → 去空行 → SHA256
- 相同内容共享 snapshot，UPDATE 清理旧数据，SKIP 直接复用

## 9. 数据质量体系

### 已实现

- **Content Quality Gate**：LLM 驱动的内容质量评估（入模前拦截导航/目录/稀疏片段）
- **Question Post-Validation**：LLM 输出后校验可回答性
- **Qn Prefix Removal**：generated_question.title 不含 Q1/Q2 前缀
- **Retrieval Unit Budget**：raw_text ≥ 55%, generated_question ≤ 20%
- **LLM Provenance**：task_id 全链路追溯
- **Data Quality Eval**：真实 SQLite 产物级评估

### 参考：工业级评估框架

| 框架 | 适用场景 | 我们的对标 |
|------|---------|-----------|
| Anthropic Contextual Retrieval | chunk 上下文增强 | 已实现 LLMContextualizer |
| Microsoft GraphRAG indexing | 实体/关系/社区分层构建 | 部分对标（entity_card + structural + discourse relations） |
| LlamaIndex Property Graph | schema 约束的属性图抽取 | DomainProfile 约束实体类型 |
| Ragas Metrics | RAG 质量评估 | eval.py 提供 keyword recall + data quality audit |
| Haystack Evaluation | pipeline/component 评估 | stage_events 追踪每个 stage 耗时/成功 |

## 10. 如何运行

```python
from knowledge_mining.mining.jobs.run import run, publish

# 完整 pipeline
result = run(
    "/path/to/input/folder",
    asset_core_db_path="asset_core.sqlite",
    mining_runtime_db_path="mining_runtime.sqlite",
)

# 仅 Phase 1
result = run("/path/to/input", phase1_only=True)

# 发布 release
publish(result["run_id"])
```

## 11. 测试

```bash
python -m pytest knowledge_mining/tests/ -v
# 152 个测试覆盖：contracts, infra, stages, pipeline, runtime, 评估
```

## 12. 当前成熟度评估

### 已完成

- 完整的 9 阶段 Pipeline（端到端）
- 三层架构：contracts → infra → stages（严格单向依赖）
- Stage Registry（版本选择 + 自动发现）
- LLM 集成：问题生成、上下文增强、语义实体提取、语篇关系
- Domain Pack 场景知识外置
- 数据质量门：Content Quality Gate, Question Post-Validation, Unit Budget
- Shared Snapshot 共享去重
- 增量构建 + 版本发布
- 断点续跑支持
- 152 个测试

### 距工业级的差距（诚实评估）

以下差距基于 Codex 工业级审查（2026-04-29），按优先级排列：

#### P0 — 运行正确性

| 问题 | 说明 | 业界对标 |
|------|------|---------|
| 并发结果与文档绑定不可靠 | `StreamingPipeline.process_all()` 不保序，可能把 A 文档结果写进 B 文档 | LangGraph / Airflow 均要求 stable identity binding |
| run status 真相源分裂 | API 返回 `failed`，数据库却写 `completed` | 工业系统要求单一真相源（Kubernetes: etcd only） |

#### P1 — 发布质量门

| 问题 | 说明 | 业界对标 |
|------|------|---------|
| validate_build 只检查结构 | 不验证 retrieval unit 质量、LLM provenance、导航污染 | Microsoft GraphRAG: quality gates per indexing stage |
| data quality eval 未接入 release gate | 质量评估存在但不是发布必要条件 | Databricks: data quality constraints block job completion |
| Domain Pack 不是唯一合同源 | 部分模块仍绕过 DomainProfile 私读 YAML | LlamaIndex: schema 作为唯一 extraction 合同 |

#### P1 — 架构合同

| 问题 | 说明 | 业界对标 |
|------|------|---------|
| stage registry 未接入主执行链 | run.py 直接 import 具体实现，registry 形同虚设 | Airflow: DAG registry 驱动实际执行 |
| stage completion event 缺 run_document_id | 无法把 completed/failed 事件稳定归属到文档 | 可观测性要求: OpenTelemetry trace_id 贯穿 |

## 13. 演进路线图

### Phase A: 主链可信生产线（当前最优先）

- [ ] 修复 StreamingPipeline 结果-文档绑定（stable identity）
- [ ] 统一 run status 真相源（runtime DB 是唯一权威）
- [ ] 补全 stage completed event 的 run_document_id
- [ ] 新增并发乱序写回回归测试

### Phase B: 质量门成为 Release Gate

- [ ] run_data_quality_eval() 接入 build/release 主链
- [ ] 定义 hard gate vs warning 清单
- [ ] 增加 golden regression 集
- [ ] LLM provenance、navigation pollution、question/title 规则纳入 gate

### Phase C: 平台化合同闭环

- [ ] 扩充 DomainProfile，停止核心模块私读 YAML
- [ ] stage registry 真正接入主执行链
- [ ] 默认行为从 cloud_core_network 特化改为 generic baseline

### 不建议继续做的事（底盘未收口前）

- 继续增加 retrieval unit 类型
- 继续扩更多 LLM stage
- 继续堆复杂 rerank / graph / embedding 策略
- 继续强化 registry 的概念层包装

## 14. 相关文档

- [架构演示](architecture.html)
- [Asset Core Schema](../databases/asset_core/schemas/001_asset_core.sqlite.sql)
- [Codex 工业级数据质量审查](../docs/analysis/2026-04-28-v11-knowledge-mining-industrial-data-quality-codex-review.md)
- [Codex 三层架构审查](../docs/analysis/2026-04-29-v11-knowledge-mining-3layer-architecture-codex-review.md)
- [Serving 检索架构](../agent_serving/README.md)
