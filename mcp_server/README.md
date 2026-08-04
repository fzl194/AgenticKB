# Cloud Core Knowledge MCP Server

云核心网知识证据底座 MCP Server。

## 设计原则

- **不做语义判断**：检索结果原样交给 Agent，Server 不评估证据是否充分、不改写内容
- **instructions 承载精华**：SKILL 中的使用指南、证据理解规则、回答行为、推理护栏全部内嵌在 MCP instructions 中，Agent 拿到即可正确使用
- **Agent 自主判断**：证据是否充分、如何回答，完全由 Agent（LLM）决定，Server 不做评估
- **只暴露 1 个 tool**：`search_knowledge`。`health_check` 已实现但在 `server.py` 里被注释掉，仅供内部调用，**不对外暴露**，无 resource、无 prompt

> ⚠️ **不再是「纯透传」**。为了让「发布检索范式后 MCP 自动用上」成立，client 现在做两件加工：按 domain 解析该用哪条检索引擎，以及把两条引擎不同的响应外壳归一化。详见下节。

## 检索范式自动匹配

每次 `search_knowledge` 调用：

```
GET  /api/v1/paradigm/resolve?domain={domain}
  ├─ bound=true  → POST /api/v1/paradigm/{id}/search    （该域绑定的检索范式）
  └─ bound=false → POST /api/v1/search                  （默认检索管线）
```

解析**不做缓存**——它是控制库的单行索引查询、容器内走 localhost，相对 120s 的检索预算可忽略，换来的是「发布并绑定后，下一次调用立即生效」，不需要向使用者解释生效延迟。

**解析失败（超时/5xx/网络错误）会回落到默认管线**并记 warning，不让配置面的故障拖垮检索面。但**范式执行失败不会回落**——那会让一条坏掉的绑定范式被无限期掩盖，而且用的是运维方没有配置的引擎。

响应外壳统一成 `/api/v1/search` 的形状（范式那条路会把 `contextPack` 拆平、`evidenceGroups` 改成 `evidence_groups`），Agent 无法从结构上区分两条引擎。真正说明来源的是响应里新增的 `_retrieval` 字段：

```json
"_retrieval": {"engine":"paradigm","paradigm_id":"pd-abc","name":"odn-production","version":3}
"_retrieval": {"engine":"legacy"}
```

### ⚠️ `scope` / `entities` 两个 tool 参数当前不生效

后端检索管线**从不消费**它们：`SearchService` 只读 `query`/`domain`/`channel`/`debug`/`kbIds`，查询理解只拿到 query 字符串（检索器里的 `query.scope()`/`query.entities()` 属于后端自行抽取的 `QueryUnderstanding`，与请求体同名但无关）。参数予以保留以免破坏既有调用，但一旦传入非空值，会在 `_retrieval.ignored_args` 里回报并记 warning。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SERVING_URL` | `http://121.89.90.178:8081` | 后端地址。容器内由 supervisord 覆盖成 `http://localhost:8081` |
| `MCP_PARADIGM_ROUTING` | `1` | 置 `0`/`false`/`off` 一键回到纯透传（与挖掘侧 `MINING_RUN_SUBMISSION_ENGINE=legacy` 同构） |
| `RESOLVE_TIMEOUT` | `5.0` | 范式解析超时（秒）。刻意短：它绝不该成为检索超时的原因 |
| `SEARCH_TIMEOUT` | `120.0` | 检索超时（秒） |
| `HEALTH_TIMEOUT` | `10.0` | 健康检查超时（秒） |

第二条回退路径不需要重启任何服务：取消该域的 `is_default` 绑定，下一次调用解析不到范式，自动回落。

## 文件结构

```
mcp_server/
├── __init__.py      # 版本号
├── __main__.py      # 入口，支持 stdio / streamable-http / sse
├── server.py        # FastMCP 定义：instructions + 对外 1 个 tool
├── client.py        # HTTP 客户端：范式路由 + 回落 + 响应归一化
├── schemas.py       # Pydantic 模型（HealthResult, SearchInput, EntityRef）
├── tests/           # pytest：路由/回落/归一化/ignored_args（不需要后端、不需要 mcp 包）
└── README.md        # 本文件
```

## 前提

- Python 3.10+
- 后端正在运行（`SERVING_URL`，见上表）
- 依赖：`pip install "mcp>=1.0,<2.0" httpx`

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
        result = await session.call_tool("health_check")
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

### 步骤 3：调用 health_check

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "health_check",
    "arguments": {}
  }
}
```

### 步骤 4：调用 search_knowledge

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

返回后端原始 JSON，包含 `items`（证据列表）、`relations`、`sources` 等字段。每条 item 的 `evidence_role` 标注了该证据的角色（`direct_answer` / `support` / `contrast` / `background` / `missing`），Agent 据此自行判断证据是否充分。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SERVING_URL` | `http://127.0.0.1:8000` | 后端地址 |
| `HEALTH_TIMEOUT` | `10.0` | health 请求超时（秒） |
| `SEARCH_TIMEOUT` | `120.0` | search 请求超时（秒） |
| `MCP_TRANSPORT` | `stdio` | 传输模式 |
| `MCP_HOST` | `0.0.0.0` | HTTP 绑定地址 |
| `MCP_PORT` | `9000` | HTTP 绑定端口 |

## 可用工具

| 工具 | 用途 |
|------|------|
| `health_check` | 检查知识库是否可用 |
| `search_knowledge` | 检索知识库，返回证据包（透传后端原始结果） |

## 典型调用顺序

```
1. health_check() → 确认后端可用
2. search_knowledge(query="...") → 获取证据包
3. Agent 自行判断证据是否充分，决定如何回答
```
