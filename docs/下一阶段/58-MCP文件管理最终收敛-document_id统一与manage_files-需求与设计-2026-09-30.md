# 58号：MCP 文件管理最终收敛——document_id 统一与 manage_files（需求与设计）

- 日期：2026-09-30
- 分支：worktree-kb-agent-upgrade（在 57 号 9 提交之上继续，未合并未部署）
- 状态：**已实施完毕（批次1-5；2026-10-01）**——批次1 document_id 统一输出 / 批次2
  票据绑定目录+ZIP 落位 bug 修复 / 批次3 manage_files 改名+双 action+权限迁移
  （含 006 迁移：operation 扩 replace + instructions 清空）/ 批次4 browse_directory /
  批次5 文档（README/检索使用指南/57号补记）
- 来源：用户 2026-09-30 需求全文（14 条）+ 五话题逐条对话确认
- 定位：57 号实施完成但与最终产品意图未对齐（替换走 doc_ 翻译间接层）；本批按本文把 MCP
  文件管理收敛到最终形态。**不另起炉灶，在 57 号已有框架上演进。**

---

## 0. 核心不变量（全批约束，任何实现不得违反）

1. **不创建 file_ref / replace_ref / 任何新的文件 ID 系统。**
2. **MCP 文件管理统一使用现有数据库 `asset_documents.id`，对外字段名统一为 `document_id`**
   （它就是 asset_documents.id，不是新 ID）。
3. `doc_` / `ev_` / `st_` 继续存在，但降级为**挖掘内容引用**，不再承担文件管理身份。
4. **MCP 保持三个公开工具**（不拆第四个）：
   `search_knowledge`、`get_knowledge`、`manage_files`。
5. `manage_files` 只提供**上传和替换**，不提供删除、移动、重命名。
6. 架构原则（沿用 2026-09-30 拍板）：**服务层只实现一遍，三个入口（网页/API/MCP）只是薄适配**；
   mcp_server 禁止出现业务逻辑（只允许鉴权翻译、形状翻译、错误翻译）。

---

## 1. 文件身份三分法（D1，全批的根基）

| 名字 | 是什么 | 生命周期 | 用途 |
|---|---|---|---|
| `document_id` | `asset_documents.id`（数据库身份证号） | 上传即有，永不换 | **唯一的文件管理身份**：manage_files 替换就认它 |
| `document_ref`（`doc_`） | 某次挖掘快照的内容引用（快照哈希生成，`EvidenceRefCodec.encodeDocument`） | 挖掘后才有，**重挖即变（可能过期）** | 只用来读整篇内容（get_knowledge） |
| `content_revision` | 文件内容版本号（每次替换 +1） | 随替换递增 | 替换时的 CAS"暗号"，防并发互相覆盖；**不是 ID** |

**两条闭环路径，必须使用完全相同的 document_id：**

```
路径 A（浏览→替换）：get_knowledge 浏览 → 条目 document_id + content_revision
                    → manage_files(action="replace")
路径 B（搜索→替换）：search_knowledge → evidence[].source.document_id
                    → manage_files(action="replace")
```

- 不得要求 Agent 先取得 doc_ 才能替换文件。
- 不得要求文件先挖掘成功。
- 不得通过 serving get_document 把 doc_ 反查成 document_id（57 号的翻译跳在本批拆除）。

**统一 document_id 输出面（全部返回同一个 asset_documents.id）：**

1. get_knowledge 的 `documents` 视图（文件清单）
2. get_knowledge 的 `file_results` 视图（跨库文件搜索）
3. get_knowledge 的 `document_content` 视图（doc_ 整篇阅读的 source）
4. search_knowledge 的 `evidence[].source`

- 当前清单字段叫 `id` → **统一改名 `document_id`**。本分支未正式发布 → **直接删除 `id`，不留别名**。
- `document_id` 与 `document_ref` 不是替代关系：一个管"文件是谁"，一个管"这次挖的内容在哪"。
- **外部引用（referenced=true）文档：`document_id` 返回 null + referenced 标记，明确不可通过
  manage_files 替换**（它在属主库有 id，但在本库清单语境下给 null 语义最干净——给真 id 只会让
  Agent 拿去吃 404）；manage_files 不得接受 referenced 文档作为替换目标（跨库归属 404 天然拦截）。

---

## 2. manage_files 工具（D2）

### 2.1 签名

```python
manage_files(
    action: Literal["upload", "replace"],
    kb_name: str,
    filenames: list[str],
    directory_path: str | None = None,
    document_id: str | None = None,
    expected_revision: int | None = None,
) -> dict
```

### 2.2 规则矩阵

| | action="upload"（新增文件） | action="replace"（替换任意本地文件） |
|---|---|---|
| filenames | 1~100 个（保持现状上限） | **恰好 1 个** |
| directory_path | 可选：不传/空 = 根目录；非空 = 必须已存在（**不自动创建**） | **禁止传入** |
| document_id | **禁止传入** | **必填**（get_knowledge / search_knowledge 返回的那个） |
| expected_revision | 禁止传入 | **必填**（你看到的 content_revision） |

### 2.3 action="upload" 要求

- 返回每个文件的一次性 PUT URL（票据模型不变：TTL 10 分钟、单次、绑定 库/用户/文件名/目录）。
- PUT 上传**原始字节**，不使用 base64。
- 普通文件大小限制保持现状（MCP 50MB）；ZIP/HDX/CHM 归档限制保持现状（500MB）。
- 上传成功后自动排队挖掘；大归档先解压完成再排队挖掘（57 号 deferred 语义不变）。

### 2.4 action="replace" 要求

- 替换**任何本地文件**，五种状态全部支持：`uploaded / mining / mined / failed / update_failed`
  （不依赖 doc_、活动快照或挖掘成功——与网页替换同权）。
- 替换保持原文档**名称、目录、文件身份、权限关系**不变。
- 新旧扩展名必须一致；PDF/DOCX/XLSX/PPTX 等真实性检查（magic bytes 开箱检查）保留。
- 错误矩阵：版本不一致 409；不存在/已软删 404；document_id 属于其他知识库 404（防探测）；
  无写权限 403；referenced 外部文档按跨库归属 404。
- 替换后自动排队挖掘；新版本挖掘完成前旧知识继续可检索（knowledge_outdated 保底）。
- `expected_revision` **必填**（57 号的"可省略自动取当前值"取消——乐观锁显式化；409 时重新
  get_knowledge 取新 content_revision 再试）。

### 2.5 指定目录上传（directory_path 贯穿两步流程）

```
manage_files → begin-upload（校验并绑定目录进票据）→ PUT /upload/{ticket}
            → upload-direct（只用票据绑定目录）→ intake_upload → DocumentService
```

- directory_path 在**签发票据时**校验并写入票据；PUT 请求没有参数可指定目录（presigned 模型
  天然防篡改）；消费票据时只使用票据绑定的目录。
- 复用 DocumentService 现有路径规范化和 traversal 防护（`build_storage_path` / `_normalize_directory`
  / `assert_directory_exists`）。
- 指定目录必须属于目标知识库且**已经存在**：不存在返回 **422**（明确文案指引 Agent 先浏览目录
  确认写法），**不自动创建**，防拼写错误制造幽灵目录。
- 拒绝绝对路径、反斜杠 `\`、`.`、`..`、空路径段、超长路径；根目录统一为空字符串 `""`。
- 普通文件准确落在 directory_path。
- **ZIP 上传到指定目录时，最终落位为 `{directory_path}/{压缩包名}/...`；根目录 ZIP 仍落在
  `{压缩包名}/...`**（与网页端一致）。
- ZIP 解压产生的**内部**子目录仍自动建文件夹（解压产物等同用户建目录，与现状一致）。

### 2.6 顺带修复（盘点新发现的现存 bug）

`document_service.intake_upload` 的归档分支调用 `upload_archive_path` 时**没有透传
directory_path**（该函数也无此参数）→ 网页端在子文件夹上传 ZIP 时，包内容落在**库根目录**的
"压缩包名/"下，与用户眼前文件夹无关。本批在底座修复（修一处，网页+MCP 同时受益）。

---

## 3. get_knowledge 目录逐层浏览（D3）

### 3.1 新参数

```python
browse_directory: str | None = None
```

- 未传（None）→ 保持当前 documents/file_results 行为，零变化。
- 传 `""` → 浏览知识库根目录。
- 传 `"产品文档/手册"` → 浏览该目录的**直属内容**。

### 3.2 返回契约（新视图 view="directory"）

```json
{
  "view": "directory",
  "kb": "设备库",
  "current_directory": "产品文档/手册",
  "parent_directory": "产品文档",
  "child_directories": [
    {"name": "交换机", "path": "产品文档/手册/交换机"}
  ],
  "documents": [
    {"document_id": "...", "name": "设备手册.pdf", "directory_path": "产品文档/手册",
     "status": "mined", "content_revision": 4, "file_size": 12345, "modified_at": "..."}
  ]
}
```

### 3.3 规则

- `child_directories` 只返回当前目录的**直属子目录**（来自 kb_folders 表，不递归铺平）→
  **空目录可见**。
- `documents` 只返回当前目录**直属文件**（`directory` 精确匹配过滤，不含子目录文件）。
- 根目录统一用空字符串表示；返回目录 `path`，**不创建新的 folder_ref**。
- `directory_prefix` 语义不变：仍是"指定目录及全部子目录的**递归搜索过滤**"。
  **browse_directory（逐层看目录，像 ls）与 directory_prefix（搜索时限定范围，像"在此文件夹
  及子文件夹内搜"）是两种不同模式，工具描述必须写清分工。**
- 浏览视图**不含 referenced 外部引用文档**（其目录属属主库，会污染本库目录树；它们继续留在
  平铺清单带 referenced=true，与现状一致）。
- 目录不存在 / 非法路径 → 明确错误（目录是请求参数 → 422 口径，文案指引先浏览确认）。
- 浏览必须与 ref / file_query / directory_prefix / status 互斥（形状校验，显式报错）。
- 复用现有 FolderService / KbDB（`list_folders` + `list_documents_in_kb(directory=…)`），
  **不在 mcp_server 重写目录业务**。

---

## 4. search_knowledge 配套（D4）与两条闭环

### 4.1 evidence[].source 契约字段

| 字段 | 用途 |
|---|---|
| `document_id` | manage_files 文件管理（路径 B 替换入口） |
| `document_ref`（doc_） | get_knowledge 阅读整篇内容 |
| `file_name` / `relative_path` / `knowledge_base` | 来源基本信息（已有） |
| `content_revision` | 版本暗号（57 号已埋，本批成为契约） |

- 两者不是替代关系；**不得为了隐藏 document_id 又引入新的 file_ref**。
- 命中后 Agent 直接用 `source.document_id` 调 manage_files，不需要再做一次文件名搜索。
- 现状核实：六个字段在 serving 投影（EvidenceSourceV2Mapper）已就位（57 号埋线），MCP 层
  原样透传零删改——本批只做契约钉死 + 消费端接线，**Java 零代码改动**。

### 4.2 get_knowledge 配套

- 文件清单条目字段：`document_id / name / directory_path / status / content_revision /
  file_size / modified_at / referenced / kb（跨库时）`。
- `get_knowledge(ref=doc_)` 的 document_content.source **保留同一个 document_id**
  （删除 57 号加的 `source.pop("document_id")`）。
- 文件清单、整篇文档视图、内容搜索结果**必须使用同一个 asset_documents.id**。

### 4.3 稳定性契约（测试钉死）

- 同一文件重新挖掘后 `doc_` 可以变化，但 `document_id` 必须保持不变。
- 未挖掘/失败文件没有 doc_，也能按 document_id 替换（路径 A）。
- 路径 B 天然只命中已挖掘文件（未挖内容不进检索索引）；两条路径互补覆盖全部五种状态。

---

## 5. 工具改名与权限迁移（D5 前半）

### 5.1 迁移规则

旧工具名 `upload_document` → 新名 `manage_files`：

1. **读取旧 open_tools 时**，将 upload_document 规范化为 manage_files（读时归一，
   `normalize_legacy_open_tools` + `_RENAMED_TOOLS` 加一行；验钥端点 auth.py:281 的归一
   机制现成，老钥匙零迁移无感可用）。
2. **新写入只保存 manage_files**（update_config 按 MCP_TOOL_NAMES 白名单校验，旧名自然被拒）。
3. **tools/list 只展示 manage_files**（代码只注册一个函数，天然不会双名并出）。
4. **不扩权论证**：旧 upload_document 本来已含上传+替换（57 号加的），改名不引入新权限。
5. 不增加第四个工具。

### 5.2 改名牵动面（代码级清单）

| 面 | 文件 | 动作 |
|---|---|---|
| 工具注册名 | `mcp_server/server.py`（@mcp.tool 函数） | 重命名 + 重写描述 |
| 工具族常量 | `mcp_server/identity.py` TOOL_NAMES | upload_document → manage_files |
| 钥匙白名单基线 | `knowledge_mining/mining/kb/services/mcp_key_service.py`（MCP_TOOL_NAMES + _RENAMED_TOOLS） | 换名 + 加迁移映射 |
| 迁移桥副本 | `knowledge_mining/mining/maintenance/database_upgrade/bridge_51.py`（_CURRENT_MCP_TOOLS + _LEGACY_TOOL_RENAMES 副本） | 同步换名 + 加映射（否则重放会剥掉新名权限） |
| 调用账本 | `mcp_server/server.py` 中间件 `name == "upload_document"`、mining `_complete_upload_access_record` 的 tool_name/operation | tool_name=manage_files；operation 按 upload/replace 分记 |
| 管理页标签 | `kb-ui/src/components/mcp/McpKeyConfigPanel.vue`（+ 测试） | 名称与描述更新 |
| 测试断言 | `mcp_server/tests/test_tool_surface.py` 等 | 三件套断言更新 |
| 文档 | mcp_server/README.md、57 号补记、工具接入示例 | 更新 |

账本历史记录保留旧名（历史事实，不回写）。

---

## 6. 提示词与工具描述（D5 后半）

### 6.1 DEFAULT_INSTRUCTIONS 重写要点

- 三件套新名与新能力一句话概览（manage_files 上传/替换；get_knowledge 浏览/读取）。
- 工作流：浏览（get_knowledge browse_directory）→ 上传/替换（manage_files）→ 等挖掘 →
  检索（search_knowledge）。
- 教 Agent 区分 source 里 document_id（管文件）与 document_ref（管读内容）。

### 6.2 manage_files 工具描述（硬性要求）

第一句话必须明确：

> "管理知识库文件：上传新文件到指定目录，或替换任意已有文件。当前不提供删除、移动或重命名。"

模式表 + 明确声明（不删除/不移动/不重命名；directory_path 只用于 upload；document_id 与
expected_revision 只用于 replace；不能替换 referenced=true 的外部引用文档；PUT 用原始字节
不是 base64；版本冲突 409 时重新 get_knowledge 取最新 content_revision）。

至少三个示例：①浏览根目录 `get_knowledge(kb_name="设备库", browse_directory="")`；
②上传到指定目录（action="upload" + directory_path）；③替换任意文件（先取
document_id+content_revision，再 action="replace"）。

### 6.3 老用户自定义提示词处置（用户拍板：直接丢弃，以发布的默认为准）

- **自定义工具描述（软失效，自动）**：按工具名查找，旧描述挂在 upload_document 名下，新名
  查不到 → 自动落回官方默认。无需动数据。
- **自定义 instructions（硬丢弃，一次性）**：数据迁移 `UPDATE … SET instructions = NULL`
  （core 迁移，列名以实际 schema 为准），所有钥匙回到官方默认提示词。

---

## 7. 权限与安全清单（每项操作必须重新检查，不依赖 id 难以猜测）

| 检查项 | 落点（现状/本批） |
|---|---|
| MCP key 有效且未吊销 | 票据消费时 `get_mcp_key` + status=active（已有，upload-direct） |
| manage_files 在该 key 工具白名单 | 中间件 tool_enabled（已有，随改名换名） |
| key 绑定知识域正确 | 单域钥匙 + validate_domain（已有） |
| 目标 KB 在 key 当前开放范围 | begin-upload 与票据消费时双重 `key_open_kb_ids` 复核（57 号已做，fail-closed） |
| 用户仍对 KB 可见 | `is_visible` 404 防探测（已有） |
| 用户仍有 owner/editor 写权限 | `can_write` 403（已有） |
| document_id 属于目标 KB | begin-upload `get_document_identity` + kb_id 比对 404（57 号已做，本批直连） |
| document 未软删 | get_document_identity 默认过滤软删（已有） |
| expected_revision 一致 | 签票时 CAS 校验 409 + replace_content 落库时二次 CAS（已有） |
| 票据消费时再复核 key/开放集/写权限 | upload-direct 消费链（已有）；本批新增目录归属随票据 |
| CAS 防签票后并发替换/删除 | 票据 TTL 10 分钟窗口内由 replace_content 的 CAS 拦截（409/404） |

---

## 8. 明确不做（本批禁止引入）

删除文件、删除文件夹、移动文件、重命名文件、创建文件夹、批量替换、历史版本浏览、历史版本
回滚、新的文件 ID/ref 系统。（删除语义将来若开放，按 57 号 D6 走"默认关闭的单独开关"。）

---

## 9. 测试计划（**先补测试，再实现**）

| 测试域 | 用例 | 落点文件 |
|---|---|---|
| 工具面 | tools/list 恰好三件套（含 manage_files）；不公开 upload_document/replace_document；描述覆盖上传/替换/"不提供删除" | mcp_server/tests/test_tool_surface.py |
| 权限迁移 | 旧 key 的 upload_document 归一为 manage_files；新写拒绝旧名 | knowledge_mining/tests/test_mcp_open_tools_migration.py |
| 同一 id | 三视图（清单/file_results/document_content）与 evidence source 的 document_id 相同；重挖后 doc_ 变、document_id 不变；referenced→null | mcp_server/tests + mining 契约测试 |
| 目录浏览 | 根目录/子目录直属内容；空目录可见；不递归混入孙级；非法路径与不存在目录拒绝；directory_prefix 递归语义回归 | 新 test_mcp_tools_browse.py |
| 目录上传 | 不传=根目录；已存在目录正确落位；ZIP 落位 {dir}/{包名}/；不存在目录 422；非法路径拒绝；票据绑定目录 PUT 无法篡改；权限撤销后旧票据不可用 | test_mcp_tools_replace_upload.py 扩展 + document_service 归档目录测试 |
| 任意替换 | 五种状态逐一替换；document_id 不存在/跨库/referenced/无权限/版本冲突/签票后并发替换/签票后删除/扩展名不符/假 PDF/假 OOXML/保留原名目录/replace 禁 directory_path/upload 禁 id 参数/不依赖 serving get_document | test_mcp_tools_replace_upload.py 扩展 |
| 闭环 | 路径 A/B 端到端同一 id；未挖掘文件无 doc_ 也能替换 | mcp_server/tests 集成型用例（假后端） |
| 归档 bug 回归 | 网页 ZIP 指定目录落位（服务层直测） | knowledge_mining/tests/kb |
| Java 契约 | evidence source 六字段（含 document_id/content_revision）投影钉死 | EvidenceSourceV2MapperXmlTest / EvidenceToolServiceDocumentPageTest（已存在，补充断言） |

---

## 10. 验证门禁

```
python -m pytest mcp_server/tests -q
python -m pytest knowledge_mining/tests/kb -q（相关）
mvn test
npm test && npm run build
git diff --check
真实 PG 集成测试：有环境时必跑（合并前强制门禁——57 号"单箭头"教训）
```

## 11. 文档更新清单

MCP README（三件套新名）；57 号文档补记（本批推翻的三个决定：doc_ 替换入口/剥 document_id/
expected_revision 可省略）；工具接入示例；MCP key 工具权限说明；document_id / document_ref /
content_revision 三分法说明；manage_files 两种 action；目录浏览与 directory_prefix 分工；
明确 Agent 仍然没有删除能力。

## 12. 风险与遗留

- **硬编码调用方风险**：内网若有写死 `upload_document` 工具名的脚本/集成（不走 tools/list
  动态发现），改名后会断——发版通知必须提示（用户已确认开工，此项随发版文档提示）。
- instructions 清空迁移在发版时执行（数据 only，无表结构变更）。
- 账本历史记录保留旧工具名（历史事实，不回写）。
- 本批完成后：57 号遗留（跨库深分页 200 截断等）不变，仍记录在 57 号。

## 13. 最终交付报告必须回答（验收口径）

1. 最终三件套工具名称；2. 三工具如何通过 document_id 串起来；3. 没有新增文件 ID；
4. upload_document 旧权限如何迁移；5. 指定目录上传与 ZIP 落位规则；6. 五种文件状态的替换测试；
7. 目录逐层浏览测试；8. 全部测试与构建结果；9. 未执行的真 PostgreSQL/E2E 测试。

---

## 附：实施顺序（TDD）

1. 迁移与命名（mcp_key_service / bridge_51 / identity / 工具重命名骨架 + 测试）
2. document_id 统一输出（mining 清单改名 + referenced null + 去 pop + 契约测试）
3. 指定目录上传（票据字段 + begin-upload 校验 + 归档落位 bug 修复 + 测试）
4. manage_files 双 action（dispatch + 参数矩阵 + 描述 + 测试）
5. browse_directory（mining 端点 + MCP 参数 + 测试）
6. 提示词重写 + instructions 清空迁移 + kb-ui 标签 + 文档
7. 全量门禁 + 提交
