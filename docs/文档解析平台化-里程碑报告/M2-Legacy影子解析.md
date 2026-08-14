# 里程碑 M2：Legacy Shadow Parse —— 交付报告

> 日期：2026-08-14
> 分支：`feat/doc-parse-platform-m0`（提交见 §7）
> 决策留档：ADR-0003 **D-027**（本里程碑全部自主决策）
> 上游：M0（契约冻结）+ M1（MinIO 文件地基，真实环境已接通）

## 1. 范围

SRS §14 M2 = WP3（Parse IR SDK）+ WP4（Legacy Adapters）+ WP2 解析模型子集，退出条件：

> 当前 parser 能从 MinIO Frozen Input 读取内容并 shadow-write 新 Snapshot 解析制品，不影响现有发布。

按既定**压缩策略**执行：legacy 适配器只包 **Markdown / TXT**；PDF/DOCX/Excel/HTML 等复杂格式的保真解析全部留给 M3 Docling（调研报告 §1.2 的既定结论：legacy 对复杂格式无容器/坐标/表格网格保真能力，SRS §4.5 路由表中 MD/TXT 的 primary 本就是 native adapter）。

WP3 的 IR 类型/校验器在 M0 已交付（`contracts/parse_ir/`），M2 补齐的是**消费侧**：Adapter SDK 契约 + 两个真实适配器 + 影子写入链路。

## 2. 交付物清单

### 2.1 Parser Adapter SDK 契约（SRS §C06/C07/C04 子集）
`knowledge_mining/mining/contracts/parser_adapter.py`（新，纯 stdlib）：
- `DocumentParser` Protocol —— **同步纯函数** `parse(text, *, mime) -> BackendParseArtifact`；流式读 MinIO 是 Operator 的职责（§4.6），适配器因此可脱离基础设施测试
- `BackendBlock` / `BackendParseArtifact` —— 行号导向的候选制品，**保留 raw_output** 供 §9.5 replay（adapter mapping bug 免重跑后端）
- `ParseIRNormalizer` Protocol —— 后端块 → 项目 Parse IR 的唯一转换点
- `ParserDescriptor` + `BackendRegistry` —— C04 子集（确定性 `select_for(mime)`，路由规则/reason codes/fallback 留给 WP6/M3）

### 2.2 Legacy 适配器（WP4 压缩版）
`knowledge_mining/mining/parse_adapters/`（新包，旧链路零改动）：
- `legacy_markdown.py` —— `LegacyMarkdownParser`：复用 `infra/structure.py` 的 markdown-it token→block 转换（实测 SectionNode 树会吸收 heading 为节点 title、丢失块级身份与行号，token 级拍平才保真）；list 展开为逐条 `list_item`（行号逐行配对，数量不匹配回退整体 block 不伪造）；pipe table 保行列结构
- `legacy_txt.py` —— `LegacyPlainTextParser`：按空行分段，**不复现旧 `PlainTextParser` 的 300-token 切分**（调研报告 §1.5 风险项：parser 必须输出原子结构，切分是 Segment Compiler 的职责）
- `normalizer.py` —— `LegacyLineNormalizer`：block→element 类型映射（未映射落 `unknown` 不伪造）；单一 `section` 容器（page_number=None 不伪造，§3.6）；`stable_element_id(scope=source_raw_hash)`；**行级 EvidenceSpan**（source_locator{line_start,line_end} + text_range + raw_text，§A01 line-addressable）；heading 弹栈 parent 链 + `parent_of`；`next_in_reading_order`；table → `TableAsset`（首行 is_header、cell 保 raw）；产出必过 `parse_ir.validate`，error 级 issue 即 raise（§4.7）
- `registry.py` —— `build_default_registry()`：注册两个 descriptor

### 2.3 影子写入链路（SRS §C08 + M2 退出条件）
- **DDL 009**：`databases/asset_core/schemas/009_shadow_parse_runs{,_postgresql}.sql`（双版本，幂等）——新表 `asset_parse_runs`：幂等键 `UNIQUE(document_id, source_raw_hash, parser_fingerprint)`（§2.2 幂等复用），status 收窄为 SUCCEEDED/FAILED（影子运行无状态机，M4 才扩展）；挂载进 `infra/pg_schema.py` 链尾（**唯一修改的既有文件**）
- **`mining/shadow_parse/`**（新包）：
  - `contracts.py` —— `ParseRunRecord` / `ParseRunRepository` Protocol / `ShadowParseResult`（含 `reused`）
  - `repositories_memory.py` + `repositories_pg.py` —— 双实现；PG 版 `ON CONFLICT(幂等键) DO UPDATE RETURNING`，池需 `row_factory=dict_row`（项目既有惯例）
  - `service.py` —— `ShadowParseService.run(frozen)`：幂等探针 → `ObjectStoreSourceArtifactReader.open_stream` 流式 sha256 校验读 → 严格 UTF-8 → parse → normalize → IR JSON（sort_keys 内容寻址）→ put 到 `{prefix}parse` bucket（artifact_class=parse_ir + expected_sha256）→ `find_by_location` 去重后 register StorageObject → upsert SUCCEEDED 投影（element/container/relation 计数）。失败先落 FAILED 行再 re-raise
- **硬隔离**：绝不写 `asset_document_snapshots` / `asset_raw_segments` / `mining_run_documents`（"不影响现有发布"；Snapshot 正式提交是 M4/WP9）

### 2.4 测试
- `tests/parse_adapters/`（3 文件，34 用例）：MD 块级行号精确 / TXT 原子段（6000 词长文 = 1 element）/ 父链 / 阅读序 / 行级回溯 / TableAsset / stable id 确定性 / round-trip validate / 悬空引用拒绝
- `tests/shadow_parse/`（3 文件，11 用例）：服务编排（stub）+ **真实适配器集成**（`test_integration_real_adapters.py`：真实 MD/TXT 适配器 × ShadowParseService 全链路）+ PG 仓储（gated）

## 3. 测试结果

```text
tests/parse_adapters + tests/shadow_parse   45 passed, 1 skipped (PG-gated)
真实环境 e2e（121.89.90.178 MinIO + kb_db PG）：
  0. ensure_primary_schema OK（009 asset_parse_runs 建表）
  1. md: SUCCEEDED elements=7 relations=12 ir_sha_ok=True idempotent=True
  2. txt: SUCCEEDED elements=3 relations=2 ir_sha_ok=True idempotent=True
  3. asset_raw_segments / mining_run_documents 计数不变（零污染）
  E2E M2 SHADOW PARSE FULLY OK + cleanup done
全量回归：见 §7（提交时点数字）
```

## 4. 关键决策摘要（详见 ADR-0003 D-027）

| # | 决策 | 理由 |
|---|---|---|
| 1 | Adapter 同步纯函数（不读 IO） | Operator 负责冻结对象→文本，适配器可测性最大化（§4.6） |
| 2 | TXT 不复现 300-token 切分 | 原子 element 是解析事实，切分属 Segment Compiler（§3.7/§10.2） |
| 3 | MD 从 token 级拍平（不走 SectionNode 树） | 树会丢 heading 块级身份与行号（调研 §1.2） |
| 4 | 影子运行表 status 仅两态 | 完整 Parse Run 状态机是 M4；影子阶段一次执行直接落终态 |
| 5 | IR object key = IR 字节 sha 内容寻址 | 同 IR 必同 key，配合 find_by_location 去重（D-002/D-020） |
| 6 | 影子链路零写发布表 | M2 退出条件"不影响现有发布"；快照正式化在 M4/WP9 |

## 5. SRS M2 退出条件核对（§14）

| 退出条件 | 结果 |
|---|---|
| 当前 parser 能从 MinIO Frozen Input 读取内容 | ✅ `ShadowParseService` 经 `ObjectStoreSourceArtifactReader.open_stream`（流式 + sha256 校验），真实 MinIO 验证 |
| shadow-write 新 Snapshot 解析制品 | ✅ canonical Parse IR JSON 落 `{prefix}parse` bucket（artifact_class=parse_ir，注册行含 sha256/size/版本），PG `asset_parse_runs` 投影（计数 + parser 指纹 + schema 版本） |
| 不影响现有发布 | ✅ 硬隔离：零写 snapshots/raw_segments/mining_run_documents，真实 e2e 断言两表计数不变；旧 ingestion/stages/workflow 代码零改动 |

## 6. 已知缺口（后续里程碑）

1. **multipart seam 未接**（M1 遗留）：`upload_part/complete/abort` 仍是 NotImplementedError
2. **路由器未建**：`select_for` 先注册先得，无 reason codes/fallback/budget（WP6/M3）
3. **影子链路未挂 workflow**：`document_parse`/`segment_compile` 算子拆分是 M6/WP11；当前影子链路是独立服务入口
4. **复杂格式无 legacy 路径**：PDF/DOCX/Excel/HTML 直接等 M3 Docling（压缩策略既定）
5. **`asset_parse_runs` 无 domain 列**：按 document_id 关联，域隔离由 document 侧保证（如需域级运维查询再补列）

## 7. 如何验证

```bash
# 单测 + 集成
cd knowledge_mining && python -m pytest tests/parse_adapters/ tests/shadow_parse/ -q
# 真实环境 e2e：脚本为本地验证工件（根目录 _*.py 按仓库惯例不入库），
# 可按 §3 的链路与断言复写；配置来自 storage.yaml / database.yaml
```

提交：见 git log `feat/m2-shadow-parse`（M2 全部改动 + 本报告 + ADR D-027）。

## 8. 留给 M3

- Docling Adapter（WP5）：PDF/DOCX/PPTX/XLSX → Parse IR，模型/依赖指纹
- File Inspector + Backend Registry 完整版 + Parser Router（WP6）：路由决策可解释、可版本化
- Parse Orchestrator 初版（WP7）：Parse Run 状态机、attempts、timeout/cancel
- golden corpus benchmark（30-50 份最小语料）
