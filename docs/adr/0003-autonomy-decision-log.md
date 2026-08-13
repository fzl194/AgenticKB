# ADR-0003：自主执行决策日志

- 状态：**Accepted**（持续追加）
- 日期：2026-08-13 起
- 背景：用户全权委托实施方在 `feat/doc-parse-platform-m0` 分支上端到端完成文档解析平台化，明确「所有的决策请基于原始文档来决策」「所有决策需要留档写到 md 中」「我不决策任何信息」。
- 本文件记录实施过程中**所有非 SRS 显式条款、但必须拍板的实现级决策**，每条标注 SRS 依据或「SRS 未覆盖，按下述理由」。重大架构决策另起独立 ADR；本日志登记编号 `D-NNN`。

> 格式：`D-NNN | 主题 | 决策 | 依据`

---

## D-001 ｜ 契约层风格：frozen dataclass + Protocol，Parse IR 用 jsonschema 校验
- **决策**：Parse IR 与 Object Store Port 的类型定义沿用 `knowledge_mining/mining/contracts/` 的 Layer-1 约定——`@dataclass(frozen=True)` + `VALID_*` frozenset 常量 + `@runtime_checkable Protocol`，**不引入 Pydantic 模型**。SRS 提到的「schema validation」用 `jsonschema`（已是项目依赖）+ 自定义引用完整性校验函数实现。
- **依据**：`contracts/__init__.py` 声明「Zero external dependencies — only stdlib dataclasses and typing.Protocol」；现有 `ContentBlock/SectionNode/RawSegmentData` 均为 frozen dataclass，新 IR 须与之共存并最终替换，风格必须一致。SRS §7/§C00 关心的是「契约 + 校验」，未强制 Pydantic。Python 3.11+。

## D-002 ｜ Object Store Port 双 adapter：Fake（文件系统）+ MinIO
- **决策**：定义 `ObjectStorePort`（Protocol），提供两个实现：`FakeObjectStore`（本地目录模拟 S3 语义，bucket=目录、key=文件、SHA-256 校验、presigned 用短时 token、multipart 用追加）用于全部测试与开发；`MinioObjectStore`（minio SDK）用于生产，未部署时不被测试调用。`make_object_store(config)` 按 `provider=minio|fake` 选择。业务代码只依赖 Port。
- **依据**：用户「由于我还没部署 MINIO，所以这部分需要你做一些假的测试」；SRS §C00「公共端口只暴露项目类型……MinIO SDK response 不得越过 adapter 边界」；§8.7「不能只把 storage_path 换成 s3://」。

## D-003 ｜ 新表归属：文件/存储/快照扩展全部进 asset_core，目录扩展进 kb
- **决策**：`asset_storage_objects / asset_upload_sessions / asset_storage_object_refs / asset_file_audit_events / kb_storage_quotas / asset_storage_operations(outbox)` 及 `asset_documents / asset_document_snapshots / asset_document_snapshot_links` 的扩展字段统一落在 `databases/asset_core/schemas/008_*.sql`（+ `_postgresql.sql`）；`kb_folders` 扩展落 `databases/kb/schemas/008_*.sql`。遵循现有「sqlite + postgresql 双迁移」约定。
- **依据**：`asset_*` 表历史均在 asset_core（001-007）；`kb_folders` 在 kb（004）。SRS §8.5 表命名以 `asset_` 前缀。

## D-004 ｜ 迁移期读写策略：M0 只加表/列，不改任何读写路径
- **决策**：M0 产出的 DDL 全部为 `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`（增量、幂等），不修改 `DocumentService` / mining jobs / serving 任何现有读写。现有 `storage_path` 链路保持原样。真实切换在 M1。
- **依据**：SRS §8.8「Phase 1 建表与 MinIO 上线：只加新表/列，不改变现有读写」；§2.3 兼容不变量。

## D-005 ｜ 提交策略：按里程碑在分支本地提交，不推送远端
- **决策**：在 `feat/doc-parse-platform-m0` 分支按里程碑（M0、M1…）边界做本地 git commit，不执行 `git push`（用户未授权外发，且涉及远端凭据）。master 保持不动。auth.yaml 的无关改动不带入提交。
- **依据**：用户「你所有代码都放在分支上，确保主分支不受干扰」；全局规则「Commit or push only when the user asks」——提交到已授权的功能分支属于「按要求放代码」，push 属外发动作需另行授权。

## D-006 ｜ 测试范围：契约层单测全量跑；MinIO adapter 仅可导入+ guarded smoke
- **决策**：WP0.2/0.3 契约与校验器写 pytest 单测（全量跑，用 Fake）；`MinioObjectStore` 只保证可导入、签名兼容 Port，其端到端测试用 `@pytest.mark.postgres` 式 guard（`RUN_MINIO_SMOKE=1` 才执行），无 MinIO 时跳过。
- **依据**：用户「假的测试」；现有 pyproject 已有 `postgres` guarded marker 先例。

## D-007 ｜ Confidence 增加 `source: str` 维度（SRS §5.3 未显式列出）
- **决策**：`Confidence` 在 SRS §5.3 的 text/layout/type/reading_order 四维之外，增加自由字符串字段 `source`（默认 `"unknown"`），记录「这些分数由谁产出」（如 `docling` / `ocr` / `native`）。不引入新枚举，保持扩展性。
- **依据**：SRS §3.5/§5.3 要求 confidence 可追溯到 parser/model；SRS 未规定具体维度清单，仅说「多维、非单浮点」。`source` 是 provenance 标签，不是分数维度本身，因此不进 `VALID_CONFIDENCE_DIMENSIONS`。
- **SRS 覆盖情况**：SRS §5.3 说「多维」，本决策补充「来源标签」。

## D-008 ｜ EvidenceSpan 必须携带至少一个 locator（空 span 校验拒绝）
- **决策**：`EvidenceSpan` 的 `text_range / source_locator / visual_region / native_ref / raw_text` 五者均为可选（SRS §7.4「未知可缺」），但**全部缺失**的 span 被校验器以 `empty_evidence_span` 拒绝（SRS §7.4「不得伪造」）。
- **依据**：SRS §7.4「未知时可以缺失，但不得伪造」——一个没有任何定位的 span 等价于「伪造了证据存在」，因此按 SRS 意图拒绝。`ocr_confidence` 单独存在不算 locator（它描述质量，不描述位置）。
- **SRS 覆盖情况**：SRS §7.4 的「不得伪造」原则的具体化。

## D-009 ｜ stable_element_id 算法：scope + order_index（默认）或 + 内容 salt
- **决策**：`stable_element_id(scope, order_index, salt="")` 输出确定性 id：
  - `salt == ""`（默认）：`f"{scope}-e-{order_index:05d}"`，纯位置型，适合按稳定阅读顺序输出的 normalizer。
  - `salt != ""`：`sha1(scope|order_index|salt)[:16]` 前缀型，适合需按内容消歧同序号兄弟的场景。
  - 拒绝空 scope 和负 order_index。
- **依据**：SRS §2.1「一个 Snapshot 的 Parse IR 内 element id 不因数据库分页或切片变化而变化」。算法只依赖 (scope, order_index, salt)，不依赖页码/批次偏移，满足不变量。SHA-1 用于短 id（非安全用途），碰撞概率对本场景可忽略。
- **SRS 覆盖情况**：SRS §2.1 规定「稳定」但未规定算法；本决策提供默认实现。

## D-010 ｜ structured_assets 容器：dict[asset_id -> asset] + 序列化 kind 判别
- **决策**：`ParsedDocument.structured_assets` 用 `dict[str, Any]`（值是 `TableAsset | FigureAsset | FormulaAsset`），JSON round-trip 时每个值注入 `"kind"` 判别字段（`table`/`figure`/`formula`），`from_dict` 据此重建对应 dataclass。缺 `kind` 字段则报错。
- **依据**：SRS §3.9 把 Structured Asset 分多类（Table/Figure/Formula/Form/KV），v0.1 实现前三类。dict 容器允许 O(1) 按 id 查找（供 Knowledge Access Layer 的 `get_table(snapshot, asset_id)`），优于 tuple。`kind` 判别是序列化层细节，不进 dataclass 字段。
- **SRS 覆盖情况**：SRS §3.9 列出 asset 类型但未规定容器形状。

## D-011 ｜ Confidence 分数范围校验 [0.0, 1.0]
- **决策**：校验器对 `Confidence` 的四个数值维度（text/layout/type/reading_order），当值非 None 时强制 `0.0 <= v <= 1.0`，越界报 `confidence_out_of_range`。
- **依据**：SRS 未显式规定区间，但「confidence」语义、§4.9 质量门禁的 PASS/WARN/FAIL 阈值均隐含 [0,1] 归一化分数。提前在契约层校验可避免下游质量门禁收到非法值。
- **SRS 覆盖情况**：SRS §4.9 隐含；本决策显式化。

## D-012 ｜ 引用完整性校验为 error 级（非 warning）
- **决策**：`dangling_relation / dangling_parent / dangling_caption_ref / dangling_cell_span / dangling_span_page / dangling_footnote_ref` 全部为 `error` 级（使 `ValidationResult.valid=False`）。
- **依据**：SRS §4.7「后端返回成功但 JSON 缺字段、关系悬空或坐标非法时，记为 normalization failure，不可进入质量门禁」。悬空引用是硬错误，不是建议。
- **SRS 覆盖情况**：SRS §4.7 直接要求。

## D-013 ｜ ObjectStorePort 签名取舍（WP0.3 契约层）
- **决策**：`ObjectStorePort`（Protocol，全 async，`contracts/storage/port.py`）按下列取舍定稿：
  1. **`put_stream(stream, options) -> PutResult` 不接收 bucket/object_key**。SRS §3.1A 规定 object key 是「系统生成的不可变、无业务语义 key」，由 adapter 生成并分配 `storage_object_id`；任务书 §"其余一律用 storage_object_id" 与之冲突，按 SRS 取舍。bucket/object_key 仅出现在 `presign_put` / `initiate_multipart`（对象尚未存在）。
  2. **`get_stream(storage_object_id) -> AsyncIterator[bytes]` 为 async generator**（`async def` + `yield`），调用方 `async for chunk in port.get_stream(...)` 直接迭代，**不 `await`**。Port 注解 `async def -> AsyncIterator[bytes]` 对 generator 与 "await 返回迭代器" 两种实现都合法；实现选 generator 以支持流式无界对象。
  3. **无 `ReadStream` 包装类**。types.py 只放数据类；流类型在 Port 注解里用 `collections.abc.AsyncIterator[bytes]`（任务书显式要求）。
  4. **`VALID_ARTIFACT_CLASSES` 复用 `parse_ir/enums.py`**（单一事实源，避免双定义漂移），在 `storage/enums.py` re-export。
  5. **错误码目录只含存储层子集**（`storage_unavailable/503`、`storage_object_missing|corrupt/409`、`checksum_mismatch/422`、`storage_forbidden/403`、`quota_exceeded/413` 等）；SRS §C01 的 `upload_session_expired/upload_incomplete/file_too_large/unsafe_file/document_revision_conflict` 属 WP1B 编排层，不在本 Port 重复。errors.py 顶部注释保留完整 §C01 -> HTTP 表供业务层映射。
  6. **`SourceArtifactReader` 为独立 Protocol**（同文件），`materialize_temp(storage_object_id, run_id) -> Path` 实现 run-scoped 临时文件 + hash 校验（SRS §C02/§C03/WP1D）。生产实现内部组合一个 `ObjectStorePort`。
  7. **`complete_multipart` 的 sha256 在契约/Fake 层按重组字节计算**。真实 MinIO adapter（M1）的复合对象 sha256 计算方式（客户端提供 part hash 聚合 vs 流式回读复合对象）留 M1 决定，Port 契约不锁定算法，只锁定「`expected_sha256` 不符必抛 `ChecksumMismatch` 且丢弃对象」。
- **依据**：SRS §C00「公共端口只暴露项目类型……MinIO SDK response、S3Error、bucket 命名和凭据不得越过 adapter 边界」；§3.1A「object key 使用系统生成的不可变、无业务语义 key」；§9.5「MinIO 不可用返回可重试 `storage_unavailable`；不能伪装成 document not found」。任务书对 `put_stream` 的 `ref_or_bucket_key` 参数自相矛盾，按 SRS §3.1A 化解。
- **SRS 覆盖情况**：SRS §C00/§3.1A 显式要求；签名细节（generator vs awaitable、无 ReadStream 类、artifact_class 复用）SRS 未规定，本决策补充。

## D-014 ｜ 状态机契约：复用 storage.enums 的状态集合，新实体集合定义在 state_machines.py
- **决策**：`contracts/state_machines.py` 是所有实体状态机的单一事实源。Upload Session / Storage Object 的合法状态集合（`VALID_UPLOAD_SESSION_STATES` / `VALID_STORAGE_OBJECT_STATES`）从 `contracts/storage/enums.py` re-export（避免双定义漂移，与 D-013 第 4 条同一原则）；新增的 Document Content / Parse Run / Snapshot Commit 状态集合在本模块首次定义。`LEGAL_TRANSITIONS: dict[str, frozenset[tuple[str,str]]]` 与 `TERMINAL_STATES: dict[str, frozenset[str]]` 严格按 SRS §9.0A–§9.5 落地，全部用 `frozenset` 不可变容器（D-001 风格）。
- **依据**：SRS §9 给出的是状态图文本，本决策把它固化为可被 WP0.4 DDL 的 `CHECK` 约束、WP1B 编排层、WP3 校验层共同引用的 Python 常量。复用 enums.py 避免「Upload Session 合法状态在两个文件里各写一份」的漂移风险。
- **SRS 覆盖情况**：SRS §9 直接要求；状态集合的归属（re-export vs 新建）SRS 未规定，本决策按 D-013 既定原则补充。

## D-015 ｜ Parse Run 「任意非终态 → CANCELLED」用显式枚举而非通配
- **决策**：SRS §9.2 写「（任意）→ CANCELLED」，实现层把 `CANCELLED` 的入边**显式枚举**为来自 9 个非终态的边（QUEUED/INSPECTING/PLANNED/PARSING/NORMALIZING/RECONCILING/EVALUATING/REPAIRING/FALLING_BACK），**不**采用「除终态外全部允许」的通配语义。同理 Upload Session 的 ABORTED/EXPIRED 入边也逐条列出（来自 INITIATED/UPLOADING/OBJECT_STAGED/VERIFYING）。
- **依据**：契约层要的是「可审计的封闭集合」——通配会让新增状态时 silently 拥有 CANCELLED 入边，违背「状态只前进」与「新增状态需人工评审」的意图（SRS §9.2「状态只前进；重试创建新的 attempt event」）。显式枚举后，新增 Parse Run 状态必须同时更新本表，触发 code review。
- **SRS 覆盖情况**：SRS §9.2 说「任意」，本决策收紧为「显式任意」；语义等价但更安全。

## D-016 ｜ Storage Object 的终态只认 DELETED，事故状态不是终态
- **决策**：`TERMINAL_STATES["storage_object"] = {"DELETED"}`，而 `QUARANTINED / MISSING / CORRUPT` **不**算终态。即 `is_terminal("storage_object", "MISSING") == False`。
- **依据**：SRS §9.0B 明确「`MISSING/CORRUPT` 是完整性事故状态，不等价于业务删除」——事故状态可能被运维恢复（从副本重建 MISSING、修复 CORRUPT、解除 QUARANTINED），因此它们在状态机里虽无显式出边（v0.1 不建模恢复路径），但语义上「不是终态」。`DELETED` 才是唯一不可逆终态。这一区分让 `is_terminal()` 的调用方（如 Snapshot Commit 的前置校验）不会把事故对象误判为「可安全忽略」。
- **SRS 覆盖情况**：SRS §9.0B 隐含；本决策把「事故 ≠ 终态」显式化。

## D-017 ｜ nullable `object_version_id` 唯一性：表达式索引 `COALESCE(object_version_id,'')`（WP0.4）
- **决策**：`asset_storage_objects` 的位置唯一性用**表达式唯一索引** `UNIQUE INDEX uq_asset_storage_objects_location ON asset_storage_objects(provider, bucket, object_key, COALESCE(object_version_id, ''))`，**不**用列级 `UNIQUE(provider,bucket,object_key,object_version_id)`。PG 与 SQLite 双版本一致采用此写法（SQLite 表达式索引原生支持 `COALESCE`）。
- **依据**：SRS §8.5 末段显式要求「物理唯一约束还必须规范化 nullable `object_version_id`，避免 PostgreSQL 的 NULL 语义放过重复 current objects」。PG 列级 `UNIQUE` 中多个 NULL 互不冲突，会让「同 provider/bucket/key + 两个 NULL version」（即两份 current object）并存；`COALESCE(NULL,'')` 把 NULL 归一为 `''`，使两份 current object 判重。`''` 不是合法 S3 version id（AWS/MinIO version id 为固定长度 hex），不会与真实 version 冲突。
- **SRS 覆盖情况**：SRS §8.5 末段直接要求；具体归一值 `''` 是本决策补充。

## D-018 ｜ 新列/新约束的「立即 vs M1 推迟」边界：UNIQUE/CHECK 立即，FK 推迟（WP0.4）
- **决策**：008 迁移中——
  - **立即生效**（M0 加）：所有新表/新列、所有 `UNIQUE`（含 partial / 表达式）、所有 `CHECK`（枚举值、`>= 0` 范围、乐观锁 `version >= 1`）。
  - **M1 推迟**（注释标注 `M1 补 FK`）：所有指向 `asset_storage_objects.id` / `knowledge_bases.id` / `kb_folders.id` / `kb_users.id` 的外键。
  - **partial unique 的幂等守卫**：`uq_asset_snapshot_fingerprint`（PG 版）用 DO 块「先查重复行再建索引」守卫，沿用 004 `asset_snapshot_workflow_binding.sql` 既有风格——`CREATE UNIQUE INDEX IF NOT EXISTS` 只查索引是否存在、不查数据是否干净，存量重复行会让建索引撞 `unique_violation` 而失败；有重复时跳过（系统仍可运行，应用层 upsert 仍去重），待清理后再强约束。
- **依据**：D-004「M0 只加表/列、不改读写」；FK 在存量数据未回填时会拒绝（如 `asset_documents.storage_object_id` 现在全为 NULL，但指向的 storage object 尚未创建，硬 FK 会让 M1 回填事务处处受阻）。UNIQUE/CHECK 不依赖存量数据存在性，可安全立即加。partial unique 守卫风格与 004 一致，避免重复造轮子。
- **SRS 覆盖情况**：SRS §8.5「owner 表须各自持有真实 FK」要求 FK 存在，但未规定里程碑；本决策把 FK 放 M1（与 SRS §8.8 Phase 2 回填对齐），M0 只准备结构。

## D-019 ｜ SQLite 与 PG 的 `ADD COLUMN` 幂等性差异：DDL 写 PG 风格，SQLite 经测试加载器降级（WP0.4）
- **决策**：008 两份 DDL **都**写 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`（PG 扩展语法，表达幂等意图、作为单一事实源）；SQLite 版本因 SQLite 不支持 `ADD COLUMN IF NOT EXISTS`（实测 `near "EXISTS": syntax error`），由**契约测试加载器**（`test_storage_ddl._load_schema`）在执行前用 `pg_schema._split_ddl` 拆句、按 `PRAGMA table_info` 检查列存在性、剥离 `IF NOT EXISTS` 后回放。这是**测试期兼容手段**，不污染 DDL 文件本身——生产 SQLite 库（开发期本地库）按 001 baseline 一次性建库、不在运行库上重跑增量；真实增量迁移只在 PG 上发生（`pg_schema.py` 执行链）。
- **依据**：项目既有约定是「PG 迁移幂等（DO 块守卫）+ SQLite baseline 一次性建库」；`005_kb_file_meta.sql` 等存量迁移也用 `ADD COLUMN IF NOT EXISTS` 但实际只在 PG 执行。SQLite 文件存在的目的是契约测试 + 开发期 `:memory:` / 本地库基线，不在已迁移的 SQLite 库上重跑。把 SQLite 兼容逻辑放测试加载器而非 DDL，保持两份 DDL 文本对齐、可读、可 diff（D-003 双版本一致）。
- **SRS 覆盖情况**：SRS 未规定 SQLite 兼容策略；本决策为测试可执行性的实现细节。

## D-020 ｜ ObjectStorePort 改为 location 寻址（取代 D-013 第 1 条）— M1 落地时发现
- **决策**：M1.1 实现 adapter 时发现，WP0.3 让 `ObjectStorePort` 以 `storage_object_id` 寻址（put/get/stat/delete）在 MinIO 上不可行——S3/MinIO 原生按 `(bucket, object_key, version_id)` 寻址，无法凭项目自有的 `storage_object_id` 反查对象，除非 adapter 自带注册表；而「注册表放在 adapter 内」对 MinIO 不可扩展（单 JSON 索引对象有并发读改写竞争）。故把 Port 的字节操作改为按 `ObjectLocation(bucket, object_key, version_id?)` 寻址：
  - `put_stream(location, stream, options) -> PutResult`（caller——即 Repository——按 SRS §3.1A key 策略选 location）
  - `get_stream / stat / delete / head_exists / copy / presign_get / presign_put / initiate_multipart(location,...)` 全部按 location
  - `PutResult` 去掉 `storage_object_id`，保留 `sha256/size/etag/version_id`
  - **业务身份 `storage_object_id`（PG `asset_storage_objects.id`）由 Repository（WP1B/M1.2）持有**，Port 不再认识它
  - `SourceArtifactReader`（按 `storage_object_id` 寻址）从 ObjectStorePort 移出，改在 M1.2/WP1D 由「Repository 解析 id→location + ObjectStore 取字节」组合实现
- **依据**：S3/MinIO 原生寻址模型；SRS §3.1A「object key 系统生成、无业务语义」（key 由 caller 选，符合）；SRS §8.5「asset_storage_objects 是对象引用清单」（即 PG 才是 id→location 注册表，不是 adapter）。D-013 第 1 条「adapter 生成 storage_object_id」被本条取代。
- **SRS 覆盖情况**：SRS §C00 未规定 Port 寻址键；本决策按 S3 模型补全，使 MinIO adapter 无需自带注册表。
- **影响**：需修订 WP0.3 的 `contracts/storage/port.py`、`types.py`、`test_object_store_port.py`（M1.1 一并完成），WP0.3 已提交的 16 个测试改写为 location 寻址。

## D-021 ｜ M1.1 Object Store adapter 实现取舍（Fake + MinIO + factory）— 补 D-020 之外
- **决策**：M1.1 落地两 adapter（`mining/infra/object_store/`），在 D-020（location 寻址）之外补以下取舍：
  1. **FakeObjectStore 为文件系统后端、跨实例持久**（D-002 落地）。对象存 `{root}/{bucket}/{object_key}`、sidecar meta 存 `{...}.meta.json`（sha256/size/etag/mime/artifact_class/created_at）。put 写 `.tmp` 后 `os.replace` 原子化；expected_sha256 不符抛 `ChecksumMismatch` 且删 tmp。阻塞 IO 一律包 `asyncio.to_thread`（不引 aiofiles/aiohttp 新依赖）。跨实例持久让「实例 A 写、新建实例 B 同 root 能读到」可直接用文件系统单测验证，无需起服务。
  2. **minio SDK 顶层不 import，仅方法内 lazy import**（D-006 落地）。环境未装 minio，`import minio` 会 ModuleNotFoundError；故 `MinioObjectStore.__init__` 与每个方法内部 `from minio import ...`，模块顶层零 minio 引用。构造时若 SDK 缺失抛带安装提示的 `ImportError`。
  3. **MinIO put/get 用临时文件 materialize**（避免大对象占内存）。put：drain 到内存算 sha256→写 tmp 文件→`put_object(bucket,key,FileReader,length)`→返回 version_id/etag；get：`get_object` 响应体在 thread 里流式写到 tmp 文件，再分块 64KB yield 回。S3 异常经 `_map_s3_error` 归一（NoSuchKey/NoSuchBucket→`StorageObjectMissing`、AccessDenied/Forbidden→`StorageForbidden`、其余一律 `StorageUnavailable`，**绝不**伪装成 missing，SRS §9.5）。
  4. **MinIO multipart 暂留 NotImplementedError seam**（put/get/stat/delete/head_exists/copy/presign_* 已可单测 + guarded smoke）。MultipartUploader 的 `upload_part/complete/abort` 在 M1 guarded smoke 阶段接通真实 SDK（`from minio.api import MultipartUploader`），契约已锁定「expected_sha256 不符必抛 `ChecksumMismatch` 且丢弃对象」。
  5. **object_key 布局 `v1/{ab}/{cd}/{sha256}` 由 caller（Repository）选，不在 adapter 内**（SRS §3.1A「object key 系统生成、无业务语义」、§8.1）。`keys.build_object_key(artifact_class, sha256, prefix="v1")` 为纯函数、可单测，Repository（M1.2）调用它构造 `ObjectLocation`。
  6. **凭据不进 repr/日志**：`ObjectStoreConfig.__repr__` 覆盖，access_key/secret_key 显示 `***set***`/`<empty>`；`storage.yaml` 的 minio 段为注释示范，标注「凭据由 Secret 管理，勿提交真实值」。
  7. **factory 不暴露 `ObjectRef`**：`make_object_store(config) -> ObjectStorePort` 只认 `provider`（fake/minio）；`ObjectRef`（带业务 id）是业务层 DTO，Port/adapter 不用。
- **依据**：D-020（location 寻址）、D-002（双 adapter）、D-006（guarded MinIO）；SRS §3.1A/§8.1（key 策略）、§9.5（错误归一不伪装 404）、§C00（SDK 类型不越界）；不引新依赖原则（asyncio.to_thread 替代 aiofiles）。
- **SRS 覆盖情况**：SRS §C00 要求 adapter 隔离 SDK、§8.1 要求 key 布局、§9.5 要求错误语义；具体「tmp 文件 materialize」「multipart 留 NotImplementedError seam」「Fake 跨实例持久」SRS 未规定，本决策补充。
- **影响**：`mining/infra/object_store/{__init__,config,keys,fake,minio,factory}.py` + `tests/infra/{test_fake,test_minio,test_factory}.py` + `system/storage.yaml` + `pyproject.toml`（加 `minio>=7.2`）。契约层（WP0.3）location 化修订在 D-020 影响 内完成。MinIO multipart 真连在 M1 guarded smoke 补（需真实 MinIO 实例）。

---

> 后续决策按 D-022… 追加。每个里程碑结束在对应交付报告中引用本日志条目。
