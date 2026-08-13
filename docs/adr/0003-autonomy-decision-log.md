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

---

> 后续决策按 D-014… 追加。每个里程碑结束在对应交付报告中引用本日志条目。
