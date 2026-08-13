# Asset Core Schema

> 当前版本：v1.2
> SQLite 契约：`databases/asset_core/schemas/001_asset_core.sqlite.sql`  
> Generic SQL 基线：`databases/asset_core/schemas/001_asset_core.sql`

## 目标

`databases/asset_core/schemas/` 定义 CoreMasterKB 当前共享的知识资产数据库契约。

这版 schema 对应当前统一口径：

```text
共享内容快照
  -> build（document -> snapshot 的知识视图）
  -> release / publish（哪个 build 当前对外生效）
```

Serving 主读取链路是：

```text
active release
  -> build
  -> selected document snapshots
  -> retrieval_units
  -> source_refs_json
  -> raw_segments / raw_segment_relations
```

## 当前边界

本目录只定义 **Knowledge Asset DB** 契约。

不在这里定义：

- Mining Runtime DB
- LLM Runtime DB
- 本体 / fact / graph
- 个性化规则层

## 表总览

| 层 | 表 | 作用 |
|---|---|---|
| 输入身份 | `asset_source_batches` | 一批输入资料的身份 |
| 文档身份 | `asset_documents` | 逻辑文档身份 |
| 共享内容 | `asset_document_snapshots` | 可被多文档共享引用的内容快照 |
| 文档-快照映射 | `asset_document_snapshot_links` | 哪个文档在本次输入下引用了哪份快照 |
| 原始事实 | `asset_raw_segments` | snapshot 下的原始片段 |
| 上下文关系 | `asset_raw_segment_relations` | snapshot 下的片段关系 |
| 检索入口 | `asset_retrieval_units` | Serving 主检索对象 |
| 全文索引 | `asset_retrieval_units_fts` | SQLite FTS5 |
| 向量挂载 | `asset_retrieval_embeddings` | 预留向量索引 |
| 构建视图 | `asset_builds` | 一次完整知识构建 |
| 构建清单 | `asset_build_document_snapshots` | build 中 document -> snapshot 的选择 |
| 发布控制 | `asset_publish_releases` | 哪个 build 当前在某个 channel 上 active |
| 对象存储 | `asset_storage_objects` | MinIO 对象定位、hash、size、state、retention（008，SRS §8.5/§3.1A） |
| 上传会话 | `asset_upload_sessions` | 上传状态机与幂等键（008，SRS §8.5/§3.1B） |
| 对象引用索引 | `asset_storage_object_refs` | GC/审计加速冗余引用（008，SRS §8.5） |
| 文件审计 | `asset_file_audit_events` | append-only 文件管理审计流（008，SRS §8.5） |
| 存储配额 | `asset_storage_quotas` | KB 级配额与乐观并发（008，SRS §8.5） |
| 存储副作用 outbox | `asset_storage_operations` | promote/cleanup/GC/通知（008，SRS §8.5） |

## 设计原则

### 1. `asset_document_snapshots` 是共享内容快照

它不是“文档专属快照”，而是：

```text
一份可被一个或多个 document 共享引用的不可变内容对象
```

复用判断的核心是：

```text
normalized_content_hash
```

### 2. `asset_document_snapshot_links` 承载文档专属信息

路径、原始来源、文档级 scope/tags，不放到共享 snapshot 本体里，而放到：

```text
asset_document_snapshot_links
```

这样才能同时保留：

- 内容复用
- 文档身份
- 来源回溯

### 3. Build 按 document 建，但选择 snapshot

`asset_build_document_snapshots` 的语义是：

```text
在 build_X 中，逻辑文档 D 采用 snapshot S
```

这意味着：

- 多个 document 可以在同一个 build 中指向同一个 snapshot
- build 仍然是文档视图，不是全局去重知识点视图

### 4. Publish 是 `release -> build`

`asset_publish_releases` 不是日志，也不是换文件动作，而是：

```text
把某个已验证 build 挂到某个 channel
```

Serving 永远只读当前 channel 的 active release。

## 当前最关键的三张表

### `asset_document_snapshots`

决定“内容是否复用”。

### `asset_build_document_snapshots`

决定“本次知识视图里每个文档采用哪份内容快照”。

### `asset_publish_releases`

决定“哪个 build 当前正式对外生效”。

如果这三张表的语义稳定，这套资产库主链就稳定。 

## Domain 隔离与升级

新建数据库中，`asset_documents` 的身份唯一性是
`(domain, document_key)`，`asset_document_snapshots` 的内容复用边界是
`(domain, normalized_content_hash)`。不同 domain 可以使用相同文档键或内容哈希，
同一 domain 内仍保持唯一。

PostgreSQL 会在基础 schema、runtime schema 之后，以单个事务执行
`003_asset_core_domain_isolation.sql`。迁移根据 source batch、build、release 和
mining run 的来源回填旧数据：唯一 domain 保留实际值，多 domain 来源使用
`__legacy_shared__`，没有来源使用 `__legacy_unscoped__`。build selection 仅在
同 domain、同 document/snapshot 且不晚于 build 创建时间的最近 link 唯一时，
回填 `source_batch_id`；歧义数据保持 `NULL`。

每个 `(domain, channel)` 最多有一个 active release。Generic SQL 与 SQLite 文件
只定义新建库基线；SQLite 不执行 PostgreSQL 的旧数据来源推断与回填迁移。

`003_asset_core_domain_isolation.sql` 会在一个事务中更新历史资产并重建约束，执行时
会持有相关表锁。生产升级应安排维护窗口，并在部署会话中配置合适的
`lock_timeout`，避免长时间等待业务事务；超时后可安全重试整段迁移。

迁移只自动解析本次新增 domain 列后仍为 `NULL` 的历史资产。`default` 同时也是合法
运行时 domain，因此已存在的 `domain='default'` 会原样保留；无法判定来源的非标准
部分迁移状态需要在升级前人工审计，不能仅凭字段值自动改写。

## 迁移文件索引

| 文件 | 作用 |
|---|---|
| `001_asset_core.sqlite.sql` | SQLite 新建库基线（asset_core v1.1） |
| `001_asset_core.sql` | Generic SQL 基线（v1.1） |
| `002_asset_core_postgresql.sql` | PostgreSQL 新建库基线 + v1 幂等升级（v1.1） |
| `003_asset_core_domain_isolation.sql` | PostgreSQL：domain 隔离与存量数据回填（单事务） |
| `004_asset_snapshot_workflow_binding.sql` | snapshot 绑定 workflow_id/version/graph_hash + partial uniques |
| `004_kb_isolation.sql` | asset_documents 增 kb_id/storage_path/directory_path/owner_id + (kb_id,document_key) 唯一 |
| `005_kb_file_meta.sql` | asset_documents 增 file_size/modified_at |
| `006_asset_build_kb.sql` | asset_builds 增 kb_id |
| `007_asset_block_type_image.sql` | raw_segments/retrieval_units 的 block_type 增 image |
| `008_object_storage_foundation.sql` | SQLite：对象存储地基新表 + asset_documents/snapshots/links 扩展（SRS §8.3/§8.3A/§8.5） |
| `008_object_storage_foundation_postgresql.sql` | PostgreSQL 版本（与 008 对齐；JSONB/TIMESTAMPTZ/DO 块守卫） |

008 为 M0 增量（ADR-0003 D-004）：只加表/列、UNIQUE/CHECK，不加 FK 硬约束、不改任何读写。
FK、真实切换、存量回填在 M1 进行（SRS §8.8 Phase 2-4）。nullable `object_version_id`
的唯一性经表达式索引 `COALESCE(object_version_id,'')` 归一（SRS §8.5 末段）。
