# ADR-0001：已锁定的地基不变量

- 状态：**Accepted**（SRS §15.1 已固定，本 ADR 仅做固化归档）
- 日期：2026-08-13
- 上游：`docs/文档解析平台化-能力规格与工作拆解.md` §15.1、§2、§10

## 背景

SRS 第 15 节「OPEN QUESTIONS」明确列出了 10 条「已固定、不再开放」的决策。这些决策是后续所有工作包（WP1A–WP14）的共同前提，本 ADR 把它们集中固化，避免每个 WP 重复解释同一组身份与边界语义。

## 决策

### D1 对象存储 = MinIO，业务经 Object Store Port 调用 S3 API

正式对象存储固定为 MinIO。业务代码（File Management、Parser、Serving）一律通过项目自有的 Object Store Port 访问，禁止直接依赖 MinIO SDK 类型、bucket 命名或凭据。MinIO 的 endpoint / access key / secret / TLS / bucket prefix / SSE-KMS 配置由主控配置或 Secret 管理，不进入 workflow manifest、数据库业务行或日志。

**后果**：MinIO 可被替换为任意 S3 兼容实现而不改动业务层；但要求所有对象读写先抽象成 Port 接口。

### D2 PostgreSQL 是业务事实源，MinIO 只存字节

文件目录、权限、Document 当前态、知识 Snapshot、对象引用清单、配额全部以 PostgreSQL 为准。MinIO 仅保存对象字节及其存储侧 metadata。`/` 只是 object key 前缀，不构成用户文件树；`kb_folders` 是逻辑目录唯一事实源。

**后果**：不能用 `list_objects(prefix=...)` 实现用户文件树；改名/移动只改 PostgreSQL，不搬对象字节。

### D3 新文件 MinIO-only；`storage_path` 退为迁移期 legacy 字段

新上传与在线编辑从切换日起只写 MinIO，禁止继续产生新的本地路径依赖。`storage_path` 仅用于存量文件的迁移回填与双读回退，完成退场后不再作为身份或下载依据。

**后果**：现存 `storage_path` 耦合（约 39 个文件，含 Java serving `RawFileService`）必须在 M1 逐一切换到对象引用；切换期内允许 MinIO 优先、本地回退。

### D4 不建设文档版本管理

不提供版本列表、版本回滚、`asset_document_source_versions` 表。Document 只维护「当前内容」：`storage_object_id + raw_hash + content_revision`。在线编辑采用 copy-on-write 写新对象 + 原子切换当前指针，这是并发与引用保护，不是产品版本能力。MinIO 自身 versioning 仅作运维防护，不向业务暴露为文档版本。

**后果**：解析器或切片策略升级产生新 Snapshot；旧 Release 仍引用旧 Snapshot，历史可复现。无需维护多套版本实体。

### D5 `asset_document_snapshots` 是唯一知识版本根

Parse IR、原子元素、segments、Structured/Binary Assets、Quality、后续 Knowledge Artifact 全部归属于 Document Snapshot。不再新增 Parse Result / Segment Set 并行版本体系。同一文档可因 parser/workflow/编译策略变化形成多个 Snapshot；哪个进入 Build/Release 由现有 Build 选择 Snapshot 决定。

**后果**：知识历史只有一条版本线（Snapshot）；Parse IR 可作为 MinIO artifact 被新 Snapshot 复用，但复用不引入独立版本表。

### D6 Parser 只消费 Mining Run 冻结输入

知识更新开始时，Mining Run Document 冻结 `document_id + storage_object_id + raw_hash + content_revision`。解析器只读这次绑定的对象。需要本地路径的第三方库由 Materializer 创建 run-scoped 临时文件，校验 hash 后使用，run 结束立即清理。临时路径不是资产字段，不写入 Snapshot / provenance / Build。

**后果**：解析期间用户在线编辑不会污染本次 Run；完成时若文档当前 revision 已变，Run 标记 SUPERSEDED，不自动发布。

### D7 Document 身份永久；路径仅派生

`asset_documents.id` 是永久文档身份。`folder_id` / 显示名 / 路径均可变；路径派生的 `document_key` 只保留为迁移期兼容别名，不作为长期身份。

### D8 文档更新只令知识 outdated；不自动切线上

文档更新把知识状态标记 outdated，但不修改当前 Release 使用的旧 Snapshot。只有知识更新成功并通过新 Build/Release 才切换线上知识。

### D9 Section View / Evidence Bundle 是动态读模型，不新建版本体系

Knowledge Access 的各类 View 默认从 Snapshot 下的 Container/Element/Relation/Structured Asset 动态组装，缓存可删可重建，不构成事实源或版本根。

### D10 Knowledge Artifact 是 Snapshot 的场景化子资产

高级知识制品（规则 / SOP / FAQ 等）归属于 `document_snapshot_id`，必须通过 Evidence Link 回指原子元素/span。Snapshot 进入 READY 后禁止追加或覆盖 Artifact；新增场景或重新编译必须产生新 Snapshot。

### D11 Parse IR 为解析事实源；Markdown 仅为导出视图；Parse 与 Segment 分离

Parse IR（Container + Element + Evidence Span + Relation + Structured Asset）是 Snapshot 内的解析事实。Markdown / HTML / CSV 都是 rendered view。`parse_segment` 复合算子拆分为 `document_parse`（产 Parse IR）与 `segment_compile`（从 IR 编译切片）两个固定算子。切片只产生映射，不回写原子元素。

## 后续动作

本 ADR 的所有条款直接进入 WP0 契约文档与 WP2 数据模型，无需额外审批。任何对上述条款的修订必须新增 ADR 并标记本 ADR 为 `Superseded`。
