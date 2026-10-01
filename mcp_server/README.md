# Cloud Core Knowledge MCP Server

云核心网知识证据底座 MCP Server。

## 设计原则

- **不做语义判断**：检索结果原样交给 Agent，Server 不评估证据是否充分、不改写内容
- **instructions 承载精华**：SKILL 中的使用指南、证据理解规则、回答行为、推理护栏全部内嵌在 MCP instructions 中，Agent 拿到即可正确使用
- **Agent 自主判断**：证据是否充分、如何回答，完全由 Agent（LLM）决定，Server 不做评估
- **三件套 tool**（2026-08-31 两轮收敛 9→3；58号 `upload_document` → `manage_files` 改名）：`search_knowledge`（搜内容）、`get_knowledge`（一切读取行为：层级浏览/目录逐层浏览/证据原文/整篇文档/能力报告/结构导航/表格查询/文件清单与搜索）、`manage_files`（文件管理：上传到目录/替换任意本地文件；**不提供删除、移动、重命名**）。无 resource、无 prompt

> ⚠️ **不再是「纯透传」**。为了让「发布检索范式后 MCP 自动用上」成立，client 现在做两件加工：按 domain 解析该用哪条检索引擎，以及把两条引擎不同的响应外壳归一化。详见下节。

## 检索范式的选择

`search_knowledge` 有两条选法，取决于调用方传没传 `paradigm`：

```
传了 paradigm（name 或 id）
  GET  /api/v1/paradigm/mcp-catalog        ← 解析成 id（30s 缓存）
  POST /api/v1/paradigm/{id}/search        ← 不再走 resolve：工具本身就是那次选择

没传
  GET  /api/v1/paradigm/resolve?domain={domain}
    ├─ bound=true  → POST /api/v1/paradigm/{id}/search    （该域绑定的检索范式）
    └─ bound=false → POST /api/v1/search                  （默认检索管线）
```

`resolve` **不做缓存**——它是控制库的单行索引查询、容器内走 localhost，相对 120s 的检索预算可忽略，换来的是「发布并绑定后，下一次调用立即生效」。catalog 则**有 30s 缓存**，因为它只是挂在响应上的提示；但**显式选择那条路不吃陈旧**：找不到时会强制刷新一次再判定，所以「发布完立刻按名字调用」照样成立。

**resolve 失败会回落到默认管线**并记 warning，不让配置面的故障拖垮检索面。但**范式执行失败不会回落**——那会让一条坏掉的绑定范式被无限期掩盖，而且用的是运维方没有配置的引擎。**显式选择失败同样不回落**，四种情况各有独立错误码：

| 错误码 | 含义 |
|---|---|
| `unknown_paradigm` | 该域下没有这个范式。消息里列出可用项供 Agent 自我纠正。存在但未被提供（未发布 / 非 assemble 终点 / 引用了匿名不可读的知识库）与不存在共用此码——catalog 对匿名调用方本就不区分 |
| `paradigm_domain_mismatch` | 范式存在但属于别的知识域。消息里写清两个域名 |
| `catalog_unavailable` | 清单暂时拉不到，无法把名字解析成 id。传范式 id（`pd-` 开头）则不受影响，直接透传 |

响应外壳统一成 `/api/v1/search` 的形状（范式那条路会把 `contextPack` 拆平、`evidenceGroups` 改成 `evidence_groups`），Agent 无法从结构上区分两条引擎。真正说明来源的是 `_retrieval`：

```json
"_retrieval": {
  "engine": "paradigm", "paradigm_id": "pd-abc", "name": "odn-production", "version": 3,
  "selected_by": "explicit",
  "available_paradigms": [{"name": "ODN 拓扑排障", "description": "查 ODN 拓扑与端口占用"}]
}
"_retrieval": {"engine": "legacy", "selected_by": "fallback"}
```

`selected_by` ∈ `explicit` / `domain_default` / `fallback` / `rejected`。

### 发现是副产品，不是一个工具

`available_paradigms`（name + 一句话描述，按本次 `domain` 过滤）挂在**每一条**返回路径上，**包括出错的**。所以 Agent 不需要先调一个"列出范式"的工具：直接检索一次，结果顺带告诉它还有什么，不满意时第二次指名重试。

错误路径也挂是必需的：纯 KB 部署的域没有 active release（KB 挖掘 `publish=false`），不指名范式的第一次调用必然 `no_active_release` 失败——只挂成功路径的话，Agent 撞墙后没有任何线索。

拉不到清单时该字段**缺席**而不是空数组：空会被读成"这里没有别的范式"，那是一句它没资格说的话。

> 为什么不给每个范式生成一个独立 tool：MCP 工具名必须是 ASCII 标识符（`^[a-zA-Z0-9_-]{1,64}$`），而范式名是中文自由文本、id 是 `pd-`+uuid8。自动生成的名字要么丢掉全部语义，要么得让人填一个英文名——后者只是把手工从代码挪到表单。把范式名当**数据**传就没有这个约束。

## 原文下钻：`get_evidence`（批次8，取代已退役的 get_segment_fulltext）

`search_knowledge` 返回的 `content` 有固定 token 预算，命中项可能被截断（`truncated=true`）。要引用条款/参数/步骤的准确原文，把该条证据的 `ref`（`ev_` 开头的 opaque ref）原样传回 `get_evidence` 即可，可选 `mode: auto|exact|window|parent|whole_document` 控制展开粒度（默认 auto=预算内就大）。

- **不需要内部 id/type**：ref 是不透明公开身份，服务端自行授权解析；
- ref 失效（库重新挖掘/不可见）返回稳定的 typed error（`expired_ref` / `out_of_scope`），Agent 据此重新检索拿新 ref；
- 结构化探索配合 `inspect_knowledge`（能力披露）→ `navigate_structure`（关系导航）→ `query_structured_asset`（表格 schema 查询与聚合）使用。

### 原件不作为 tool 暴露

`GET /api/v1/documents/{id}/raw` 能直接取回上传的原始文件，但**不包装成 tool**：把一份 200 页 PDF 灌进 Agent 上下文代价极大，且答不出片段原文答不了的东西。改为在配置了 `MCP_RAW_FILE_BASE_URL` 时，给 `hasRawFile=true` 的片段附上 `rawFileUrl`，供人或前端点开。

默认**不生成**这个链接：`SERVING_URL` 在容器里是 localhost，据此拼出来的地址在调用方那边根本打不开——给一个看起来该能用、实际打不开的链接，比不给更糟。

### ⚠️ `scope` / `entities` 两个 tool 参数当前不生效

后端检索管线**从不消费**它们：`SearchService` 只读 `query`/`domain`/`channel`/`debug`/`kbIds`，查询理解只拿到 query 字符串（检索器里的 `query.scope()`/`query.entities()` 属于后端自行抽取的 `QueryUnderstanding`，与请求体同名但无关）。参数予以保留以免破坏既有调用，但一旦传入非空值，会在 `_retrieval.ignored_args` 里回报并记 warning。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SERVING_URL` | `http://121.89.90.178:8081` | 后端地址。容器内由 supervisord 覆盖成 `http://localhost:8081` |
| `MCP_PARADIGM_ROUTING` | `1` | 置 `0`/`false`/`off` 一键回到纯透传（与挖掘侧 `MINING_RUN_SUBMISSION_ENGINE=legacy` 同构） |
| `RESOLVE_TIMEOUT` | `5.0` | 范式解析超时（秒）。刻意短：它绝不该成为检索超时的原因 |
| `SEARCH_TIMEOUT` | `120.0` | 检索超时（秒） |
| `FULLTEXT_TIMEOUT` | `30.0` | 原文下钻超时（秒）。按 id 直查，比检索快得多 |
| `MCP_CATALOG_TTL` | `30.0` | 范式清单缓存时长（秒）。只影响提示的新鲜度；显式指名找不到时会强制刷新，不受此值影响 |
| `MCP_CATALOG_TIMEOUT` | `5.0` | 拉清单超时（秒）。与 `RESOLVE_TIMEOUT` 同理，绝不该成为检索变慢的原因 |
| `MCP_RAW_FILE_BASE_URL` | 空 | 调用方**能访问到**的 serving 地址。设了才在结果里附原件下载链接；默认不附，理由见上 |
| `HEALTH_TIMEOUT` | `10.0` | 健康检查超时（秒） |

第二条回退路径不需要重启任何服务：取消该域的 `is_default` 绑定，下一次调用解析不到范式，自动回落。

## 文件结构

```
mcp_server/
├── __init__.py      # 版本号
├── __main__.py      # 入口，支持 stdio / streamable-http / sse
├── server.py        # FastMCP 定义：instructions + 对外 9 个 tool（批次8 九件套）
├── client.py        # HTTP 客户端：范式路由 + 回落 + 响应归一化
├── schemas.py       # Pydantic 模型（HealthResult, SearchInput, EntityRef, FullTextInput, SegmentRef）
├── tests/           # pytest：路由/回落/归一化/范式选择/原文配对（不需要后端、不需要 mcp 包）
└── README.md        # 本文件
```

## 前提

- Python 3.10+
- 后端正在运行（`SERVING_URL`，见上表）
- 依赖：`pip install "mcp>=1.0,<2.0" httpx`
  > ⚠️ `pyproject.toml` 与 `docker/Dockerfile` 装的是 `fastmcp>=2.0`（独立包），而 `server.py` import 的是 `mcp.server.fastmcp`（官方 SDK 内置的 FastMCP 1.x）。现在能跑是因为 fastmcp 依赖 mcp、恰好 import 得到。三处应统一，尚未处理。

## 测试

```bash
python -m pytest mcp_server/tests/ -q     # 全部用 httpx.MockTransport，不连后端
```

## 两种运行模式

### 模式 1：stdio（Claude Code 本地集成）

```bash
python -m mcp_server
# 等同于
python -m mcp_server --transport stdio
```

Claude Code 通过 `.mcp.json` 自动启动，无需手动运行。

### 模式 2：HTTP（内网暴露，供其他 Agent / Postman 使用）

```bash
python -m mcp_server --transport streamable-http --port 9000
```

输出：
```
MCP Server starting on http://0.0.0.0:9000 (transport=streamable-http)
```

此时内网其他机器可通过 `http://<你的IP>:9000/mcp` 接入。

也可以用环境变量：

```bash
MCP_TRANSPORT=streamable-http MCP_PORT=9000 python -m mcp_server
```

## 给其他人的接入信息

你只需要告诉对方以下信息：

```
MCP Server 地址：http://<你的内网IP>:9000/mcp
Transport 类型：streamable-http
协议版本：2024-11-05
```

对方 Agent 的 MCP Client 配置示例（以 Claude Desktop 为例，编辑 `claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "cloud-core-knowledge": {
      "url": "http://192.168.x.x:9000/mcp",
      "transport": "streamable-http"
    }
  }
}
```

如果对方的 MCP Client 是代码形式（mcp SDK）：

```python
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://192.168.x.x:9000/mcp") as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(
            "search_knowledge",
            {"query": "什么是业务感知", "domain": "cloud_core_network"},
        )
```

## Postman 测试

MCP 协议是 JSON-RPC over HTTP，每个请求是 `POST /mcp`。

### 关键约束

1. **Headers 必须包含**：
   - `Content-Type: application/json`
   - `Accept: application/json, text/event-stream`

2. **Session 机制**：第一次 `initialize` 返回的响应头中有 `Mcp-Session-Id`，后续所有请求必须带上这个 Header。

### 步骤 1：初始化会话

```
POST http://127.0.0.1:9000/mcp
Content-Type: application/json
Accept: application/json, text/event-stream
```

Body (raw JSON):
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {
      "name": "postman-test",
      "version": "1.0"
    }
  }
}
```

响应头中找到 `Mcp-Session-Id`，复制其值（如 `257fd3e5f2ad45c7ab715d4d3c3246d8`）。

响应体（SSE 格式，`data:` 行后面是 JSON）：
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": { ... },
    "serverInfo": {
      "name": "cloud-core-knowledge",
      "version": "0.1.0"
    },
    "instructions": "你是云核心网知识证据底座。..."
  }
}
```

### 步骤 2：查看可用工具

```
POST http://127.0.0.1:9000/mcp
Content-Type: application/json
Accept: application/json, text/event-stream
Mcp-Session-Id: <步骤1拿到的Session-ID>
```

Body:
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list",
  "params": {}
}
```

### 步骤 3：调用 search_knowledge

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "search_knowledge",
    "arguments": {
      "query": "什么是业务感知",
      "domain": "cloud_core_network"
    }
  }
}
```

指定范式时多传一个 `paradigm`（取值来自上一次结果的 `_retrieval.available_paradigms`）：

```json
"arguments": {
  "query": "什么是业务感知",
  "domain": "cloud_core_network",
  "paradigm": "ODN 拓扑排障"
}
```

返回后端原始 JSON，包含 `items`（证据列表）、`relations`、`sources` 等字段。每条 item 的 `evidence_role` 标注了该证据的角色（`direct_answer` / `support` / `contrast` / `background` / `missing`），Agent 据此自行判断证据是否充分。另有 `_retrieval` 说明本次由哪条引擎作答、还有哪些范式可选。

## 可用工具（批次8 九件套）

| 工具 | 用途 |
|------|------|
| `search_knowledge` | 搜内容：检索知识库，返回纯 EvidenceResponse（query/evidence/has_more）；evidence[].source 同时带 document_id 与 document_ref |
| `get_knowledge` | 一切读取行为：域→库顶层浏览（kb_tree）、目录逐层浏览（browse_directory，view=directory）、文件清单/文件名搜索（documents/file_results）、证据原文（ev_）、整篇文档（doc_）、能力报告与结构导航与表格查询（st_） |
| `manage_files` | 管理文件：`action="upload"` 上传新文件（可指定已存在目录 directory_path，ZIP 落位 {目录}/{包名}/…）或 `action="replace"` 按 document_id+expected_revision 替换任意本地文件（五种状态均可，不要求已挖掘）；两步直传 + 自动排队挖掘；**不提供删除、移动、重命名** |

**文件身份三分法（58号，Agent 侧约定）**：

- `document_id` = `asset_documents.id`：文件管理身份，上传即有、永不换。文件清单、目录浏览、整篇阅读、search 证据 source 全部返回同一个值；`manage_files(action="replace")` 只认它。
- `doc_`（document_ref）：某次挖掘快照的内容引用，重挖会变、未挖掘文件没有——只用于 `get_knowledge` 读整篇内容。
- `content_revision`：文件内容版本号（每次替换 +1），替换时的 CAS 暗号（expected_revision），不是 ID。

两条闭环路径使用同一个 document_id：路径 A `get_knowledge`（浏览/清单）→ `manage_files(replace)`；路径 B `search_knowledge` → `source.document_id` → `manage_files(replace)`。referenced=true 的外部引用文档 document_id 为 null，不可替换。

**browse_directory 与 directory_prefix 的分工**：前者是"打开文件夹看一眼"（直属子目录+直属文件，空目录可见，根目录传 `""`）；后者是"搜索时限定该目录及全部子目录"的递归过滤（配 file_query/search filters 用）。

工具开关按用户级 open_tools 控制；含已退役/改名工具名的历史配置会在验钥时自动迁移（剔除退役名 + 改名映射 1:1 不扩权，如 upload_document → manage_files）。58号起老钥匙的自定义 instructions 已随迁移清空（以官方默认为准），自定义工具描述按工具名查找、旧名挂载的自动失效。

传输相关的 `MCP_TRANSPORT` / `MCP_HOST` / `MCP_PORT` 见上文「两种运行模式」；后端相关的环境变量见上文那张表（**以那张为准**）。

## 典型调用顺序

```
1. search_knowledge(query="...", domain="...")
     → EvidenceResponse：query / evidence[]（ref,type,content,source）/ has_more
2. 需要 table_row 级收窄时：
   search_knowledge(..., filters={"evidence_types": ["table_row"]})
   （不支持的结构化过滤键返回 typed 400，不静默忽略）
3. 需要准确引用/更大粒度时：
   get_knowledge(ref=条目.ref, mode="parent")
4. 结构化追问：
   get_knowledge(ref=条目.source.document_ref)   → 能力报告
   get_knowledge(ref=st_…, query={"aggregate": {...}})  → 表格查询
5. 文件管理（浏览 → 上传/替换）：
   get_knowledge(kb_name=..., browse_directory="")        → 逐层看目录
   manage_files(action="upload", ..., directory_path="产品文档/交换机")
   manage_files(action="replace", ..., document_id=..., expected_revision=…)
6. Agent 自行判断证据是否充分，决定如何回答
```
