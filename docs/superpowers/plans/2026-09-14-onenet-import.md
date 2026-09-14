# 知识一张网切片接入 实施计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 47 号设计（`docs/下一阶段/47-知识一张网切片接入-实施设计-2026-09-14.md`）实现一张网产品文档切片接入：管理员导入向导后端 + 域公共库产出 + KB 引用复用 + 重同步 + 前端页面。

**Architecture:** 切片批次=新文件格式（MinIO 对象存全字段 JSONL，parse adapter `onenet_jsonl` 直入现有挖掘管线）；β 相对规则还原文件级 Document；`kb_document_refs` 引用表 + 范围 UNION 三类口径；`onenet_imports` 订阅记录支持 nid diff 重同步。

**Tech Stack:** Python/FastAPI（mining 服务）、Java/Spring MyBatis（serving）、Vue3（kb-ui）、PostgreSQL 迁移、pytest/vitest/junit。

**开发分支:** worktree `D:\mywork\AgenticKB\.claude\worktrees\onenet-import`，分支 `worktree-onenet-import`（基于 master@208f5a7）。

**外网开发约束（必须遵守）:**
- 无法访问一张网接口与真实 PG/MinIO——所有 HTTP 用 mock（httpx MockTransport / fakeredis 不需要，PG 用 fake connection 模式，参照 `knowledge_mining/tests/kb/` 现有 fake 模式）
- 测试命令：Python 在仓库根 `python -m pytest knowledge_mining/tests/onenet/... -q`；Java `mvn -f agent_serving_java/pom.xml test`；前端 `cd kb-ui && npx vitest run`
- 中文 API 测试的 GBK 坑不适用（不经过 curl）
- 部署/内网验证由用户执行（M5 不在本计划内）

---

## 已核实的关键接缝（实施依据，勿再猜）

| 接缝 | 事实 |
|---|---|
| 解析路由 | `workflow/new_chain_services.py:169 default_plan_factory`：`mime = raw_file.mime`（来自 `asset_storage_objects.mime`，`jobs/run.py:303 _kb_object_documents` 冻结）→ `registry.all()` 首个 `supports(mime)` 且 license ok 且 `resolve_pipeline(parser_id)` 非空 |
| 适配器注册 | `parse_adapters/factory.py _PIPELINE_FACTORIES`（parser_id → (parser 工厂, normalizer 工厂)）；`iter_native_parsers()` 供 registry 自动注册 descriptor |
| Normalizer 复用 | `parse_adapters/normalizer.py LegacyLineNormalizer`：block_type 映射（heading/paragraph/table），heading 用 `level` 建 parent 链，table 用 `block.structure={"columns":[...],"rows":[{col:val}]}` 建 TableAsset；构造参数 `parser_fingerprints={parser_id: fingerprint}` |
| 对象写入 | `kb/services/document_service.py _store_source_stream`（内容寻址 + mime 显式）+ `KbDB.insert_document_from_storage`（支持自定义 document_key）+ `find_document_by_key`（幂等查重） |
| 自动挖掘 | `kb/services/auto_mine.py enqueue_auto_mining`（排队/合并语义，永不抛） |
| Python 范围 CTE | `kb/db.py:758 _CURRENT_SNAPSHOT_CTE`（kb ANY + b.kb_id=d.kb_id + not deleted + selection active） |
| Java 范围 SQL | `AssetBuildDocumentSnapshotMapper.xml:36 selectLatestKbSnapshots`（同口径；`StructureRefService:127` 也走它）；Evidence/Structure 其它查询基于 ActiveScope.snapshotIds，无需改 |
| KB 可见性 | `kb/db.py is_visible`（admin/owner/public/member）；文档级路由用 `document.kb_id == kb_id` 验证归属 |
| MCP list-documents | `kb/routes/mcp_tools.py list_documents` → `kbdb.list_documents_in_kb` |
| 前端 | `kb-ui/src/router/index.ts`（ADMIN_ROUTES + siteRole 守卫）；api 客户端在 `src/api/`；KB 详情 `views/kb/KbDetailView.vue`；组件 `components/kb/` |
| 迁移编号 | asset_core 下一个 = 016；kb 下一个 = 012；同一 PG 库，按 schema 目录分组应用 |

---

## Chunk 1（M1）：配置 + 客户端 + 摸底 + TOC 扫描 + β 还原

### Task 1: OnenetConfig 配置加载

**Files:**
- Create: `knowledge_mining/mining/onenet/__init__.py`
- Create: `knowledge_mining/mining/onenet/config.py`
- Create: `main_control_service/config/system/onenet.example.yaml`
- Test: `knowledge_mining/tests/onenet/__init__.py`、`knowledge_mining/tests/onenet/test_config.py`

- [ ] **Step 1: 写失败测试** —— 配置从 yaml 路径或显式 kwargs 构造；缺失凭据时 `missing_credentials` 报错；默认值（token_url/search_url/source_type/timeout/拉取段宽/限速）。

```python
# test_config.py 要点
def test_config_from_file(tmp_path):
    p = tmp_path / "onenet.yaml"
    p.write_text("app_id: 'com.x'\nstatic_token: 'tok'\n", encoding="utf-8")
    cfg = OnenetConfig.from_file(p)
    assert cfg.app_id == "com.x"
    assert cfg.search_url == OnenetConfig.DEFAULT_SEARCH_URL  # http://apigw-cn-south02.huawei.com/api/search/source_type?source_type=0
    assert cfg.chunk_width == 10000

def test_missing_credentials_raises(tmp_path): ...  # 文件缺 static_token → ValueError("missing_credentials")
```

- [ ] **Step 2: 跑测试确认失败** Run: `python -m pytest knowledge_mining/tests/onenet/test_config.py -q`（预期 import error）
- [ ] **Step 3: 实现 config.py** —— `@dataclass(frozen=True) OnenetConfig`：app_id/static_token/token_url/search_url/source_type=0/timeout=300/chunk_width=10000/page_size=1000/throttle_seconds=0.3/max_part_id_default=None；`from_file(path)` 用 `yaml.safe_load`；环境变量覆盖 `ONENET_APP_ID/ONENET_STATIC_TOKEN`；`resolve_config()`（无参：先 env 后默认 yaml 路径 `main_control_service/config/system/onenet.yaml`，都没有→None，调用方按"未配置"处理）。example.yaml 只含占位符不含真实凭据。
- [ ] **Step 4: 跑测试通过** → commit `feat(onenet): 配置加载——yaml/env 双通道+example`

### Task 2: OnenetClient（自 kone_connector 移植 + httpx 可注入 transport）

**Files:**
- Create: `knowledge_mining/mining/onenet/client.py`
- Test: `knowledge_mining/tests/onenet/test_client.py`

- [ ] **Step 1: 写失败测试**（httpx.MockTransport 注入）——覆盖：token 获取（credential=base64(static_token)，响应 result 直填 Authorization）；token 过期 401 重取重试一次；查询原生模式（searchQueryList AND）；DSL 模式（dsl 字符串）；`count_source`（track_total_hits 拿真实 total）；`part_range`（asc/desc sort 拿 min/max）；`fetch_source_chunk`（range part_id 段拉取：sort 稳定、from 翻页、窗口>10000 抛 `ChunkWindowTooWide`、总数不符抛 `ChunkIncomplete`）；异常响应（无 searchResults 键）报错；请求失败 3 次退避（monkeypatch time.sleep）。
  - MockTransport 按 URL 分发：token_url 返回 `{"result": "Basic x"}`；search_url 校验 Authorization 头并按 body 返回切片
- [ ] **Step 2: 确认失败** → **Step 3: 实现移植** —— 从 `kone_connector/src/onenet_client.py` 移植，改造点：`requests` → `httpx.Client(verify=False, transport=transport)`（构造参数 `transport: httpx.BaseTransport | None`）；保留 token 缓存/401 重取/3 次退避/段拉取校验；常量 TOKEN_URL/SEARCH_URL_TPL 从 config 默认值取；类型注解 + 模块 docstring 注明实测来源（kone_connector 2026-09-07）。
- [ ] **Step 4: 测试通过** → commit `feat(onenet): 一张网客户端移植——httpx可注入/分段拉取/幂等校验`

### Task 3: probe 摸底（多字段查询 → source_id 去重）

**Files:**
- Create: `knowledge_mining/mining/onenet/probe.py`
- Test: `knowledge_mining/tests/onenet/test_probe.py`

- [ ] **Step 1: 失败测试** —— `search_documents(client, filters)`：filters={doc_name?, file_name?, doc_type?, language?}，fuzzy 字段用 fuzzy=true、精确字段 fuzzy=false，native 模式组合 AND；返回按 source_id 去重的文档行（首条切片元数据 + 切片命中数 + `capped` 标志：total≥10000 时 True）。`probe_source(client, source_id)` 返回 {source_id, doc_name, file_name, doc_type, parsed_version, publish_time, total_slices, part_min, part_max, product_line, pbi}（复用 client.count_source/part_range + 首 3 条样本）。
- [ ] **Step 2/3: 实现** —— 纯组合逻辑（client 之上），命中封顶提示字符串 `"results_capped"` 字段。
- [ ] **Step 4: 通过** → commit `feat(onenet): 摸底与多字段文档查询——source_id去重+封顶标志`

### Task 4: toc_scan 章节树轻量扫描

**Files:**
- Create: `knowledge_mining/mining/onenet/toc_scan.py`
- Test: `knowledge_mining/tests/onenet/test_toc_scan.py`

- [ ] **Step 1: 失败测试** —— `scan_toc(client, source_id, max_part_id=None) -> dict`：遍历 part_id 段（fields=["path","title","part_id"]），按 path 建树；节点字段 {path, title, depth, slice_count, part_min, part_max, children}；返回 {source_id, parsed_version(首切片), total_nodes, tree}。子集模式 max_part_id 生效。空 source → ValueError。**树构建复用 restore.build_path_tree（Task 5）**——测试先写树断言（嵌套结构、切片数聚合）。
- [ ] **Step 2/3: 实现** —— 分段遍历复用 client.fetch_source_chunk(fields=...)；解析 JSONL 行内存建树（dict 树，插入排序保序）。
- [ ] **Step 4: 通过** → commit `feat(onenet): TOC轻量扫描——三字段拉取+树聚合`

### Task 5: restore β 规则文件还原（核心算法）

**Files:**
- Create: `knowledge_mining/mining/onenet/restore.py`
- Test: `knowledge_mining/tests/onenet/test_restore.py`（含真实样例夹具）

- [ ] **Step 1: 失败测试** —— 用 `查询结果(前32条)-20260914094325.json`（拷入 `knowledge_mining/tests/onenet/fixtures/udg_sample_32.json`）+ 构造用例覆盖：
  1. β 规则：path 深度=2 的切片（path[-2] 有直属页面）归到文件 `path[-2]`，heading=path[-1]
  2. α 兜底：叶子自身有切片且无更深 path → 自身成文件
  3. 边界归并：深度>2 的切片（path[-2] 是标题层）向上归并到最近的有直属切片祖先
  4. 文件内容 = 文件直属切片 + 下层切片按 part_id 升序
  5. 文件排序 = min(part_id) 升序
  6. 真实样例：32 条 → 期望文件集合 {`IPFD-010001 接口管理特性概述`(12切片), `实现原理`(6), `IPFD-010002 支持VLAN子接口特性概述`(12), ...}——按样例手工推演断言
  7. 目录树：文件之上层级 → folder path（"/"分隔）
  8. 表格清洗：`[tbl_predict_start/end]` 剥离保留管道表格
  9. `rule_version="beta-1"` 常量
  10. 异常：path 为空切片 → 计入 `unassigned` 不抛
- [ ] **Step 2: 确认失败** → **Step 3: 实现**：
```python
# restore.py 核心签名
RULE_VERSION = "beta-1"

@dataclass(frozen=True)
class RestoredFile:
    file_path: str          # path[-2] 节点的完整 path（β）或叶子 path（α）
    heading_title: str      # 首切片 title 或节点名
    folder_path: str        # 文件之上层级 "/" 连接（剔除包名首段）
    slices: tuple[dict, ...]  # 按 part_id 升序
    part_min: int; part_max: int

@dataclass(frozen=True)
class RestoreResult:
    rule_version: str
    files: tuple[RestoredFile, ...]   # min(part_id) 升序
    folders: tuple[str, ...]          # 去重目录路径
    slice_count: int; unassigned: int

def build_path_tree(slices) -> dict: ...   # toc_scan 复用
def restore_files(slices, *, package_first_segment_drop=True) -> RestoreResult: ...
```
  - 归并算法：先建树；节点标记 `has_direct`（有直属切片）；对每个有切片的叶子/节点，β 判定：文件节点 = 该切片父节点若 has_direct 则父节点，否则向上找最近 has_direct 祖先，都无则切片自身节点（α）
  - ⚠️ 注意：β 的语义是「path[-2]=文件」，等价实现=「切片挂在文件节点的子标题下」；对样例中 `path[-2]` 本身 has_direct 的情况（如 slice[0] path 末段=控制接口震荡特性、其父=实现原理 has_direct→slice[0] 属于文件「实现原理」heading「控制接口震荡特性」）——**以真实样例断言为准，规则实现须让 32 条样例通过**
- [ ] **Step 4: 通过** → commit `feat(onenet): β规则文件还原——相对规则+α兜底+真实样例回归`

### Task 6: 段拉取与校验（import 用的 fetch+verify）

**Files:**
- Create: `knowledge_mining/mining/onenet/fetch.py`
- Test: `knowledge_mining/tests/onenet/test_fetch.py`

- [ ] **Step 1: 失败测试** —— `fetch_selection(client, source_id, selection, workspace: Path) -> FetchOutcome`：selection={subtrees: [path 前缀列表] 或 None(整包), max_part_id?}；分段拉取落 `workspace/parts/part_{lo}_{hi}.jsonl`（存在跳过=幂等）；完成后合并 `slices.jsonl`（段选择去重逻辑移植 `kone_connector/src/fetch_batch.py _select_segments`）；`verify`：nid 去重/part_id 去重/覆盖校验（移植 `_verify_file`）；**子树过滤**：拉取按 part_id 段（用 toc 的 part_min/part_max 收窄段范围），本地按 path 前缀过滤落盘；返回 {manifest: {...对齐 02_batch_protocol}, slices_path}。失败注入：段损坏（行非 JSON）→ FetchVerifyError 带样例。
- [ ] **Step 2/3: 实现** —— 移植 demo 的段选择/校验；selection 序列化进 manifest。
- [ ] **Step 4: 通过** → commit `feat(onenet): 批次拉取器——段幂等/子树过滤/完整性校验`

## Chunk 2（M2）：IR 适配器 + 导入编排 + 公共库落库

### Task 7: onenet_jsonl parse adapter（parser + 注册）

**Files:**
- Create: `knowledge_mining/mining/parse_adapters/onenet_jsonl.py`
- Modify: `knowledge_mining/mining/parse_adapters/factory.py`（_PIPELINE_FACTORIES + import）
- Test: `knowledge_mining/tests/onenet/test_onenet_adapter.py`

- [ ] **Step 1: 失败测试**：
  1. `OnenetJsonlParser.parse(jsonl_bytes, mime="application/x-onenet+jsonl")` → BackendParseArtifact：每个文件 JSONL（结构：每行 {nid, part_id, path, title, content}，**文件级首行 meta**？——设计定：每行独立切片，path 已含全部层级）产出 blocks：
     - 对每个切片：其 path 各层级（剔除与前一切片相同的前缀段）产出 `heading` block（level=层级深度 1..N，text=段名）——**注意 heading 只在新进入层级时产出**（连续同 path 切片不重复产 heading）
     - content → `paragraph` block（清洗 `[tbl_predict_*]` 标记）；content 内 `[tbl_predict_start]...[tbl_predict_end]` 块 → `table` block（structure={"columns": [首行管道单元格], "rows": [{col: val}...]}）+ 块间文本仍是 paragraph
     - 每个 block 带 `native_ref={"nid":..., "part_id":...}`（回源溯源）
  2. 不支持的 mime → UnsupportedFormat
  3. 空行/坏行 → warning 计数不抛
  4. descriptor：parser_id="onenet_jsonl"、supported_mimes=frozenset({"application/x-onenet+jsonl"})、license ok、local、parser_fingerprint 含 rule 常量
  5. **路由集成**：`build_default_registry()` 后 `factory.resolve_pipeline("onenet_jsonl")` 非 None；registry 中有 descriptor supports 专用 mime；`legacy_txt` **不** supports 该 mime
  6. **normalizer 集成**：`LegacyLineNormalizer(parser_fingerprints={ONENET_JSONL_PARSER_ID: ONENET_JSONL_FINGERPRINT}).normalize(artifact, source_raw_hash=...)` → ParsedDocument：heading 元素有 level、parent_of 关系成链、table 元素 + TableAsset、validate 通过
  7. 深度 10+ 层 heading 链正确
- [ ] **Step 2: 确认失败** → **Step 3: 实现 parser**（纯函数式：JSONL → blocks；表格解析复用 `kone_connector/src/ingest_kb.py _extract_tables` 的管道表格逻辑移植为 `_parse_pipe_table`）
- [ ] **Step 4: 通过** → commit `feat(onenet): onenet_jsonl 解析适配器——path树→heading块/表格块/native_ref溯源`

### Task 8: 迁移 016（asset_core）+ 012（kb）

**Files:**
- Create: `databases/asset_core/schemas/016_onenet_imports.sql`（含 `_postgresql.sql` 变体——对照现有迁移目录惯例，sqlite 变体仅当现有文件成对存在）
- Create: `databases/kb/schemas/012_kb_document_refs.sql`

- [ ] **Step 1: 检查现有迁移成对模式**（`ls databases/asset_core/schemas/` 对比 sqlite/pg 变体；015 是否成对）→ 按惯例写：
```sql
-- 016_onenet_imports.sql（核心列对齐 47 号 §四-4）
CREATE TABLE IF NOT EXISTS onenet_imports (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    source_id TEXT NOT NULL,
    doc_name TEXT,
    parsed_version_seen TEXT,
    total_slices BIGINT,
    fetched_max_part_id BIGINT,
    selection_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('queued','fetching','restoring','importing','mining','done','failed')),
    kb_id TEXT NOT NULL,
    folder_root_path TEXT,
    document_count INTEGER,
    error TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (domain, source_id)
);
-- 012_kb_document_refs.sql
CREATE TABLE IF NOT EXISTS kb_document_refs (
    kb_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (kb_id, document_id)
);
CREATE INDEX IF NOT EXISTS idx_kb_document_refs_document ON kb_document_refs(document_id);
-- 加 onenet_toc_cache（与 onenet_imports 同文件）
```
- [ ] **Step 2: 验证** —— 若本机有 docker PG 则 `docker compose exec postgres psql` 应用并 `\d` 检查；无 PG 则语法静态检查 + 既有迁移测试模式跑（查 `tests/` 有无 schema 应用测试）
- [ ] **Step 3: commit** `feat(db): onenet_imports/toc_cache/kb_document_refs 迁移`

### Task 9: 公共库 bootstrap + 导入编排 ImportService

**Files:**
- Create: `knowledge_mining/mining/onenet/import_service.py`
- Test: `knowledge_mining/tests/onenet/test_import_service.py`

- [ ] **Step 1: 失败测试**（fake KbDB/DocumentService/client 模式，参照 `tests/kb/` fake 惯例）：
  1. `ensure_public_kb(domain, actor)`：不存在则建（固定名「一张网产品文档」、visibility=private、metadata.kind=onenet、绑定默认范式 DEFAULT_WORKFLOW_ID——参照 kbs.py create_kb 的范式绑定）；已存在（含软删态）→ 复用/恢复；并发幂等由 UNIQUE(domain,name) 兜底（冲突→重查）
  2. `start_import(...)`：创建 onenet_imports 行（status=queued）；**异步任务**（asyncio.create_task，注册强引用——参照 archive_tasks 模式）
  3. 任务编排状态机：queued→fetching（调 fetch_selection）→restoring（restore_files）→importing（逐文件：对象写入 mime=application/x-onenet+jsonl + Document 登记 document_key=`onenet:{source_id}:{sha1(file_path)[:16]}` + directory_path=folder_path）→mining（enqueue_auto_mining 到公共库）→done（document_count、parsed_version_seen、fetched_max_part_id 回写）
  4. 失败：任一步抛错 → status=failed + error 真实原因；**重试幂等**：document_key 已存在（find_document_by_key）→ 复用该 Document 不重建
  5. 同 source 重复 start_import → 409 冲突（UniqueViolation 捕获）
  6. 文档 metadata_json：`{"source_system":"onenet","source_id":...,"logical":true,"file_path":...,"url":首切片url,"public_level":...,"parsed_version":...,"doc_name":...,"file_name":...,"language":...,"publish_time":...,"product_line":...,"pbi":...,"onenet_raw":{未映射字段包}}`
  7. 文件名 sanitize：file_path 末段 → 安全文件名（`/`→`_`，长度≤80，冲突加 part_min 后缀）
- [ ] **Step 2/3: 实现** —— 依赖注入（kbdb/doc_svc/client/fetch/restore 全部参数注入可测）；对象写入用 doc_svc 的 `_store_source_stream`（bytes → AsyncIterator 包装）+ `kbdb.insert_document_from_storage`（自定义 key）。
- [ ] **Step 4: 通过** → commit `feat(onenet): 导入编排——公共库bootstrap/状态机/幂等登记/自动挖掘`

### Task 10: /api/onenet 路由（管理面查询/摸底/TOC/导入/记录）

**Files:**
- Create: `knowledge_mining/mining/onenet/routes.py`
- Modify: `knowledge_mining/mining/api/app.py`（include_router）
- Test: `knowledge_mining/tests/onenet/test_routes.py`

- [ ] **Step 1: 失败测试**（httpx ASGI transport + fake service 层）：
  - `POST /api/onenet/search` {filters} → 文档列表（admin 守卫：非 admin 403）
  - `POST /api/onenet/probe` {source_id} → 摸底卡片
  - `POST /api/onenet/toc` {source_id} → 树（写 onenet_toc_cache；parsed_version 匹配则复用缓存）
  - `POST /api/onenet/imports` {source_id, selection} → 202 {import_id}；重复 409
  - `GET /api/onenet/imports?domain=` → 记录列表
  - `GET /api/onenet/imports/{id}` → 详情（含 documents 列表——按 document_key 前缀 `onenet:{source_id}:` 查公共库）
  - `POST /api/onenet/imports/{id}/resync` → 202（Chunk 4 实现，先桩 501？**不**——直接 Task 13 实现，此路由延后到 Chunk 4 再加）
  - `PATCH /api/onenet/imports/{id}/selection` → 合并 selection（Chunk 4）
  - 鉴权模式参照 `kb/routes/kbs.py` 的 admin 断言；域名取法参照 mcp_tools 的 domain 解析
- [ ] **Step 2/3: 实现**（路由薄壳，逻辑全在 service）
- [ ] **Step 4: 通过 + app.py 注册** → commit `feat(onenet): 管理面API——search/probe/toc/imports`

## Chunk 3（M3）：引用机制 + 范围 UNION 三类口径

### Task 11: refs 服务 + Python 范围接缝

**Files:**
- Create: `knowledge_mining/mining/onenet/refs_service.py`
- Modify: `knowledge_mining/mining/kb/db.py`（_CURRENT_SNAPSHOT_CTE UNION + list_referenced_documents + document_in_kb_or_referenced + is_visible 不动但新增 refs 分支的辅助）
- Test: `knowledge_mining/tests/onenet/test_refs.py`、`knowledge_mining/tests/kb/test_refs_scope.py`

- [ ] **Step 1: 失败测试**：
  1. `RefsService.add_refs(kb_id, document_ids, actor)`：校验每个 document 属**同域** onenet 公共库（kb metadata.kind=onenet）且未软删——**服务端强制**；目标 kb 可写（can_write）；批量插入 ON CONFLICT DO NOTHING；返回 {added, skipped}
  2. `remove_refs(kb_id, document_ids)` / `list_refs(kb_id)`（join 文档名/状态）
  3. **CTE 接缝**：fake connection 捕获 SQL——`_CURRENT_SNAPSHOT_CTE` 改造后 SQL 含 `OR d.id IN (SELECT document_id FROM kb_document_refs WHERE kb_id = ANY(%(kb)s))`；语义测试：fake 行为（自有 active + 引用 active + 引用软删排除）
  4. `list_documents_in_kb` **不变**（owned-only）；新增 `list_referenced_documents(kb_id)`：refs JOIN asset_documents（带 document_name/status/directory_path/kb 来源名）
  5. `document_in_kb_or_referenced(kb_id, document_id)` → bool（文档详情/预览/下载路由的归属校验改造用）
  6. 统计口径：`stats_assets`/`overview_status_counts` **不加 refs**（快照不变式测试：SQL 不含 kb_document_refs）
- [ ] **Step 2/3: 实现** —— CTE 改动点（注意 `b.kb_id = d.kb_id` 条件在引用文档上：公共库 build 的 kb_id=公共库 id ≠ d.kb_id? **d.kb_id=公共库**且 b.kb_id=公共库——成立**；但 WHERE 的 kb ANY 是请求库——改法：
```sql
WHERE (b.kb_id = ANY(%(kb)s) OR EXISTS (
         SELECT 1 FROM kb_document_refs r
         WHERE r.kb_id = ANY(%(kb)s) AND r.document_id = d.id))
  AND b.kb_id = d.kb_id AND d.deleted_at IS NULL
```
  同步改 `get_current_serving_snapshot` 的来源 link document 绑定（42 号 O3 已按 document 绑定，不动）
- [ ] **Step 4: 通过 + 既有 kb 测试回归**（`python -m pytest knowledge_mining/tests/kb -q`）→ commit `feat(onenet): 引用服务+Python范围UNION——CTE/归属校验/引用列表/统计口径隔离`

### Task 12: 文档路由 + MCP list-documents 引用可见

**Files:**
- Modify: `knowledge_mining/mining/kb/routes/documents.py`（get_document/preview/download 的归属校验放宽为 in_kb_or_referenced；引用文档写操作 403）
- Modify: `knowledge_mining/mining/kb/routes/mcp_tools.py`（list_documents 合并 referenced=true）
- Test: `knowledge_mining/tests/onenet/test_refs_routes.py`

- [ ] **Step 1: 失败测试** —— 引用文档：GET 详情/预览/下载 200；PATCH/DELETE/move/replace 403 with `referenced_readonly`；自有文档行为不变；MCP list-documents 返回合并列表（referenced 标志）
- [ ] **Step 2/3: 实现**（最小 diff：归属断言函数替换）
- [ ] **Step 4: 通过** → commit `feat(onenet): 引用文档只读可见——路由归属放宽+写拒绝+MCP合并`

### Task 13: Java 范围接缝 + 引用路由

**Files:**
- Modify: `agent_serving_java/src/main/resources/mapper/AssetBuildDocumentSnapshotMapper.xml`（selectLatestKbSnapshots UNION refs）
- Create: `agent_serving_java/src/test/java/.../AssetBuildDocumentSnapshotMapperTest.java`（或扩展现有 mapper 测试——先看现有测试基建）
- Test: Java 侧若现有 mapper 测试用真实 PG（IT）则本机跳过、写 BoundSql 断言测试（参照 44 号 R2 的 BoundSql 展开验证法）

- [ ] **Step 1: 失败测试** —— MyBatis 配置加载 mapper → BoundSql 展开含 refs 子查询；kbIds 参数化正确
- [ ] **Step 2/3: 实现 SQL**：
```xml
WHERE (b.kb_id = d.kb_id AND d.kb_id IN (...kbIds...)
       OR (b.kb_id = d.kb_id AND EXISTS (
             SELECT 1 FROM kb_document_refs r
             WHERE r.document_id = d.id AND r.kb_id IN (...kbIds...))))
```
  （保持 DISTINCT ON / selection active 语义不变；文档软删过滤已有）
- [ ] **Step 4: `mvn test` 通过** → commit `feat(onenet): Java检索范围UNION——selectLatestKbSnapshots吃引用`

### Task 14: 引用 API 路由

**Files:**
- Modify: `knowledge_mining/mining/onenet/routes.py`（POST /api/kb/{kb_id}/onenet/refs、DELETE、GET——注意挂 kb 前缀需新 router 或挂 kb_documents 路由；设计：独立 router `APIRouter(prefix="/api/kb/{kb_id}/onenet")`）
- Test: 扩 `test_refs_routes.py`

- [ ] **Step 1: 失败测试** —— 建/删/列引用（权限：can_write 才能建/删；is_visible 能列）；跨域文档 403 `cross_domain_reference`
- [ ] **Step 2/3/4: 实现+通过** → commit `feat(onenet): KB引用API——批量建/删/列`

## Chunk 4（M4）：重同步 + facets + 前端

### Task 15: 重同步（探测/diff/传播）

**Files:**
- Create: `knowledge_mining/mining/onenet/resync.py`
- Modify: `routes.py`（POST /{id}/resync + PATCH /{id}/selection）
- Test: `knowledge_mining/tests/onenet/test_resync.py`

- [ ] **Step 1: 失败测试**：
  1. `probe_changes`：parsed_version/total/part_max 三信号比对 → {changed: bool, signals}
  2. `resync(import_id)`：无变化→{changed:false}；有变化→重放 selection 重拉（workspace 复用幂等段）→ 新旧切片集合 nid diff（新增/消失/内容变更——content hash 对比）→ 按文件粒度归并（文件内任一切片变→文件受影响）→ 受影响文件：对象重写+Document content_revision 递增（复用 replace 语义或重登记）+ 入挖掘；消失文件的 Document 软删 + **kb_document_refs 行删除**
  3. selection PATCH：合并 subtrees（去重）后保存，下次 resync 按新 selection 重放（新子树文档按 document_key 补建）
  4. 失败：拉取失败 → import status=failed，旧文档不动
- [ ] **Step 2/3: 实现** —— diff 基于旧 slices.jsonl（workspace 保留）与新拉取集
- [ ] **Step 4: 通过** → commit `feat(onenet): 重同步——三信号探测/nid diff/文件级传播/引用清理`

### Task 16: facets 段级搬运（language/product_line/category）

**Files:**
- Modify: `knowledge_mining/mining/retrieval_projection/projector.py` 或 segment 编译处（先定位 raw_segment.metadata_json 写入点：`retrieval_projection/persist.py`）——把 document.metadata_json.onenet 三字段写入每 representation 的 facets
- Test: `knowledge_mining/tests/onenet/test_facets_transport.py`

- [ ] **Step 1: 定位**（grep persist.py metadata_json 写入）→ **Step 2: 失败测试**（含 onenet metadata 的文档投影 → representations facets 含 language/product_line/category；非 onenet 文档不受影响；PROJECTOR_VERSION 变更断言）→ **Step 3: 实现**（最小侵入：投影入口读 document metadata，注入 facets）→ **Step 4: 通过 + retrieval_projection 回归** → commit `feat(onenet): facets段级搬运——language/product_line/category注入投影`

### Task 17: 前端 API 客户端 + 管理员导入页

**Files:**
- Create: `kb-ui/src/api/onenet.ts`
- Create: `kb-ui/src/views/onenet/OnenetAdminView.vue`（多步向导：查询→摸底卡片→TOC 树勾选（el-tree，切片数 badge）→导入确认→记录列表（状态/重试/重同步/selection 编辑））
- Modify: `kb-ui/src/router/index.ts`（admin-only 路由 + ADMIN_ROUTES）
- Modify: 导航菜单（AppLayout 找到菜单注册处）
- Test: `kb-ui/src/views/onenet/__tests__/OnenetAdminView.spec.ts` + `api/__tests__/onenet.spec.ts`

- [ ] **Step 1: 失败测试** —— api 客户端方法与端点对齐（search/probe/toc/imports/refs/resync）；向导组件：步骤流转、树勾选收集（subtrees=选中节点 path 集合）、封顶提示渲染、导入记录状态徽标
- [ ] **Step 2/3: 实现**（遵循现有 view 风格：script setup + proxyClient；测试参照 `views/kb/__tests__/` 现有模式）
- [ ] **Step 4: `npx vitest run` + `npm run build` 通过** → commit `feat(ui): 一张网管理员导入页——多字段查询/TOC勾选/导入记录`

### Task 18: KB 引用入口 + 外部引用 tab + 预览标注

**Files:**
- Create: `kb-ui/src/components/kb/OnenetRefPanel.vue`（已导入文档列表→树勾选→一键引用/引用管理）
- Modify: `kb-ui/src/views/kb/KbDetailView.vue`（挂载入口按钮/tab）
- Modify: `kb-ui/src/components/kb/KbFileManager.vue`（「外部引用」只读 tab：list_referenced_documents，不可操作，仅取消引用）
- Modify: `kb-ui/src/views/kb/KbDocPreviewView.vue`（metadata.source_system=onenet → 「由切片重建的逻辑文档」标注 + url 回源链接；预览渲染：JSONL 对象的预览走专用端点或前端即时还原——**定**：后端预览路由对 onenet mime 返回还原 markdown（restore 纯函数复用），前端无特殊逻辑）
- Modify: `knowledge_mining/mining/kb/routes/documents.py` 预览（onenet mime 分支）
- Test: 组件测试 + 后端预览测试

- [ ] **Step 1: 失败测试**（组件：树勾选→refs API 调用载荷；外部引用 tab 渲染与只读；预览标注条件渲染。后端：onenet 对象预览返回 markdown）
- [ ] **Step 2/3: 实现** → **Step 4: vitest + build + 相关 pytest 通过** → commit `feat(ui): KB一张网引用——引用面板/外部引用tab/逻辑文档预览`

## Chunk 5：回归 + 审查

### Task 19: 全量回归

- [ ] `python -m pytest knowledge_mining/tests -q`（排除 PG 依赖标记——参照 42 号跑法：先全跑看跳过）
- [ ] `mvn -f agent_serving_java/pom.xml test`
- [ ] `cd kb-ui && npx vitest run && npm run build`（**必须 build**——vue-tsc 空检查坑）
- [ ] 修复回归问题（若有），commit

### Task 20: 代码审查（code-reviewer + security-reviewer 子代理）

- [ ] 派 code-reviewer：M1-M4 全部 diff（质量/一致性/契约）
- [ ] 派 security-reviewer：新路由（鉴权/输入校验/凭据不外泄/SSRF——search/probe/toc 的 source_id/filters 参数校验）
- [ ] CRITICAL/HIGH 修复 + 回归 → commit

### Task 21: 文档与交付

- [ ] `docs/下一阶段/48-知识一张网切片接入-实施交付报告-<日期>.md`：实现清单/接缝审计表（kb_id 全量点分类标注）/测试结果/内网验收步骤（M5：UDG 全量校准清单、deploy-sync 注意事项）
- [ ] 00-README 索引更新
- [ ] 最终 commit；**不合并 master、不 push**（用户审阅后定）

---

## 内网验收清单（写给用户，M5）

1. 配置 `main_control_service/config/system/onenet.yaml`（真实 app_id/static_token）
2. UDG 子集导入（--max-part-id 2000 等价：向导里选小子树）→ β 规则文件数与预期比对（样例回归基线）
3. UDG 全量导入 → 公共库文档数/目录树/挖掘成功率/检索命中
4. 业务库引用 → 检索/章节下钻/表格查询 → 取消引用收窄
5. 重同步演练（上游 parsed_version 变化模拟）
