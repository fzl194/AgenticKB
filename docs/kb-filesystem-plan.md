# 知识库：云端文件管理系统 —— 开发与测试规划

> **状态**：✅ 已实现（G1–G5 落地，G6 收口）
> **版本**：v0.2（2026-07-28）
> **分支**：`feat/kb-management`
> **范围**：把知识库从「带目录字段的文件列表」升级为真正的**云端文件管理系统**——层级文件夹、创建文件夹、拖拽移动、在线预览；并修复底层「文档身份 vs 当前位置」混淆的缺陷，使移动/改名不丢挖掘历史。
> **前置**：KB 管理 P1–P6 + 前端 F0–F6 已交付（建库/上传/挖掘/成员/设置），本规划在其之上。
>
> **实现状态（v0.2）**：
> - **G1 身份修复** ✅ `503b48e` — storage_path 查身份 + document_key 冻结；连带修复 `uq_asset_documents_domain_document_key` 残留约束（003 在 004 之后又加回域级唯一，挡住同域多库同路径）；legacy upsert_document 去依赖域级约束。32→46 测试。
> - **G2 kb_folders 一等文件夹** ✅ `48b1d15` — 表 + CRUD + 磁盘镜像 + 权限。
> - **G3 移动/改名** ✅ `2e51def` — 文件 + 文件夹子树，身份键不变，环检测，磁盘 mv + 失败补偿。
> - **G4 在线预览** ✅ `43296db` — KbFilePreview 抽屉，复用 download blob 按类型渲染。
> - **G5 文件管理器 UI** ✅ `43296db` — KbFileManager（**主区域层级目录，非左树**——D5 调整），面包屑 + 卡片网格 + 拖拽移动。
> - **决策落地**：D1=kb_folders 一等表；D2=legacy 不兜底；D3=文本/图/PDF（office 下载）；D4=撤回 UI 占位灰显；D5/UI=文件管理器式主区域（非左侧树）。
> - **测试**：后端 46/46 kb 测试通过；前端 vue-tsc + vite build 通过、vitest 50/50 通过。

---

## 0. 调研结论（决定方案的关键事实）

### 0.1 文档身份模型现状与缺陷
- `document_key = "doc:/{relative_path}"` 同时承担「逻辑身份（应不变）」与「当前位置（会变）」两个职责。
- 挖掘运行态查找身份 `get_document_lifecycle_state` / `get_document_by_key` 按 `(domain, document_key)` 查、**不带 kb_id**（`infra/db.py:340,444`）→ **多库同 document_key 歧义**（Alice/KB1 与 Bob/KB2 都放 `qos.pdf` 时，挖 KB2 可能挂到 Alice 的文档上）。
- `mining_run_documents` **同时有 `document_id`（commit 时写）和 `document_key`（register 时写）**；`derive_document_status` 当前 join `document_key`（`kb/db.py:317`）。
- 其余关联（`asset_document_snapshot_links`、`asset_build_document_snapshots`）早已挂 `document_id`，与 `document_key` 无关。

### 0.2 内容去重（已正确，无需改）
- `asset_document_snapshots` `UNIQUE(domain, normalized_content_hash)` → 同域同内容只挖一份 snapshot，多库共享；第二次挖廉价（仅建 link）。
- 磁盘**不去重**（每库独立原始字节，有意）；跨域不去重（有意隔离）。

### 0.3 文件夹：存储/挖掘已支持层级，缺一等文件夹
- 磁盘已分层：`<upload_root>/<kb_id>/<dir>/<sub>/<file>`；zip 解压保留层级。
- 挖掘 `ingest_directory` 用 `rglob("*")` **递归 walk**，`relative_path` 含层级 → 挖掘天然处理嵌套文件夹。
- 缺口：`asset_documents.directory_path` 是**扁平字符串**，没有一等文件夹实体 → **不能有空文件夹**、无文件夹元数据、无「整文件夹移动」。

### 0.4 在线预览：现状与能力
- 现有 `/api/knowledge/documents/{id}/raw-content` 返回**抽取文本**（format ∈ markdown/html/plain），前端用 `marked`+`DOMPurify` 渲染（`DocumentDetailView.vue:323`）。
- KB 文档目前**只有 download（blob）**，无预览端点。
- 依赖已有：`marked`、`dompurify`、`codemirror`（代码）。**无** pdf.js / office 渲染库。
- 浏览器原生可渲染：图片（`<img>`）、PDF（`<iframe>`/`<embed>` 走浏览器内置 PDF.js）。

### 0.5 前端能力
- Element Plus `el-tree` 原生支持 `draggable` + `allow-drop` + `allow-drag` → 拖拽移动无需新依赖。
- 已有 `createProxyClient('mining')` 代理范式、`saveBlob`/`filenameFromDisposition` 下载工具、`StatusBadge`/`EmptyState` 通用组件。

---

## 1. 身份模型修复方案（核心，先行）

**原则：身份（永不变）与位置（可变）分离。**

| 概念 | 字段 | 可变 | 用途 |
|---|---|---|---|
| 逻辑身份 | `asset_documents.id` | 永不变 | snapshot link / build selection 的关联 |
| 冻结键 | `document_key` | **冻结**（首次上传值） | `mining_run_documents` ↔ `asset_documents` 的稳定 join |
| 当前位置 | `directory_path` / `document_name` / `storage_path` | 可变 | 展示、下载、挖掘 walk-time 查找身份 |

**改动点（比预想收敛，`derive_document_status` 无需改）：**

1. **挖掘 walk-time 查身份：按 `storage_path` 而非 `document_key`**
   - 新增 `AssetCoreDB.get_document_by_storage_path(domain, storage_path)`；`get_document_lifecycle_state` 增 `storage_path` 入参，过滤从 `document_key=` 改 `storage_path=`。
   - pipeline 已有 `relative_path`，`storage_path = str(input_path / relative_path)`。`storage_path` 含 `<kb_id>` 前缀 → **全库唯一**，顺带消解多库歧义。
2. **`mining_run_documents.document_key` 写「冻结键」而非「walk 路径」**
   - register 时（`run.py:~922`）：若身份已找到（lifecycle_state 非空），用身份行的 `document_key`（冻结值）；新文档用 walk 派生值（= 首次冻结值）。
   - 效果：同一文件移动前后多次挖掘，`mining_run_documents.document_key` 恒等于冻结键 → **`derive_document_status` 的 join 不变、不断链、不丢 failed 状态**。
3. **`document_key` 冻结**
   - KB 的改名/移动操作**不更新** `document_key`；`select_or_create_snapshot` 改用 `storage_path` 查身份（不再 `get_document_by_key`）。
4. **审计**：grep `mining_runtime` 所有 `document_key` 消费点（`get_run_document_by_key`、`get_committed_document_keys` 等），确保一致使用冻结键，不存在「按 walk 路径反查」的残留。

**legacy `/api/runs`（deprecated）取舍**：其文档 `storage_path` 可能为 NULL → storage_path 查找失败 → 首次重挖当 NEW 处理一次。可接受（已废弃路径）。如需兜底，加「storage_path 查不到 → 回落 (domain, document_key)」双查，代价是 legacy 仍带歧义。**默认不兜底**，列为本规划决策点 D2。

---

## 2. 文件夹一等化（kb_folders 表）

**结论：「创建空文件夹」要求升级到一等文件夹表**（推翻前端计划 v0.1 的 B 档「无新表」选择——那时未要求创建文件夹）。

`kb_folders` 表（新增，`databases/kb/schemas/004_kb_folders.sql`）：

```sql
CREATE TABLE kb_folders (
    id            TEXT PRIMARY KEY,
    kb_id         TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    parent_id     TEXT REFERENCES kb_folders(id) ON DELETE CASCADE,   -- NULL = 根
    name          TEXT NOT NULL,
    path          TEXT NOT NULL,        -- 规范化完整路径，如 "5G/AMF"（根为 ""），便于展示与唯一约束
    created_at    TEXT NOT NULL,
    created_by    TEXT REFERENCES kb_users(id) ON DELETE SET NULL,
    UNIQUE (kb_id, parent_id, name)     -- 同一父下名字唯一
);
```

**不变量**：
- `kb_folders` 是文件夹结构的**唯一真相源**；磁盘目录与之镜像（建文件夹→mkdir，删空文件夹→rmdir）。
- `asset_documents.directory_path` 仍保留（挖掘 walk 与 storage_path 派生依赖它），值 = 文件所在文件夹的 `path`。
- 建文件夹 = insert `kb_folders` + `mkdir`；可空。
- 删文件夹 = 仅当无子文件夹且无文档时，删行 + rmdir；非空则 409 提示。
- 移动文件夹 = 递归改该子树所有 `kb_folders.path`、受影响文档的 `directory_path`/`storage_path` + 递归 `mv` 磁盘子树。**document_key/id 不动**。

`pg_schema.py` 的 ddl_paths 加入 `004_kb_folders.sql`（在 KB 三表之后、`004_kb_isolation` 之前/之后均可，无 FK 依赖 `asset_documents`）。

---

## 3. 移动 / 改名（文件与文件夹）

新增 KB 服务方法 + 端点（均要求 `can_write`，viewer → 403）：

| 操作 | 后端 | 对象变更 | 磁盘 | 身份键 |
|---|---|---|---|---|
| 改名文件 | `PATCH /api/kb/{kb}/documents/{doc}`（扩展现有） | `document_name` | rename 文件 | 不变 |
| 移动文件 | `POST /api/kb/{kb}/documents/{doc}/move` `{target_folder_id}` | `directory_path`/`storage_path` | mv 文件 | 不变 |
| 新建文件夹 | `POST /api/kb/{kb}/folders` `{parent_id, name}` | insert `kb_folders` | mkdir | — |
| 改名文件夹 | `PATCH /api/kb/{kb}/folders/{id}` `{name}` | 改本文件夹 + 子树 path/受影响文档 | rename 目录 | 文档不变 |
| 移动文件夹 | `POST /api/kb/{kb}/folders/{id}/move` `{target_folder_id}` | 子树 path 重写 + 受影响文档 | mv 子树 | 文档不变 |
| 列文件夹树 | `GET /api/kb/{kb}/folders` | — | — | — |
| 删空文件夹 | `DELETE /api/kb/{kb}/folders/{id}` | delete 行（须空） | rmdir | — |

**关键**：所有移动/改名**只动位置字段 + 磁盘字节**，`document_id`/`document_key`/snapshot link/build 全部不动 → 挖掘历史与可检索性连续。下次挖掘按新 `storage_path` 找到同一个身份，挂新 snapshot。

**一致性**：DB 事务内改字段；磁盘 mv 在事务提交后执行；若 mv 失败需回滚 DB（或先 mv 成功再提交）。采用「先 DB 后磁盘，磁盘失败则补偿回滚 DB」。

---

## 4. 在线预览

**策略：复用 download（blob），前端按类型渲染；尽量零新端点。**

| 类型 | 渲染方式 | 依赖 |
|---|---|---|
| md | 取文本 → `marked` → `DOMPurify` | 已有 |
| html | 取文本 → `DOMPurify` | 已有 |
| txt/log/json/yaml/csv/代码 | 取文本 → `<pre>`（代码可走 codemirror 只读） | 已有 |
| png/jpg/gif/webp/svg | blob URL → `<img>` | 原生 |
| pdf | blob URL → `<iframe>`（浏览器内置 PDF.js） | 原生 |
| office(docx/xlsx/pptx) | **暂不支持**，提示下载（或后续加 mammoth.js 仅 docx） | — |
| zip/chm/hdx/其他二进制 | 不支持，提示下载 | — |

**预览交互**：文件表/网格点击文件名 → 右侧抽屉（`el-drawer`）或下方面板预览；未挖掘文档预览原始字节，已挖掘可加「查看挖掘产物」跳现有 `/knowledge/:docId`（复用，不在本规划新建）。

**大文件保护**：预览设大小阈值（如 50MB），超出提示下载，不强行加载 blob。

---

## 5. 前端：文件管理器 UI

替换现 `KbFileTable.vue` 为「左树 + 右列表 + 面包屑 + 预览」的文件管理器布局：

```
┌─────────────────────────────────────────────────────────────┐
│ [全部文件 ▾] [新建文件夹] [上传]                  [挖掘]      │
├──────────────┬──────────────────────────────────────────────┤
│ 文件夹树      │ 面包屑: 根 / 5G / AMF                          │
│ 📁 5G        │ ┌──────────────────────────────────────────┐ │
│  └ 📁 AMF    │ │ 名称        类型   状态    大小  上传  ⋯   │ │
│ 📁 资料       │ │ qos.pdf    PDF   已发布  2MB  07-28 预览/移│ │
│ (拖拽到此移动)│ │ amf.md     MD    已上传  8KB  07-28 预览/移│ │
│              │ └──────────────────────────────────────────┘ │
└──────────────┴──────────────────────────────────────────────┘
预览抽屉（点文件名）：按类型渲染
```

- **左树** `el-tree`：`draggable` + `allow-drop`（仅允许拖到文件夹节点）+ `allow-drag`；文件节点也可拖（从右侧列表拖到左侧文件夹）。
- **右列表**：当前文件夹内容（子文件夹 + 文档）；行操作：预览/下载/改名/移动/撤回(灰显)。
- **面包屑**：当前路径，可点击跳层。
- **上传**：默认进当前文件夹；`directory` 字段 = 当前文件夹 path。
- **新建文件夹**：在当前层建。
- **权限**：viewer 隐藏所有写操作（上传/新建/移动/改名/删除），后端 403 兜底。
- 组件拆分：`KbFileManager.vue`（容器）+ `KbFolderTree.vue`（左树）+ `KbFileGrid.vue`（右列表，由现 `KbFileTable` 演进）+ `KbFilePreview.vue`（预览抽屉）+ `KbFolderCreateDialog.vue`。

API 扩展（`api/kb.ts` `useKbApi`）：`listFolders`/`createFolder`/`renameFolder`/`moveFolder`/`deleteFolder`/`moveDocument`。

---

## 6. 开发阶段拆分

| 阶段 | 内容 | 依赖 | 可见里程碑 |
|---|---|---|---|
| **G1 身份修复** | storage_path 查身份 + register 写冻结键 + select_or_create 改 storage_path + 审计 mining_runtime 消费点 | — | 多库同 key 不串、移动后可正确再挖（后端测试见 §7）|
| **G2 文件夹表 + CRUD** | `kb_folders` DDL + 服务/端点（建/列/删/改名/移动）+ 磁盘镜像 + pg_schema 注册 | G1 | 能建空文件夹、列表文件夹树 |
| **G3 移动/改名** | 文件 move 端点 + 文件夹改名/移动（子树重写）+ DB/磁盘一致性 | G2 | 文件/文件夹改名、拖拽移动后端就绪 |
| **G4 在线预览** | 前端 `KbFilePreview`（按类型渲染，复用 download blob）+ 大文件保护 | — | 点文件名即预览（md/html/text/img/pdf）|
| **G5 文件管理器 UI** | `KbFileManager` 容器 + 左树 + 右列表 + 面包屑 + 拖拽 + 新建/上传到当前文件夹 | G2,G3,G4 | 完整云端文件系统体验 |
| **G6 测试 + 文档** | §7 全量测试 + 更新 `kb-management-design.md`/前端计划 | G1–G5 | 收口 |

每阶段独立可测。**G1+G2+G3 结束**=后端文件系统完整（建/移/改/删 + 不丢历史）；**G5 结束**=前端可视化文件管理器；**G4** 可与 G2/G3 并行。

---

## 7. 测试规划

### 7.1 后端单测（`knowledge_mining/tests/kb/`，pytest + kb_db_test）
- **身份修复（G1）**
  - 多库同 document_key：KB1、KB2 同域都放 `qos.pdf`，挖 KB2 → link/run_document 挂在 **KB2 的文档**（断言 document_id）。
  - 移动后 document_key 不变：上传→记录 key→移动→断言 asset_documents.document_key 未变。
  - 移动后再挖：旧 run 与新 run 的 `mining_run_documents.document_key` 一致（冻结键），`derive_document_status` 返回正确状态，不丢 failed。
  - storage_path 查不到（legacy null）行为符合 D2 决策。
- **文件夹（G2）**
  - 建空文件夹 → `kb_folders` 有行 + 磁盘有空目录。
  - 同父同名 → 409。
  - 删空文件夹 ok；删非空（有子文件夹或文档）→ 409。
  - 跨库文件夹名不冲突。
- **移动/改名（G3）**
  - 移动文件：`directory_path`/`storage_path` 更新、磁盘字节到新位置、`document_key`/`id` 不变、download 指向新路径。
  - 移动文件夹：子树内所有文档 `directory_path`/`storage_path` 重写、磁盘子树整体迁移。
  - DB/磁盘一致性：DB 提交后磁盘失败 → 补偿回滚（事务回退）。
  - viewer 移动/改名 → 403；不可见库 → 404。
- **预览（G4）**：download 端点对各类 mime 返回正确 Content-Type（预览主要在前端，后端仅 blob）。

### 7.2 集成场景（Alice/Bob 多人多库）
- Alice(KB1) 与 Bob(KB2) 同域同传 `qos.pdf`：2 文档身份、1 共享 snapshot、挖 KB2 廉价（仅 link）、各自 owner_id 追溯正确、撤回 Bob 的不影响 Alice 的 snapshot。
- 跨库同 key 不串（G1 修复后）：Bob 挖 KB2 的 `qos.pdf` 挂到 Bob 文档，不挂 Alice。
- 文件夹移动后再挖：状态连续、检索可见性连续。

### 7.3 前端单测（vitest）
- `KbFolderTree`：树渲染、拖拽 allow-drop 规则（仅文件夹可作目标）、空文件夹显示。
- `KbFileGrid`：当前文件夹内容、面包屑跳层、行操作按角色显隐。
- `KbFilePreview`：按扩展名分流（md→marked、pdf→iframe、img→img、office→不支持提示）、大文件拦截。
- `useKbApi` 新方法契约（mock axios）。

### 7.4 构建/E2E
- `npm run build`（vue-tsc + vite）通过；`vitest` 全绿。
- `pytest knowledge_mining/tests/kb/` 全绿（含 `KB_ALLOW_TEST_TRUNCATE=1`）。
- 手工 E2E：起 main_control + mining(kb_db) + kb-ui → 建库→建文件夹→上传到文件夹→拖拽移动→预览各类型→挖掘→移动后状态连续→权限（切默认用户验证 viewer）。

---

## 8. 决策点（需你拍板，定后开工）

- **D1 文件夹建模**：采用 `kb_folders` 一等表（推荐，支持空文件夹/元数据/子树移动）。确认？（这会推翻前端计划 v0.1 的 B 档「无新表」。）
- **D2 legacy `/api/runs`**：storage_path 查不到时**不兜底**（首次重挖当 NEW，推荐），还是保留 (domain, document_key) 双查兜底？
- **D3 预览范围**：md/html/text/image/pdf 即可（推荐，零新依赖）；office(docx) 是否加 `mammoth.js`？还是一律「下载查看」？
- **D4 撤回 UI 语义**：是否在撤回入口对用户明示「撤回=不再发布，内容因被他人共享不会物理删除」？（后端撤回实现仍待 release 机制接线，本规划 UI 先占位灰显。）
- **D5 阶段顺序**：按 G1→G2→G3→G4→G5→G6 串行（推荐，G1 是根基），还是 G4 与 G2/G3 并行？

---

## 9. 不在本规划（明确排除）
- 按 KB 组合检索（serving 侧 P5，待 serving per-domain 库错位问题先解）。
- 后端文档软撤回的 release 机制接线（设计 §10，独立工作项；UI 占位）。
- 文件版本历史（多版本快照已在 snapshot 层支持，UI 展示多版本不在本期）。
- 真实登录（Phase 2，X-KB-User 仍写死默认用户）。
