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

## D-022 ｜ M1.2 Storage Repository + Upload Session 编排：六边形分层 + Protocol 化仓储
- **决策**：M1.2 按六边形（hexagonal）分层落地上传会话编排，业务逻辑只依赖 Repository **Protocol** + 已就绪的 `ObjectStorePort`，不直接依赖 psycopg：
  1. **Repository Protocol 落 `contracts/file_management.py`**（新文件，纯 stdlib）。定义 5 个 `@runtime_checkable Protocol`：`StorageObjectRepository` / `UploadSessionRepository` / `DocumentCurrentContentRepository` / `FileAuditRepository` / `QuotaRepository`；交换类型为 frozen dataclass（`StorageObjectRecord` / `UploadSessionRecord` / `DocumentCurrentContent` / `FileAuditEvent` / `QuotaRecord` / `CommitResult`）。错误：`DocumentRevisionConflict`（code=`document_revision_conflict`，409）、`UploadSessionExpired`（410）、`UploadIncomplete`（409），全部继承既有 `StorageError` 基类（`quota_exceeded` 复用 `storage.errors`）。
  2. **编排服务落 `file_management/service.py::UploadSessionService`**，构造注入 `(object_store, sessions, storage_objects, documents, audits, quotas, config)`。四步法：`initiate`（幂等 by `(kb_id,actor,idempotency_key)`，配额 `reserve`）→ `stage_from_bytes`/`stage_chunked`（state UPLOADING→OBJECT_STAGED）→ `complete`（校验 size/sha256→注册/复用 StorageObject→建/更新 Document 当前内容→审计→配额 commit，幂等返回原 `CommitResult`）→ `abort`（配额 release、删 staging、state→ABORTED）。每次状态变更前 `state_machines.assert_transition("upload_session", old, new)`。
  3. **In-memory fake 仓储落 `file_management/repositories_memory.py`**，完整实现 5 个 Protocol，dict 存储，支持乐观并发（Quota.version / Document.content_revision 校验）、dedup 探针、`list_expired`。**供测试用，使整套服务测试无需 PG 即可全绿**（解决环境无 PG 的硬约束）。
  4. **PG 仓储落 `file_management/repositories_pg.py`**，学 `kb/db.py` 的 `AsyncConnectionPool` 风格（`async with self._pool.connection() as conn` + `await conn.execute(... RETURNING *)`），对齐 008 DDL 字段。乐观并发用 `WHERE ... = %s AND content_revision = %s RETURNING ...` / `WHERE ... version = %s RETURNING ...` 服务端强制；quota 的 limit 校验直接折进同一 UPDATE（`AND (reserved+used+delta) <= limit`）。**本环境不跑**（无 PG），但可导入、语法正确。
  5. **DDL 增量**：008 SQLite + PG 各加 `ALTER TABLE asset_upload_sessions ADD COLUMN IF NOT EXISTS staging_bucket / committed_storage_object_id / committed_document_id`（M1.2 session 行需携带 commit 后指针以支持 §9.5 恢复与幂等重 complete；幂等、ADD COLUMN IF NOT EXISTS 与 D-018/D-019 增量风格一致）。
- **依据**：SRS §4.1A（上传事务）、§4.3/§4.3A（文档当前内容 + 操作语义表）、§C01（错误码）、§9.0A/§9.5（Upload Session 状态机 + 恢复）；ADR-0003 D-001（frozen dataclass + Protocol）、D-006（guarded PG）、D-017（COALESCE version_id 归一）、D-020（Port location 寻址：业务 id 下沉到 Repository）、D-002（sha256 dedup O3：同域内 find_by_location 命中即复用，不重复 copy）。
- **SRS 覆盖情况**：SRS §4.1A 上传事务的五步（initiate/stage/verify/register/promote→final）由 service 完整覆盖；§4.3A 文档当前内容的乐观并发（`content_revision` CAS）由 `set_current_content` 服务端 + fake 双实现；§C01 `document_revision_conflict`/`upload_incomplete`/`upload_session_expired`/`quota_exceeded` 错误码齐备。「业务编排逻辑不依赖 PG 可测」「Repository Protocol 化」「dedup 复用」SRS 未显式规定，本决策按六边形架构补全。
- **影响**：`mining/contracts/file_management.py`（新）、`mining/file_management/{__init__,service,repositories_memory,repositories_pg}.py`（新包）、`tests/file_management/{__init__,test_upload_session_service,test_repositories_memory,test_repositories_pg}.py`（新）、`databases/asset_core/schemas/008_object_storage_foundation{,_postgresql}.sql`（3 列增量）。测试：service + memory 全绿（无 PG），PG smoke gated skip。M1.3（File Management Port 化）在 service 之上接 KB 写权限校验与 HTTP 层。

---

## D-023 ｜ M1.3 File Management 服务 + HTTP 路由：迁移期共存、Repository 扩展、错误→HTTP 映射
- **决策**：M1.3 在 M1.2（Repository Protocol + UploadSessionService）之上落 `FileManagementService` + 新 FastAPI router，覆盖 SRS §4.3A 目录/生命周期操作语义表；与旧链路共存（不破坏存量）：
  1. **迁移期共存（硬约束）**：**不修改** `kb/services/document_service.py` 与 `kb/routes/documents.py`（它们继续写 `storage_path`，SRS §2.3）。新 `FileManagementService`（`file_management/file_service.py`）写 `storage_object_id`，经独立新 router（`file_management/router.py`）暴露。两链路并行，上层后续切换；`grep` 确认 DocumentService / 旧路由零改动。
  2. **Repository Protocol 扩展**：`DocumentCurrentContentRepository` 在 M1.2 的 `get/create_document/set_current_content/mark_outdated` 之外**新增 6 方法**——`get_row` / `list_in_kb`（按 kb/folder 过滤、`include_deleted` 开关）/ `rename` / `move` / `set_deleted` / `clear_deleted`——支撑 §4.3A 的 list/rename/move/soft_delete/restore。新增 frozen `DocumentRow`（含 kb_id/folder_id/document_name/deleted_at，原 `DocumentCurrentContent` 故意不含目录展示字段）。内存 fake 与 PG repo 同步实现（PG 用 `SELECT … ORDER BY` / `UPDATE … WHERE id`；不碰对象字节）。
  3. **FileManagementService**（全 async，构造注入 `(object_store, documents, storage_objects, audits, quotas, sessions, config)`）按 §4.3A 落 9 方法：`list_documents`/`get_document`（size/mime 从 storage_objects 取）/`download_url`（→`object_store.presign_get`，对象 MISSING/CORRUPT 抛 `StorageObjectMissing`，**不伪装 404**，§9.5）/`replace_content`（copy-on-write：put_stream 新对象→dedup（`find_by_location` 命中即复用，D-002/O3）→`set_current_content` 乐观更新→审计 `replace_content`→`mark_outdated`；旧对象不删，被 revision 历史引用）/`rename`/`move`（**只改目录行，不动 storage_object**）/`soft_delete`（`set_deleted`，**不删对象**，§8.6）/`restore`/`purge`（**只登记 purge_request 审计**，物理删除留 M1 GC：需检 active reference/Build 引用/retention，§8.6）。写操作均 append `FileAuditEvent`。
  4. **HTTP router**（`/api/kb/{kb_id}/documents[/{id}/...]` + `/upload-sessions`）学现有 kb/routes 风格：`get_file_management_service` / `get_upload_session_service` 依赖工厂（生产从 `app.state` 取，测试用 `dependency_overrides` 注 memory+Fake）；`_map_error(e)` helper 集中错误→HTTP（§C01）：`DocumentRevisionConflict→409`、`UploadSessionExpired→410`、`UploadIncomplete→409`、`QuotaExceeded→413`、`ChecksumMismatch→422`、`StorageObjectMissing→409`、`StorageUnavailable→503`、`NotFound→404`、`Forbidden→403`、其余 `StorageError→502`。`PUT /content` 用 `request.stream()` 原始字节流直喂 `put_stream`（大文件不落内存）。
  5. **无 PG 全绿**：service + router 测试仅依赖 memory fakes + `FakeObjectStore`（`tempfile.mkdtemp`），`dependency_overrides` 注入，**不连 PG**。PG router 集成可后补 PG-gated smoke（skip）。
- **依据**：SRS §4.3A（操作语义表：rename/move/soft_delete/restore/purge 不动对象）、§4.3（文档当前内容）、§8.6（GC：被引用对象不物理删）、§9.0B/§9.5（MISSING 不伪装 404）、§C01（错误码→HTTP 表）、§2.3（迁移期共存）；ADR-0003 D-002（sha256 dedup）、D-020（location 寻址）、D-022（Repository Protocol + 编排分层）。`replace_content` 临时对象先 put 再 copy-到-content-addressed-key 再删 tmp（顺序错误会触发 copy 时 StorageObjectMissing，已修正）。
- **影响**：`mining/contracts/file_management.py`（+`DocumentRow`、Protocol +6 方法）、`mining/file_management/{file_service,router}.py`（新）、`mining/file_management/{repositories_memory,repositories_pg}.py`（+6 方法实现）、`tests/file_management/{test_file_service,test_file_router}.py`（新，25 用例）。未改 `DocumentService` / `kb/routes/documents.py`。测试：`file_management + contracts + infra` 共 302 passed / 4 skipped（PG/MinIO gated）。读权限校验由上层 caller 依赖链负责（本层仅做错误映射），M1.4 安全 intake 在此之上接线。

---

## D-024 ｜ M1.4 Frozen Input + 安全 Intake + SourceArtifactReader：新包共存、纯逻辑安全门、D-020 承诺落地
- **决策**：M1.4 在新包 ``mining/frozen_input/``（独立于 ``file_management`` 与 ``jobs``）落地 SRS §3.2（Frozen Source Binding）/ §C02 / §C03 安全子集 / §C00（SourceArtifactReader），与旧冻结路径共存：
  1. **新包共存（硬约束）**：**不修改** ``mining/jobs/run.py``（``grep`` 确认零改动）。旧路径冻结 ``raw_content_hash`` / domain / channel / ontology；新路径冻结 ``storage_object_id + raw_hash + content_revision`` 三元组，读 ``asset_documents`` 同一行的不同字段，两者语义正交、迁移期并行。新包 ``mining/frozen_input/{__init__,contracts,safe_intake,service,source_reader}.py`` 全部 < 800 行、函数 < 50 行、纯 stdlib（无 python-magic / 无 PG / 无 MinIO SDK 在 import 时）。
  2. **contracts（§3.2 + §C03 + §C01）**：``FrozenInput``（document_id / source_storage_object_id / source_raw_hash / source_content_revision / mime / size / original_filename / captured_at + 冻结时快照的 provider/bucket/object_key/object_version_id 给 reader 免二跳）+ ``IntakeVerdict``（ok / detected_mime / detected_format / encrypted / is_archive / errors / warnings）。错误继承 M1.2 的 ``FileManagementError`` 以复用既有错误→HTTP 表：``UnsupportedFile``（422）、``UnsafeFile``（422，带稳定 ``reason`` 子码：``path_traversal_*`` / ``archive_*_exceeded`` / ``archive_corrupt``）、``FrozenInputStale``（409）。``IntakeVerdict`` 与 ``FrozenInput`` **故意分离**：前者是无状态字节探针产物（准入），后者是仓储状态快照（冻结后），耦合会把两个不相关关注点绑死。
  3. **SafeIntake 纯逻辑（§2.4 + §C03 安全子集）**：自实现 ``_SIGNATURES`` 表覆盖项目实际格式（md/txt/html/pdf/doc/docx/xls/xlsx/ppt/pptx/png/jpg/gif/rtf + zip/rar/7z/gz/tar），**签名胜过扩展名**（``detect_mime`` 先匹配 magic-byte，OLE2 由扩展名消歧 doc/xls/ppt，OOXML 由扩展名消歧 docx/xlsx/pptx vs 纯 zip，纯文本无签名时 ``_looks_like_text`` 启发式 + 扩展名提示）。``inspect`` 组合 MIME + 加密标志（PDF ``/Encrypt``）+ archive 标志 + 策略判定。``check_archive_limits`` 三限（member_count 默认 1000 / expanded_size 默认 2GiB / ratio 默认 100x，均可被 caller 覆写）。``sanitize_archive_member_path`` 拒绝空 / 绝对 / ``..`` / 越根（``resolve().relative_to(root)`` 校验）。``enumerate_zip_members`` 用 stdlib ``zipfile.infolist()`` 仅读中央目录、**不落盘解压**，供 ``check_archive_limits`` 消费。不引外部依赖、可单测、确定性强。
  4. **FrozenInputService（§3.2 + §9.5）**：构造注入 ``(DocumentCurrentContentRepository, StorageObjectRepository, ObjectStorePort)``。``freeze(document_id)``：读 document 当前指针 → 校验 storage_object 存在且 state == ``AVAILABLE``（否则 ``StorageObjectMissing``，不伪装 404）→ 返回 ``FrozenInput``（含快照 location）。``check_stale(frozen)``：重读 document 当前 revision，≠ 冻结值抛 ``FrozenInputStale``（含 frozen/current revision）；document 被硬删视为 stale（current=-1 sentinel）。``check_stale`` 是**咨询式**：抛错后 caller 决定保留 Snapshot 但不自动发布（§3.2「不得自动成为最新知识」）。
  5. **ObjectStoreSourceArtifactReader（D-020 承诺落地 + §C00 + §10.2）**：构造注入 ``(ObjectStorePort, tmp_root)``。``open_stream(frozen)``：用 ``frozen`` 快照的 location 调 ``object_store.get_stream``（**直接调用，不 await**——``get_stream`` 是 ``async def`` 生成器，调用即返回异步迭代器，与 FakeObjectStore / 现有 test 用法一致），边 yield 边 ``hashlib.sha256().update``，流结束校验 ``hexdigest`` == ``frozen.source_raw_hash``，不符抛 ``StorageObjectCorrupt``。``materialize_temp(frozen, run_id)``：写到 ``{tmp_root}/{run_id}/{storage_object_id}``，写完校验 hash，不符删残文件再抛 ``StorageObjectCorrupt``；``cleanup_temp(run_id)`` 用 ``shutil.rmtree(ignore_errors=True)`` 幂等清理。阻塞 IO 一律 ``asyncio.to_thread``（与 D-021 一致，不引 aiofiles）。**临时路径不进 DB**（§10.2），reader 不在每次读时二跳 Repository——冻结快照权威、staleness 由 ``check_stale`` 另管。
  6. **reader 不再 resolve storage_object_id**：D-020 让 Port 改 location 寻址后，reader 的输入是 ``FrozenInput``（已携带 freeze 时快照的 location），不需要 Repository。这把热路径（流式读字节）从「repo round-trip + object_store 读」降为「object_store 直读」，staleness 检查移到提交前的 ``check_stale`` 单点。
- **依据**：SRS §3.2（冻结三元组 + 并发编辑不污染）、§2.4（扩展名不可信 / archive 三限 / 禁目录穿越 / 加密按策略）、§C02（Frozen Input Binding）、§C03（File Inspector 安全子集——本任务只做 MIME/签名/archive 限制，page count / text layer / scan ratio / language 留后续完整 Inspector）、§C00（SourceArtifactReader ``open_stream``/``materialize_temp``）、§9.0B（只冻结 AVAILABLE 对象）、§9.5（corrupt 不伪装 missing、stale 不自动发布）、§10.2（临时路径非资产字段、run 结束清理）；ADR-0003 D-001（frozen dataclass + Protocol）、D-006（无 PG 全绿）、D-020（Port location 寻址、SourceArtifactReader 由 Repository+ObjectStore 组合——本条兑现）、D-021（asyncio.to_thread 替代 aiofiles）、D-022（Repository Protocol 分层）、D-023（M1.3 共存原则延续到 M1.4）。
- **SRS 覆盖情况**：SRS §3.2/§C02/§2.4/§C00/§10.2 显式要求；具体「OLE2/OOXML 用扩展名消歧而非解析 Content_Types」「enumerate_zip_members 用 ``infolist()`` 不落盘」「reader 用冻结快照的 location 免二跳」「DocumentRow.original_filename 留空（M1.2 StorageObjectRecord 不带 filename，由上层 upload session / API 层补）」SRS 未规定，本决策按安全门 + 性能 + 既有契约补全。
- **取舍 / 已知缺口**：(a) 加密检测仅做 PDF ``/Encrypt`` 子串 + OOXML 留空（false negative 由 parser backend 二次校验兜底）；(b) OOXML 消歧用扩展名，理论上「真 zip 伪装成 docx」会被识别为 docx——但仍是受支持格式、不构成安全风险；(c) ``original_filename`` 不在 ``StorageObjectRecord``，需上层从 upload session 补；(d) 完整 File Inspector（page count / scan ratio / language / 布局复杂度）留后续里程碑；(e) PG 持久化 ``FrozenInput`` 行（落 ``mining_run_documents``）不在本任务——本任务只提供应用层 freeze/check_stale/reader 三件套，DB 落地由 Mining Run 提交路径在 M1.5+ 接线。
- **影响**：``mining/frozen_input/{__init__,contracts,safe_intake,service,source_reader}.py``（新包）、``tests/frozen_input/{__init__,test_safe_intake,test_frozen_input_service,test_source_reader}.py``（新，55 用例）。未改 ``mining/jobs/run.py``（grep 确认）、未改 M1.2/M1.3 任何文件。回归：``frozen_input + contracts + infra + file_management`` 共 **357 passed / 4 skipped**（PG/MinIO gated），无回归。

---

## D-025 ｜ M1.5 本地文件迁移（WP1C）：纯迁移工具新包、不改读写路径、MigrationInventory/ProgressStore 抽象免 PG 全绿
- **决策**：M1.5 在新包 ``mining/file_migration/``（独立于 ``file_management`` 与 ``jobs``）落地 SRS §8.8 Phase 2（历史回填）+ §A23（验收），与既有读写路径**硬隔离共存**：
  1. **新包共存（硬约束，延续 D-004/D-023）**：**不修改** ``mining/jobs/run.py``、``DocumentService``、``file_management/service.py``、``contracts/``、``infra/``（``git diff`` 确认零改动）。旧读写路径继续按 ``storage_path`` 工作；本任务只产出一个**一次性迁移工具**，把存量 ``storage_path`` 文件回填到对象存储 + 新列。新包 ``mining/file_migration/{__init__,contracts,inventory_fs,progress_memory,service}.py`` 全部 < 800 行、函数 < 50 行、纯 stdlib（无 psycopg / 无 MinIO SDK 在 import 时）。
  2. **contracts（§8.8 报告字段 + 状态机）**：``MigrationItem``（document_id / kb_id / storage_path / current_content_revision / size_hint? / mime_hint?）+ ``MigrationTaskStatus`` 常量（PENDING / UPLOADING / VERIFIED / SWITCHED / FAILED，1:1 对齐 §8.8 五态）+ ``MigrationTaskResult``（document_id / status / storage_object_id? / sha256? / size? / error_reason? / bytes_migrated）+ ``MigrationReport``（total / migrated / switched / failed / missing_files / hash_conflicts / permission_failed / orphan_files / fallback_read_count / duration_seconds / per_document tuple——字段名 1:1 对齐 §8.8「迁移报告至少输出：总数、已迁移、缺失、hash 冲突、权限失败、孤儿文件、回退读取次数」）。``error_reason`` 用稳定子码常量 ``REASON_MISSING_FILE`` / ``REASON_PERMISSION`` / ``REASON_HASH_CONFLICT`` / ``REASON_REVISION_CONFLICT`` / ``REASON_ORPHAN`` / ``REASON_UNKNOWN``。两个 ``Protocol``：``MigrationInventory``（``iter_pending`` / ``count_pending``，数据来源抽象）+ ``MigrationProgressStore``（``get`` / ``upsert`` / ``list_failed`` / ``list_pending``，幂等恢复账本）。
  3. **FileMigrationService（§8.8 Phase 2 + §A23 + §9.5 幂等）**：构造注入 ``(ObjectStorePort, StorageObjectRepository, DocumentCurrentContentRepository, MigrationInventory, MigrationProgressStore, config)``。``migrate_document(item)`` 六步：(1) progress.get → 已 SWITCHED 直接返回（幂等）；(2) ``_open_legacy_file`` 流式打开（``asyncio.to_thread(os.stat)`` 取 size + ``asyncio.to_thread(fh.read, CHUNK)`` 分块流），FileNotFoundError → FAILED(missing_file)、PermissionError → FAILED(permission_failed)、其他 OSError → FAILED(missing_file)；(3) 流式 ``hashlib.sha256`` 增量算 hash；(4) dedup 探针 ``find_by_location(source bucket, build_object_key("source", sha256))`` 命中则复用 StorageObject（D-002），否则 ``put_stream``（``expected_sha256=sha256`` fail-closed）上传到 final 位置；(5) ``stat`` 校验 size + sha256 与对象一致（§A23 verify-before-switch，v1 做全量 sha256 比对）→ register StorageObject(AVAILABLE) + mark VERIFIED；(6) ``documents.set_current_content(document_id, storage_object_id, raw_hash, expected_revision=item.current_content_revision)``——乐观并发：``DocumentRevisionConflict`` → FAILED(revision_conflict) 且**不切换指针**（新内容胜，§8.8「迁移期间若文档内容或逻辑文档发生修改，当前任务必须基于乐观 revision 失败并重新盘点，不能覆盖新内容」），KeyError（文档行消失）→ FAILED(orphan_file)。每步前 upsert progress（PENDING/UPLOADING/VERIFIED/SWITCHED/FAILED），switch 失败时 ``_fail_switch`` 把 partial progress（object_id / sha256 / size）带上以便审计/回收孤儿对象。
  4. **run / resume / dry_run（§8.8 重跑幂等 + 盘点）**：``run(limit?, dry_run=False)`` 顺序迭代 inventory 逐个 migrate（并发留 TODO），汇总 MigrationReport；``resume()`` 重迭代 inventory，per-document 幂等守卫跳过已 SWITCHED，重试 PENDING/UPLOADING/VERIFIED/FAILED（§8.8 重跑幂等）；``dry_run=True`` 只 ``count_pending`` + 抽样 ``os.path.isfile`` 探存在性，**不写对象、不改 DB**，返回预估报告（``missing_files`` 来自抽样）。``fallback_read_count`` 恒为 0——本地 fallback 读是 Phase 3 双读关注点，迁移工具本身不执行 fallback。
  5. **FilesystemMigrationInventory（盘点抽象的 FS 实现）**：``__init__(items=[MigrationItem])``（测试用，items 指向 ``tmp_path`` 真实小文件）或 ``from_manifest(path)`` 读 JSON list（运维用，暂存 curated 子集）。**不直接 SELECT 数据库**——PG 版 ``inventory_pg``（``SELECT asset_documents WHERE storage_path NOT NULL AND storage_object_id NULL``）是独立 PG-gated 模块，本环境不跑。
  6. **MemoryMigrationProgressStore**：纯 dict 实现 ``MigrationProgressStore``，``list_pending`` 返回所有非 SWITCHED（PENDING/UPLOADING/VERIFIED/FAILED）供 resume 重试；PG 版（``progress_pg``，落迁移账本表）留后续接线。
- **依据**：SRS §8.8（Phase 0-6，重点 Phase 2 历史回填 + 迁移报告字段 + 乐观 revision 失败 + 重跑幂等 + verify-before-switch）、§8.7（替换边界：``storage_path`` 降级为 legacy 字段）、§A23（验收：hash→upload→HEAD 校验→绑定当前 Storage Object、重跑幂等、校验失败不切换、切换期 MinIO 优先本地回退）；ADR-0003 D-002（sha256 dedup）、D-004（M0 只加列、迁移在 M1——本条兑现迁移侧）、D-005（按里程碑提交）、D-020（Port location 寻址，``build_object_key("source", sha256)`` + ``put_stream(location, stream, PutOptions(expected_sha256))``）、D-021（``asyncio.to_thread`` 替代 aiofiles，文件读取/``os.stat`` 一律 to_thread）、D-022（Repository Protocol 分层，注入 ``StorageObjectRepository`` / ``DocumentCurrentContentRepository``）、D-023/D-024（新包共存原则延续到 M1.5）。
- **SRS 覆盖情况**：SRS §8.8 Phase 2 + 报告字段 + §A23 显式要求；具体「report 同时给 ``migrated`` 和 ``switched`` 两个别名」「``resume`` 默认重迭代 inventory（内存 progress 不带原始 path/revision，PG resume 需 JOIN asset_documents 恢复）」「switch 失败时 partial progress 带上 object_id/sha256/size 供审计」「``fallback_read_count`` 在迁移工具中恒为 0」SRS 未规定，本决策按可审计性 + 既有契约 + Phase 边界补全。
- **取舍 / 已知缺口**：(a) ``migrate_document`` 顺序执行，批量并发留 TODO（当前单文档流式 + 小文件场景足够，大文件并发需考虑 IO/对象存储限流）；(b) 大文件 hash 用「先 drain 算 hash 再 re-stream put」两次读盘——清晰优先，生产大文件可改为 ``put_stream`` 让 store 算 hash、caller 事后 stat 比对（D-020 已支持）；(c) ``resume`` 默认重迭代 inventory，纯 progress-only resume（不依赖 inventory）需 PG progress 行 JOIN asset_documents 恢复 path/revision，留 ``inventory_pg``/``progress_pg`` 接线；(d) ``inventory_pg`` / ``progress_pg`` PG 实现本任务未写（PG-gated，按 SRS §8.8 Phase 0 盘点 SQL + 薄封装留后续）；(e) Phase 3 双读（MinIO 优先、storage_path 回退）不在本任务——本任务只做 Phase 2 回填工具，双读 + Phase 5 停回退 + Phase 6 清理本地卷需独立审批和保留期（SRS §8.8 / WP1C 完成标准）；(f) hash_conflict 场景（同 location 不同 hash）需对象存储侧制造冲突，FakeObjectStore 无法注入，留集成/MinIO 环境验证。
- **影响**：``mining/file_migration/{__init__,contracts,inventory_fs,progress_memory,service}.py``（新包）、``tests/file_migration/{__init__,test_file_migration}.py``（新，12 用例：happy path×3 / 幂等重跑 / missing file / 乐观并发冲突 / dedup / dry_run / resume×2 / sha256 校验 / manifest×2 / 报告字段）。未改 ``mining/jobs/run.py``、``DocumentService``、``file_management/``、``contracts/``、``infra/``（git diff 确认）。回归：``file_migration + contracts + infra`` 共 **255 passed / 1 skipped**（MinIO smoke gated），无回归。

---

> 后续决策按 D-031… 追加。每个里程碑结束在对应交付报告中引用本日志条目。

## D-028 ｜ M3.0 契约演进：DocumentParser.parse 输入统一 bytes（v1.1）
- **决策**：M3 引入二进制格式（PDF/DOCX/XLSX/PPTX）后，M2 的 `parse(text: str, *, mime)` 不再成立。契约演进为 `parse(data: bytes, *, mime: str)`：
  1. **decode 责任移入文本适配器**：MD/TXT 适配器内部严格 UTF-8 解码，坏字节包 `ParserAdapterError`（不再由 service 预解码——二进制格式本来就不能解码，解码是"文本格式适配器"的格式知识）。ShadowParseService 相应只做"流式收集 bytes → 传 parser"。
  2. **TDD 执行**：先批量改测试到 bytes 目标契约（RED，25 failed）→ 契约+适配器+service 实现（GREEN，46 passed）。
  3. **兼容性**：M2 影子链路语义零变化（e2e 重跑全绿）；只有 parse 签名从 str→bytes，所有调用方在库内同步。
- **依据**：SRS §C06（Adapter SDK 演进）、§4.6（Adapter 将冻结对象转换为库输入）；用户 M3 需求对齐（2026-08-14：纯代码混合路线，二进制原生格式接入）。
- **影响**：`contracts/parser_adapter.py`（签名+docstring）、`parse_adapters/legacy_{txt,markdown}.py`（接 bytes + `_decode_utf8`）、`shadow_parse/service.py`（去掉 decode）、相关测试。

## D-028A ｜ M3 路线拍板记录（用户对齐，2026-08-14）
- **用户决策**：① M3 第一期走**纯代码混合路线**——DOCX/XLSX/PPTX/HTML 原生适配器 + PDF 增强适配器，**全部用工业级成熟库**（python-docx / openpyxl / python-pptx / lxml / pdfplumber，均已在环境中，零新增依赖；自研代码只做"库输出 → Parse IR"映射，不写解析算法）；② **OCR 暂不做但预留云端接口**（backend_kind="cloud" 槽位 + 配置位，用户将来配模型即插即用）；③ **不引入本地 AI 模型**（Docling/模型权重 3-6GB 暂缓，复杂版面 PDF 留待后续按需接入，解析后端可插拔保证主链不改）；④ 验收语料 = 自造 fixture 自动化 TDD + 用户后续提供 2-3 份真实文档人工验收。
- **SRS 对齐**：调研报告 §3.1（PyMuPDF AGPL 排除、pdfplumber 类方案定位）、§3.2（组合表：DOCX/PPTX/XLSX 用原生库专项 adapter 正是建议组合）、SRS §4.5（路由表 Office 的 primary 可为 native adapter）、§C04（backend_kind cloud 槽位）、用户"不重复造轮子"工程约束。

## D-029 ｜ M3 实现：Inspector/Router/原生适配器 ×5 + 工厂；评审修正 2 HIGH + 5 MEDIUM
- **决策**：M3 按 D-028A 路线落地（严格 TDD，每个工作包先 RED 后 GREEN）：
  1. **File Inspector（§C03）**：`file_inspector/inspect.py` `DocumentProfile`（格式/容器数/加密/文本层）——复用 `safe_intake.detect_mime` 签名探测；ZIP→OOXML 以 `[Content_Types].xml` 消歧；PDF 走 pdfplumber（BytesIO 不落盘，`/Encrypt` 先扫）；未知格式不抛。**路由**：`ParserRouter.plan(profile) -> RouteDecision`（reason codes：`no_text_layer_needs_ocr`/`ocr_reserved_cloud`/`unsupported_format`），按 registry 查 local+license=ok 的 backend，不硬编码 parser_id。
  2. **原生适配器 ×5（§C06）**：`parse_adapters/native/{native_docx,native_xlsx,native_pptx,native_html,_base}.py` + `{native_pdf,pdf_normalizer}.py`。全部"库 API 直读 → BackendBlock"映射（零解析算法）；公共骨架 `_base.BaseNativeNormalizer`（类型映射/heading 弹栈/阅读序/stable id/强制 validate）。关键映射决策：XLSX openpyxl 标准双读（公式/展示值分离）；HTML 无 charset 声明强制 UTF-8（lxml 默认 latin-1 会把中文变 mojibake）；PDF heading 字号启发式（1.15×众数）`confidence.type=0.6` 如实降权不冒充高置信。
  3. **工厂（M3.5）**：`parse_adapters/factory.py` `resolve_pipeline(parser_id) -> (parser, normalizer)`——实现类↔descriptor 对应的单一事实源；`build_default_registry()` 经 `iter_native_parsers()` 注册全部已实现 parser（7 个）+ docling/cloud_vlm 占位槽位（license != "ok"，Router 永不选中）。
- **评审修正（合入前全修）**：
  - **HIGH-1 DOCX 矩形合并 row_span 多计**：vMerge continue 按列累计使 2 列宽合并的 row_span 翻倍——改为按行去重（`counted_this_row` 集合），补矩形合并回归测试（2×3 合并 row_span=3）。
  - **HIGH-2 不可信声明几何 DoS**：HTML `rowspan/colspan` 上限 10k（超限截断为 1 + `clamped_spans` 可见标记，§7.4）；XLSX 网格维度上限 10k/面积 2M + 合并区域面积上限 100k（超限跳过展开 + `clamped_geometry` 标记）。**已知库级边界**：`mergeCell A1:XFD1048576` 形态会让 openpyxl **打开期**挂死（发生在适配层截断之前），该形态依赖上游 M1 intake 文件级限制兜底，记录于 M3 报告缺口。
  - **MED×5**：(a) 四个 native 适配器的块迭代循环包 try——中段损坏也归一 `ParserAdapterError`（§C06"第三方异常不得穿越"原本只在打开期成立）；(b) HTML 嵌套表格 `iter("tr")` 递归后代遍历改为仅直接子行（`_nearest_table_within` 判定，thead/tbody 包裹仍归外层）；(c) `_base` annotations 白名单过滤 cells/rows/cols——表格数据不再在 IR 双份存储；(d) `reset_heading_stack_on_container_change` 钩子，PPTX 开启——无标题 slide 的正文不再误挂上一张 slide 的标题。
- **LOW 未修项（记录）**：`/Encrypt` 全文子串扫描可能误报（正文含该字面量的 PDF）；base 与 pdf normalizer 的 `Confidence.source`/`Relation.method` 取值不一致；`native_html` 声明 `application/xhtml+xml` 但 Inspector 不产出该 MIME（不可达声明）；XLSX `"="` 前缀判公式的固有误报；PPTX group shape/DOCX 嵌套表格/内联图片未遍历（保真缺口，M4 范围）。
- **验证**：M3 套件 136 passed（含恶意 rowspan/稀疏网格/矩形合并 3 个评审回归用例）；scoped 回归 M0-M3 **520 passed, 6 skipped**；真实环境 e2e（真 MinIO+PG，7 格式全链路）两轮全绿（修复前后各一轮），发布表零污染。
- **影响**：新包 `file_inspector/`、`parse_adapters/native/`、`parse_adapters/{native_pdf,pdf_normalizer,factory}.py`；修改 `contracts/parser_adapter.py`（v1.1：parse(bytes)、BackendBlock 结构化字段、note）、`parse_adapters/{legacy_markdown,legacy_txt,normalizer,registry}.py`、`shadow_parse/service.py`（去 decode）、`infra/structure/__init__.py`（M2 评审 H3 的 disable_image_resolution）；测试 +100。


## D-026 ｜ 真实环境接通（MinIO 121.89.90.178:19000 + PG kb_db）
- **决策与事实**：
  1. `storage.yaml` 落真实凭据（与 database.yaml 同机制：主控 `/api/v1/system/storage/raw` 暴露，mining `control_plane.fetch_storage_config` 拉取，`ObjectStoreConfig.from_control_plane()` 消费，app lifespan best-effort 预填）。
  2. `bucket_prefix` 必须是 **agentickb-dev-**：app 凭据策略锁定该前缀、bucket 已由运维预建（agentickb-dev-{source,staging,parse,binary}，恰为 SRS §8.1 四类）。
  3. `_ensure_bucket` 改幂等：bucket_exists AccessDenied（最小权限 app 凭据无 ListBucket/CreateBucket）→ 视为运维已建、跳过；MakeBucket AlreadyOwned 忽略。
  4. **sha256 完整性闭环**：put 时把 sha256+artifact_class 写入对象 user-metadata；`_meta_get` 必须先 `dict(raw)` 规整（minio 返回 `HTTPHeaderDict`，其 `__contains__` 与普通 dict 不一致，直接 `in` 检查会漏）；stat 现可取回 sha256（SRS §8.6/§9.5）。
  5. `pg_schema.py` 迁移链接入 008（`_OBJECT_STORAGE_DDL`），mining 启动自动建对象存储表；已在真实 kb_db 执行验证（6 新表 + 3 表扩展列全部就位）。
  6. psycopg async 在 Windows 需 `WindowsSelectorEventLoopPolicy`（ProactorEventLoop 不可用，同项目测试惯例）。
- **真实环境验证结果**：MinIO put/get/stat/delete + ensure_buckets 幂等全通；PG 6 表落地全通；e2e 上传事务（initiate→stage→complete）中 quota/audit/session/storage_objects PG repos 全部工作。
- **已知缺口（下一轮修）**：`PgDocumentCurrentContentRepository.create_document` 未填 `asset_documents.domain`（NOT NULL，SRS §A12）→ 真实 e2e 在最后一步 NotNullViolation。修复需同步 contracts Protocol（加 domain 参数或 PG 内查 knowledge_bases）+ memory repo + PG repo。

---

## D-027 ｜ M2 Legacy Shadow Parse（WP3+WP4 压缩版 + WP2 解析模型子集）：Parser Adapter SDK 契约、MD/TXT 原子化适配、影子写入硬隔离
- **决策**：M2 按"压缩策略"落地——legacy 适配器只包 Markdown/TXT，PDF/DOCX/Excel/HTML 等复杂格式全部留给 M3 Docling（SRS §4.5 路由表中 MD/TXT 的 primary 本就是 native adapter，复杂格式 legacy 保真不足是调研报告 §1.2 的既定结论）：
  1. **契约冻结（§C06/C07/C04 子集）**：新文件 `mining/contracts/parser_adapter.py`——`DocumentParser` Protocol（**同步纯函数** `parse(text, *, mime) -> BackendParseArtifact`：流式读 MinIO 是 Operator 职责，§4.6"Adapter 将冻结对象转换为库输入"）、`ParseIRNormalizer` Protocol、`BackendBlock`（行号导向：line_start/line_end 供 §A01 line-addressable）、`BackendParseArtifact`（保留 raw_output 供 §9.5 replay）、`ParserDescriptor`+`BackendRegistry`（确定性 select_for(mime)，无路由规则——WP6/M3 的事）。纯 stdlib，零第三方依赖。
  2. **适配器（WP4 压缩版）**：`mining/parse_adapters/`——`LegacyMarkdownParser` 复用 `infra/structure.py` 的 token→block 转换（实测 SectionNode 树会丢 heading 块级身份和行号，token 级拍平才保真）；`LegacyPlainTextParser` 按空行分段，**不复现旧 PlainTextParser 的 300-token 切分**（调研报告 §1.5 风险：parser 输出必须是原子结构，切分是 Segment Compiler 的职责）；`LegacyLineNormalizer` 产 stable_element_id（scope=source_raw_hash）、行级 EvidenceSpan（source_locator+text_range+raw_text）、heading 弹栈 parent 链 + parent_of/next_in_reading_order relations、pipe table → TableAsset（首行 is_header、cell 保 raw）；产出必过 `parse_ir.validate`，error 级 issue 即 raise（§4.7"normalization failure 不可进入质量门禁"）。MD/TXT 单一 `section` 容器，page_number 留 None 不伪造（§3.6）。
  3. **影子写入（§C08 + M2 退出条件）**：DDL `009_shadow_parse_runs{,_postgresql}.sql` 新表 `asset_parse_runs`——幂等键 `UNIQUE(document_id, source_raw_hash, parser_fingerprint)`（§2.2），status 仅 SUCCEEDED/FAILED（影子运行无状态机，M4 才扩展完整 Parse Run 状态机）。`mining/shadow_parse/ShadowParseService.run(frozen)`：幂等探针（命中 SUCCEEDED → reused=True）→ `ObjectStoreSourceArtifactReader.open_stream` 流式 sha256 校验读 → 严格 UTF-8 → parse → normalize → IR JSON（sort_keys 内容寻址）→ `build_object_key("parse_ir", sha)` put 到 `{prefix}parse` bucket（artifact_class=parse_ir + expected_sha256）→ find_by_location 去重后 register StorageObject → upsert SUCCEEDED 投影（含 element/container/relation 计数）。失败先落 FAILED 行再 re-raise。**硬隔离：绝不写 asset_document_snapshots / asset_raw_segments / mining_run_documents**（M2 退出条件"不影响现有发布"；Snapshot 正式提交是 M4 WP9 的事）。
  4. **PG 仓储**：`PgParseRunRepository` 用 `ON CONFLICT(幂等键) DO UPDATE RETURNING`；**池必须带 `kwargs={"row_factory": dict_row}`**（file_management/app.py 既有惯例，真实 e2e 首跑踩到 `dict(tuple)` 崩溃后修正）。
- **依据**：SRS §14 M2（退出条件：legacy parser 从 MinIO Frozen Input 读取并 shadow-write 新 Snapshot 解析制品，不影响现有发布）、§C04/C06/C07、§4.5-§4.7、§2.2（幂等）、§A01（line-addressable）、§9.5（replay）；ADR-0003 D-001（frozen dataclass 契约）、D-002（内容寻址去重）、D-020（location 寻址 Port）、D-022（Protocol 分层）、D-023/D-024（新包共存）。
- **SRS 覆盖情况**：M2 退出条件全部显式覆盖；"影子运行的 status 枚举收窄为 SUCCEEDED/FAILED""IR object key 用 IR 字节 sha 内容寻址""mode=shadow 元数据标记"为按边界纪律补全（SRS 未规定影子运行表结构）。
- **取舍 / 已知缺口**：(a) 真实 MinIO multipart seam 仍未接（upload_part/complete/abort NotImplementedError，M1 遗留）；(b) `BackendRegistry.select_for` 是"先注册先得"，无 reason codes/fallback/budget——WP6 路由器在 M3 落地；(c) 影子链路尚未挂进 workflow 算子（`document_parse`/`segment_compile` 拆分是 M6 WP11）；(d) PDF 等复杂格式走 legacy 无保真路径，直接等 M3 Docling；(e) `asset_parse_runs` 无 domain 列——影子运行按 document_id 关联，域隔离由 document 侧保证。
- **评审修正（code-review 后合入，3 HIGH 全修）**：(H1) `normalize` 不再传 `parse_run_id` 进 IR——run 级字段会使制品字节不确定、内容寻址去重（D-002）永远 miss；run 归属只记录在投影行。(H2) PG upsert 加 `WHERE status='FAILED'` guard + RETURNING 空时回读——与 memory 实现"双 SUCCEEDED 等价、返回原行"语义对齐，防并发双跑覆盖已成功行。(H3) `infra/structure._make_md_image_block` 增 opt-in `disable_image_resolution` 开关（默认行为不变），MD 适配器启用——否则不受信 markdown 图片路径触发本地文件读取+sha256（违反 §C06 无 IO 契约且构成存在性 oracle）；service 侧 parse/normalize 经 `asyncio.to_thread` 下放（D-021）。另修 MEDIUM：`_record_failure` 双层兜底（审计落库失败不吞原异常）、bucket 前缀缺失 fail-fast（不猜 dev 命名空间）、`_provider()` 缺属性 raise（溯源字段不猜测）、normalize 拆出 `_build_element_graph`。LOW-2：list 回退整体块时产 warning（§7.4"缺可以，但应可见"）。
- **真实 e2e 二轮发现的完整性缺口（已修）**：cleanup 残留的"注册行在、对象没了"场景暴露两处盲信——(a) `_persist_ir` dedup 命中注册行时未确认对象在 → 现在 `head_exists` 校验，缺失则重放内容寻址字节（同 key 同 sha 幂等安全）并 `mark_verified`；(b) **幂等探针命中 SUCCEEDED 时未校验制品可用** → 现在 `_ir_object_available` 前置校验，制品缺失不复用、走完整重跑经 upsert 幂等回到原行（§2.2 幂等的前提是制品在，§8.6 完整性事故不静默）。
- **影响**：新文件 `contracts/parser_adapter.py`、`parse_adapters/{__init__,legacy_markdown,legacy_txt,normalizer,registry}.py`、`shadow_parse/{__init__,contracts,repositories_memory,repositories_pg,service}.py`、`databases/asset_core/schemas/009_shadow_parse_runs{,_postgresql}.sql`、`tests/parse_adapters/`（3 文件 34 用例）、`tests/shadow_parse/`（3 文件 11 用例）。唯一修改的既有文件：`infra/pg_schema.py`（追加 009 挂载）。旧链路（ingestion/stages/workflow/handlers）零改动。



## D-030 ｜ 真实中文论文验收驱动的 PDF 解析修复（CJK 聚行/三线表回退/标题档位/家具标注）
- **背景**：用户提供 73 页中文学位论文（镍基 MOF 光催化研究）做 M3 验收，暴露 4 个手写英文 fixture 覆盖不到的真实问题。全部按 TDD 修复（先写 `tests/parse_adapters/test_pdf_cjk_lines.py` RED 再实现）：
  1. **CJK 碎片（最严重）**：`extract_words` 按空格分词，中文连排被拆成"镍/基/M/O"单字碎片（2609 元素中大半是碎片）。修法：绕过 words 直接聚合 `page.chars`——`group_chars_into_lines`（top 容差聚行）+ `_join_line_text`（CJK-CJK 无空格 / Latin 按字符间距判词边界，阈值 0.15×字号：Helvetica 词内 gap≈0、空格宽 0.278×字号 / CJK↔Latin 边界一个空格）。
  2. **学术三线表识别为 0**：三线表无横线，默认 lines 策略找不到。修法：`_find_tables_with_fallback` 回退 text 策略，且要求 ≥2 行 ≥2 列才接受（防稀疏正文误报）。论文 20 张三线表全部识别。
  3. **标题无层级**：heading level 恒 1，标题树建不起来。修法：两遍式——第一遍全文档收集 heading 行字号，`heading_levels_for` 排序去重映射档位（26pt 封面/16pt 章/14pt 节 → level 1/2/3），第二遍产块。修复后论文"章→节"层级完整恢复。
  4. **页眉/页码混入正文**：同一页眉跨 49 页重复、纯数字页码 274 处碎片。修法：`classify_furniture` 文档级判定（跨 ≥3 页重复且 >12 字符 → page_header；纯数字/罗马数字 ≤6 字符 → page_number），`_annotate_furniture` 经 `dataclasses.replace` 改块类型（frozen 不可变，注意不能原地赋值）。**只标注不删除**——去重是 M4 Reconciler 职责（§4.8），下游按 element_type 过滤即可。下标拆行同修：LINE_TOP_TOLERANCE 3→6pt（CO₂ 的"2"下标偏移 4-5pt）。
- **附带**：新增验收工具 `tools/parse_preview.py`（纯本地：探测→路由→解析→IR→HTML 报告，家具折叠、合并格 rowspan/colspan 还原、低置信黄标）——用户可在不启动 pipeline 的情况下验收任意文档。
- **验证**：`test_pdf_cjk_lines.py` 9 用例 + 全套 145 passed；论文重解析效果：元素 2609 碎片 → 1614（正文 1383 段 + 35 标题成树 + 20 表格 + 家具分流 176）。
- **影响**：`native_pdf.py`（chars 聚行/两遍式/表格回退/家具标注）、`pdf_normalizer.py`（家具类型映射）、`tools/parse_preview.py`（新增）、`test_pdf_cjk_lines.py`（新增）。


## D-031 ｜ Word 对照基准驱动的 PDF 解析对齐（验收 v5-v7）+ 对齐度量工具
- **用户拍板（2026-08-17）**：① PDF 目标线=**实用线**（标题树完整+段落完整+表格基本识别，复杂版面瑕疵标记低置信）；② 验收基准=**Word 对照**（同文档双版对齐量化）；③ 防过拟合=**后续补语料**（2-3 份不同类型文档）。
- **对齐度量（tools/ 内联于验收脚本）**：同文档 docx 与 pdf 分别解析，标题按 norm 前缀匹配对齐率、正文字符覆盖率、表格数对比。基线：标题 42% → 修复后 **98%（61/62）**，正文 **101%**，真数据表 4/5。
- **修复链（全部通用排版规则，非文档特定）**：
  1. **接缝 bug（v5）**：`_line_block` 内部按字号重判 heading，把上游编号模式识别命中的同字号标题降级回 paragraph——修复为尊重上游 `heading_by_pattern` 标记（12pt 的"1.2.1 …"小节全部找回，42%→94% 的主因）。
  2. **上下框陷阱（v6）**：仅两条横线夹住标题+正文的区域被 mixed 策略当表。三层防御：extract 网格有效列数 ≥2 + **跨列断裂行检测**（竖切列把文字切碎=列边界错，p27 场景）+ 题注行豁免（"表 N-N"跨列居中是正常形态）。
  3. **数据核心收缩（v6）**：候选 bbox 上半部是单片段行（标题/正文）、下半部多片段行（数据）——按片段数收缩顶边；pdfplumber Table.bbox 只读 → 旁路 `bbox_override` 传递（crop 重查会丢行，弃用）。
  4. **片段块聚类第四层（v7）**：主/回退策略全拒时，多片段行（列对齐信号）自聚连续块作为候选框，crop 内 text 策略提取——救回无线三线表（p27 表 2-1 的 16×3 完整还原）。
  5. 连字防御：char.text 多字符（"fi" ligature）崩溃修复（对抗自审发现）。
- **已知缺口**：跨页续表（99pt gap 断开成两表，continuation_of 关联留 M4）；p28 仪器表 text 提取丢部分行；图片/公式无内容（云端 VLM 槽位）。
- **验证**：test_pdf_cjk_lines.py 28 用例 + 全套 538 passed；第二语料（英文编号文档）验证通用；论文双版对照 98%/101%/4张表。

## D-032 ｜ 整改轮（2026-08-17/18）：全格式审计驱动的 M3 地基整改 + M3→M3A 重定义
- **背景**：用户主程指令——停止对单一样本追加启发式补丁，按 Parser Adapter → BackendParseArtifact → Normalizer → Reconciler → Quality Gate 主线整改；先审计后整改；严格 TDD；跨格式 contract tests；30-50 份 golden corpus；重定义 M3 状态。审计报告：`docs/文档解析平台化-全格式审计与整改-2026-08-17.md`（8 条不变量违规 I-1..I-8 + 逐格式缺陷清单）。
- **决策与实现**：
  1. **契约 v1.2**（`contracts/parser_adapter.py`）：`ParseRuleConfig`（frozen，全部 adapter 阈值的单一契约，`config_fingerprint()` 进 `ParseIdentity.rule_config_fingerprint`）；`BackendParseArtifact.to_dict/from_dict`（JSON round-trip，replay 前提）；`effective_pipeline_fingerprint(parser, normalizer, rules, deps, reconciler, ir_schema)`（I-5：任一组成变化必变指纹）。validator 增 `invalid_bbox_order`（I-1 边界校验）。
  2. **骨架不变量**（`parse_adapters/native/_base.py` + `rendered_text.py`）：表格 Element.text 一律由 TableAsset 经 `render_table_text` 渲染（行\n列\t，I-2/I-3）；`_make_cell_spans` 钩子（I-4：cell 独立 source_span_id，无独立证据时宁缺勿伪造——取消旧的"回落首 span"）；`_extra_assets`/`_element_metadata` 钩子。
  3. **逐格式**：DOCX numPr 列表 + cell 证据 + 嵌套表（XML 层遍历——lxml 代理 id() 不稳，按 id 去重会偶发漏检，实测 flaky 根因）+ 六类结构计数诊断；XLSX Excel Table→连续区域→used_range 三级识别 + 隐藏态 + 图表诊断；PPTX bbox 角点化 + 逐段落拆分 + 几何带阅读序 + notes/group/picture(FigureAsset+sha256 binary) + chart/SmartArt 诊断 + 标题回退收紧（无占位符+单段+页顶带三条件）；HTML 嵌套列表独立 + links/caption/语义路径 + rowspan×colspan 面积上限 100k（单值 10k 挡不住 9999×9999≈10⁸ 条目，实测修复前 105s）；PDF 跨沟行通栏判定 + digit-leading 仅无 CJK 短行 + dense_frags 限行内字符 + 收缩框与 cell 一致（垂直重叠过滤+幻影空行剔除+紧凑重排）+ text 回退多片段行前置门槛；MD/TXT 图片链接计数诊断。
  4. **Reconciler（C08 最小，`mining/parse_reconciler/`）**：furniture_typing（自 native_pdf 迁入，IR 级跨页规则——adapter 不再做文档级判定）、caption_binding（caption_of + 资产回填）、table_continuation（相邻页+列数一致+表头 Jaccard≥0.5）、paragraph_continuation（保守 continues_on，不改写文本）；PatchRecord patch log；`reconciler_version` 回写 ParseIdentity。
  5. **Quality Gate（C09 最小，`mining/parse_quality/`）**：`compute_metrics`（char_coverage/structure_accuracy/table_cell_evidence/table_grid_consistency/evidence_locatability/reading_order_monotonicity/warning_counts）+ `QualityGate.evaluate`（PASS/WARN/FAIL，QualityProfile 阈值可覆写）。
  6. **shadow 链路**：`_persist_raw_artifact`（artifact_class=backend_raw，内容寻址）+ `renormalize()`（replay 不重跑 parser）+ reconciler/quality_gate 可选注入（决策进投影 metadata）。
  7. **golden corpus**：`tests/golden_corpus/`（50 份：md7/txt5/docx8/xlsx7/pptx7/html8/pdf8 × 正例19/复杂21/反例4/退化6，确定性构造）+ `tools/golden_benchmark.py`（六指标+决策/警告分布，JSON+MD 报告）+ 阈值守卫测试。基准：结构准确率 0.993、网格一致 1.0、cell 证据 0.944、证据定位 0.898、阅读序 1.0、决策 43P/1W/5F(空)/1拒。
  8. **M3 → M3A 重定义**：报告改名 `M3A-原生解析fastpath.md` 并声明未达成项（第二真实后端/版本化 Router policy/fallback attempts/Parse Operator——归 M3B/M4）。
- **执行事故留档**：整改中途一次 `rm -rf` 误删 `knowledge_mining/mining` 包（mkdir 相对路径错误），经 `git checkout` 恢复 HEAD + 上下文重建全部未提交改动（含前轮 v9 未提交增量），三格式/契约/影子全量测试验证恢复完整（652→659 passed）。教训：涉及 rm 的目录操作必须先 pwd + 绝对路径。
- **依据**：用户整改指令全量（审计先行/不变量/逐格式修复清单/M3 重定义/测试要求）；SRS §4.6-§4.9 主线、§7.4 不伪造、§3.5 指纹、§9.5 replay、§C08/§C09。
- **影响**：新增 `parse_reconciler/`、`parse_quality/`、`rendered_text.py`、`tests/{parse_reconciler,parse_quality,golden_corpus}/`、`tools/golden_benchmark.py`、审计文档；改 `contracts/{parser_adapter,parse_ir/types,parse_ir/schema}`、7 个 adapter/normalizer、`shadow_parse/service.py`；native_pdf 版本 2.0.0（家具迁出+结构修复）、native_docx/xlsx/pptx 2.0.0、native_html@2/legacy-line@2；测试 661 passed（scoped）。

## D-033 ｜ M4 质量门控解析资产：状态机补边 / 幂等锚点上移 / 真表转正隔离 / 唯一性演进
- **背景**：用户确认开启 M4（SRS §14：WP8+WP9，退出条件「低质量文档不会形成 READY Snapshot；可 fallback/replay，过期输入不会自动发布」）。对齐时按用户要求以业务语言重述（五条保证：垃圾不入库/不张冠李戴/出生证明/修复不重复花钱/失败看得见有备胎）。
- **决策与实现**：
  1. **状态机契约补边（§9.2 图的操作性缺口）**：加 `SUPERSEDED` 终态（仅 `EVALUATING→SUPERSEDED`——pre-commit revision check 发生在评估后提交前）+ 四条崩溃/回退边（`PARSING/NORMALIZING/RECONCILING→FAILED`、`PARSING→FALLING_BACK`）。依据：SRS §2.2「fallback 可由失败或质量策略触发」——解析器崩溃发生在 PARSING，不补边则要么伪造走完 EVALUATING、要么无法表达 A06。按 D-015 显式枚举原则逐条添加。
  2. **幂等锚点上移（009 修订）**：M2 的 `UNIQUE(document_id, source_raw_hash, parser_fingerprint)` 在 Run 表上与「同键多次执行」冲突（A09 重放、A07 升级、FAILED 重跑都需要第二行）。Run 是执行历史，幂等的正确锚点是 Snapshot 指纹（SRS §2.2「幂等复用 Snapshot」本意 + §8.3A）——009 索引降级为普通索引，`find_by_document_hash` 探针改为优先返回 SUCCEEDED+snapshot 行。
  3. **真表转正 + 串线隔离**：新链快照写真 `asset_document_snapshots`（SRS 固定其为唯一知识版本根，禁并行版本表）。审计发现 legacy `find_reusable_snapshot` 的 `workflow_graph_hash IS NULL` 分支理论上可命中新行（MD/TXT 同字节场景）——新链 workflow 绑定四元组填哨兵 `new-parse-chain@1`（满足 004 CHECK 的四列同态要求），两个 legacy 分支均永不匹配。
  4. **快照唯一性演进（010）**：004 的 `uq_asset_snapshot_workflow_content`（partial unique **索引**而非表约束——首次 DO 块按 pg_constraint 查找扑空后实测确认）会误拒「同内容 + 管线升级 → 新快照」（A07/A09）。重建为额外排除 `snapshot_fingerprint IS NOT NULL` 行的 partial；新链唯一性由 008 的指纹索引承担（§8.3A 原文「唯一性……演进为 UNIQUE(domain, snapshot_fingerprint)」）。
  5. **REPAIR 的 M4 降级策略**：门禁可产出 REPAIR（空容器定位 + 原因），但 M4 无页级修复执行器——编排层有备胎则按 FALLBACK 处理，无备胎则保守 WARN 提交并追加 `repair_unavailable` issue（空页信号不阻断可用结果，但必须可见）。
  6. **空容器口径**：`empty_container_ids` 只统计无元素的**叶子**容器（排除 workbook 等结构父节点）；文档无任何元素-容器绑定时（legacy MD/TXT 的 `page_span_ids=()` 形态）不判空页——无法区分「容器空」与「格式不表达容器归属」，宁缺勿误报。
  7. **ShadowParseService 重构**：`run()` 的执行体抽为公开 `execute()`（读流→parse→normalize→reconcile→落制品→评估，不写投影）与 `replay()`（normalizer 可覆盖注入）——`DocumentParseService`（parse_operator 包）按 attempt 组装临时 ShadowParseService 复用制品落存逻辑，零代码复制；M2 `run()` 语义与全部既有测试不变。
  8. **snapshot mime 白名单放宽（010）**：legacy CHECK 缺 XLSX/PPTX 两个 OOXML MIME——新链必须如实记录真实 MIME（§7.4 不伪造精神），双方言放宽（SQLite 走标准重建表）。
- **验证**：scoped 全量 701 passed/9 skipped（整改轮 661 → +40）；真实环境 e2e 五场景（转正/重放新快照 parser 零调用/垃圾 FAILED/过期 SUPERSEDED/发布表零污染+哨兵隔离）两轮幂等全绿；golden corpus 50 份端到端转正验收（6 空坏样本零快照）。
- **依据**：SRS §14 M4、§2.2（幂等/fallback 留原因）、§4.9（五值决策+预算）、§4.10（pre-commit revision check）、§8.3A（快照唯一性演进）、§9.2/§9.4/§9.5；ADR-0003 D-015（显式枚举）、D-022（Protocol 分层）、D-032（影子包路径怪癖——e2e 脚本 sys.path 必须仓库根置顶）。
- **影响**：新增 `mining/parse_operator/`、`mining/snapshot_store/`、`contracts/{parse_plan,snapshot_store}.py`、`tests/{parse_operator,snapshot_store}/`、`tests/contracts/test_m4_*.py`、`tests/parse_quality/test_gate_decisions.py`、`tests/shadow_parse/test_parse_run_lifecycle.py`、`tests/golden_corpus/test_corpus_commit.py`、`databases/asset_core/schemas/010_m4_parse_run_state_machine{,_postgresql}.sql`、`var/e2e/_e2e_m4_quality_gated.py`、`docs/文档解析平台化-里程碑报告/M4-质量门控解析资产.md`；改 `contracts/state_machines.py`、`shadow_parse/{contracts,repositories_memory,repositories_pg,service}.py`、`parse_quality/{gate,metrics}.py`、`infra/pg_schema.py`（010 挂链）、009 双方言（索引降级）。

## D-034 ｜ M5 切片编译：兼容投影白名单收敛 / A08 重切闭环 / 只读视图
- **背景**：用户选 B（按计划 M5），并新增两项需求：①范式构建器对固定头部（解析+切片）的形态拍板——采用「骨架锁定 + 参数开放」（每条知识线必须有解析事实，不可删；参数档位可调——SegmentPolicy 即该面板的契约层）；②解析结果前端可视化必须契合系统（弃离线工具形态）。
- **决策与实现**：
  1. **兼容投影白名单收敛**：asset_raw_segments 的 block_type 有 CHECK 白名单 + 下游 enrich 按类型分支。新链类型（table_row/figure/list_item/quote…）在投影层映射到 legacy 词表（table_row→table、figure→image、quote→blockquote），行/图细节保留在 structure_json——DB 约束与下游 switch 零改动。PgSegmentStore.list 读回经逆映射，block_type 呈投影值（e2e 断言以 structure_json.table_header 判定行切片）。
  2. **token 口径**：字符近似（CJK 1 字≈1 token），策略阈值语义为字符上限；不引 tokenizer 依赖。结构边界优先，token 只是上限（§3.7）。
  3. **纯标题节独立成段**：标题文本本身是可检索内容（pptx-group 语料驱动）；实现注意空缓冲 flush 不得吞掉待定节标题（首版 bug，测试抓住）。
  4. **纯图样本 0 切片不伪造**：无可索引内容（无 caption 无文本）时允许 0 切片；golden 断言按「有文本期望的语料才要求非空」数据驱动。
  5. **A08 重切闭环**：commit 接受 compiler_fingerprint（进 snapshot_fingerprint）；SnapshotRecompileService 复用 IR 产新快照（质量结论沿用旧快照 + recompiled_from issue），旧快照/旧切片保留。
  6. **只读视图**：latest_for_document（快照+link 出生证明）加进 SnapshotRepository Protocol（M4 契约测试 fake 同步补方法）；GET /api/knowledge/documents/{id}/parse-result + kb-ui「结构化数据」页签（404=未走新链，显示引导）。
- **验证**：scoped 713 passed/9 skipped（M4 后 +12）；真实环境 e2e 三场景（编译落库含行切片带表头/只读视图/A08 重切新旧并存）全绿；kb-ui vue-tsc 0 错 + vitest 155 通过。
- **依据**：SRS §4.12/§C11/§3.10/§5.3/§8.2/§8.3/§2.3/§A08；用户 M5 前需求讨论（骨架+参数、系统契合可视化）。
- **影响**：新增 `contracts/segment_compiler.py`、`segment_compiler/{compiler,projection,service,repositories_memory,repositories_pg}.py`、`snapshot_store/read_service.py`、`api/routes/parse_result.py`、011 DDL 双方言、`tests/{segment_compiler,golden_corpus/test_corpus_segments,contracts/test_m5_ddl,snapshot_store/test_read_service}`、kb-ui（mining.ts + KbDocPreviewView 结构化数据页签）、`var/e2e/_e2e_m5_segments.py`；改 `snapshot_store/{service,repositories_memory,repositories_pg}`、`contracts/snapshot_store.py`、`api/{deps,app}.py`、`infra/pg_schema.py`（011 挂链）。

## D-035 ｜ M4/M5 对抗评审整改（用户指令「注意对抗评审」）
- **背景**：三路并行恶意评审（编排/转正层、切片编译层、只读 API+前端）。共 3 CRITICAL 候选 + 10 HIGH + 10 MEDIUM，甄别后修复 16 项、留档 6 项。
- **已修复**（全部带回归测试或 e2e 复跑验证）：
  1. **CRITICAL（转正层）**：同内容不同文档共享指纹时，复用分支不补写 link 且 link 指向新构造快照 id → 第二文档永远查不到快照。修复：memory/PG 复用分支均补写 link 并重定向到既有快照 id；PG 同时修复持连接取第二连接的自锁（min_size=1 死锁）。
  2. **Run 卡死（编排层 HIGH）**：提交期基础设施异常（DB 断连等）穿透 → Run 永久卡 EVALUATING。修复：`_commit_or_supersede` 兜底 except → 终态 FAILED。
  3. **探针校验（MED）**：快照被 REVOKED/DEPRECATED 后仍被幂等探针复用。修复：`_snapshot_reusable` 前置校验 lifecycle（注入 snapshots 可选依赖）。
  4. **排序稳定（MED）**：latest_for_document 字符串时间戳排序 + 同秒无 tie-break。修复：memory 加 id tie-break；PG ORDER BY 加 linked_at/id。
  5. **编译器 4 项 HIGH**：超长段落紧跟标题的序错位+重复标题段（二分切片继承待定标题并清挂起）；表格/图切片标题链恒空（传入 stack）；block_type 白名单不闭合（未知回落 unknown，杜绝 INSERT 击穿 DB CHECK）；合并上限口径失真（strip 长度 + 标题计入）。
  6. **切片落库 3 项**：PgSegmentStore 显式事务包裹删除+插入（防空快照）；IR sha256 非法记录按完整性事故拒绝（不再静默跳过）；recompile 源快照 FAIL 防御性拒绝。
  7. **011 DDL**：links 表补 FK（ON DELETE CASCADE）+ 行级唯一索引（防重复编译产生重复 link）。
  8. **只读 API+前端 3 项**：404 判定改 HTTP 状态码（原正则匹配不到后端 detail，引导文案永不生效）；elements 限界 {count, items[:500]}（防大文档无界响应）；IR 制品缺失统一 404（不抛裸 500）+ 前端重试按钮。
- **留档未修（M6 已知缺口，按影响排序）**：
  1. parse-result 端点与 download 同样只有 require_domain，无 KB 成员可见性裁剪（存量模式，非新链特有）——M6 统一接入 serving 侧 KbAccessService 式校验。
  2. Run 级并发互斥（同输入并发双跑产生重复 Run/双倍解析费用）——需 advisory lock 或部分唯一索引。
  3. storage_objects register 的 find→put→register 竞态（同对象双注册）——需 (bucket,object_key) 唯一索引 + ON CONFLICT。
  4. raw artifact 全量驻内存（超大文本格式 replay 内存峰值 3-4 倍）。
  5. M2 upsert 与 M4 set_status 共享 Run 行命名空间，理论上可覆写审计。
  6. min_tokens 契约参数未参与编译行为（改值只会触发无谓重切）——要么实现小片段合并，要么从指纹剔除。
- **验证**：scoped 全量 **715 passed/9 skipped**；真实环境 e2e 三场景复跑全绿；kb-ui vue-tsc 0 错 + vitest 155 通过。

## D-036 ｜ M6 工作流接入：版本感知骨架 / 同步门面 / 不混跑原则
- **背景**：M6 将新解析链路接入挖掘工作流（SRS §10.2/§10.3）。用户前期拍板：固定头部=骨架锁定+参数开放；v1 历史任务必须继续可跑。
- **决策与实现**：
  1. **替换式演进 + 版本感知骨架**：catalog 同时注册 parse_segment（v1 兼容）与 document_parse/segment_compile（v2）；编译器按 manifest schemaVersion 选择固定骨架集合（v2 不再要求 parse_segment，v1 不要求新算子）——历史 manifest 不改写（§10.3），七套范式模板参数化生成 v2 版。
  2. **不混跑原则**：v2 骨架下 document_parse 组件未接线→显式 FAILED（不静默回落旧解析）；文档无新链对象（storage_path 旧形态/对象未注册）→ SKIP——保证 v2 产出的每条知识线都有快照与证据链，避免新旧解析结果在同一 Run 内混杂。
  3. **同步门面桥接**：workflow handler 是同步调用、M4/M5 服务是 async（仓储契约）——`_run_sync` 双环境桥接（无 loop 直接 asyncio.run；在 loop 线程退一次性线程池）。门面从 storage_objects **注册行**取真实 bucket/key 构造冻结输入（首版按 sha 推算 key 曾导致 bucket 不匹配，e2e 抓住后修正——对象位置以注册行为准，不猜测）。
  4. **组合根双形态**：build_new_chain_services 传 pool 即 PG/MinIO 生产组件，缺省 memory/Fake——单测与真库 e2e 共用同一装配代码。
  5. **参数档位即算子 options**：DocumentParseOptions（质量档/后端预算）与 SegmentCompileOptions（六字段与 SegmentPolicy 一一对应）注册进 OPTIONS_BY_OPERATOR——范式构建器面板直接消费 json schema 校验。
  6. Run 终态非 SUCCEEDED 时门面抛错（含 error_message）→ handler 归一算子失败——失败原因在任务运行记录可见，不留静默 SKIP。
- **验证**：M6 新增 23 用例 + 既有工作流套件零回归（合计 152 passed）；真实环境 e2e 三场景（handler 驱动真解析转正/切片真表落库行带表头/旧链文档 SKIP）全绿且清理彻底。
- **未达成如实留档**：范式构建器前端锁定头部+参数面板（R7）、v2 模板投产灰度、在线发布切换开关——见 M6 报告 §5。
- **影响**：新增 `workflow/new_chain_services.py`、`tests/test_m6_{workflow_operators,handlers,facades,workflow_e2e}.py`、`var/e2e/_e2e_m6_workflow.py`、M6 报告；改 `workflow/operators/{catalog,options}.py`、`workflow/compiler.py`（FIXED_TYPES 版本化）、`workflow/templates.py`（参数化+v2）、`workflow/handlers/document.py`（双 handler）；既有 catalog/schema 测试同步（算子 16→18）。
