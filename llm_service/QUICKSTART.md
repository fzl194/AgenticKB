# LLM Service 快速上手指南

> 最后核对日期：2026-06-18（与 README v2.0 / ARCHITECTURE.md 同步）。本版本相对旧版有重大变更：**数据库由 SQLite 迁至 PostgreSQL；配置不再走 `.env` 环境变量，改为控制面 HTTP 拉取**。下面示例已按新机制更新。

## 概述

独立的 LLM 调用服务，FastAPI 进程，端口 8900。
- 支持任意 OpenAI 兼容接口（DeepSeek / OpenAI / 通义千问 / 硅基流动 / Ollama 等）+ Anthropic 原生 API
- 支持共享 Embedding / Rerank 模型接口（当前默认对接 BigModel）
- 同步调用（`execute`，等结果）+ 异步提交（`submit`，后台 Worker 执行）
- Prompt 模板管理（`$variable` 占位符 + JSON Schema 校验）
- 三重 JSON 保障：schema 注入 prompt → response_format → jsonschema 后校验
- 所有数据存 **PostgreSQL**（库 `agent_llm_runtime`，7 张表），启动自动建库建表
- 配置变更通过系统设置中的“一键重启后台服务”统一生效
- Python SDK：`LLMClient`，Mining / Serving 直接 import 使用

## 目录结构

```
llm_service/
├── __main__.py          # python -m llm_service 入口（含 Windows event loop fix）
├── main.py              # FastAPI app 工厂 + lifespan
├── config.py            # 从控制面拉取服务配置（${VAR} 展开）
├── pg_config.py         # 从控制面拉取 DB 连接信息
├── pg_schema.py         # PostgreSQL DDL 执行（含 stale connection 清理）
├── db.py                # psycopg AsyncConnectionPool
├── models.py            # Pydantic 请求/响应模型
├── client.py            # Python SDK（Mining/Serving 接入用）
├── runtime/
│   ├── service.py       # LLMService：submit / execute 编排（含同步快路径）
│   ├── model_service.py # Embedding / Rerank 服务层
│   ├── task_manager.py  # 任务生命周期（FOR UPDATE SKIP LOCKED claim）
│   ├── worker.py        # 后台 Worker + LeaseRecovery
│   ├── persist_writer.py# 同步路径异步落库（Queue + 批写）
│   ├── parser.py        # 输出解析（text/json_object/json_array + schema 校验）
│   ├── event_bus.py     # 事件落库（INSERT）
│   ├── template_registry.py  # 模板 CRUD（含缓存）
│   └── idempotency.py   # 幂等查询
├── providers/           # ProviderProtocol / ModelProviderProtocol 实现
└── api/                 # 25 个 REST 路由（tasks/results/templates/admin/stats/health/model_api）
```

> **架构详情**：参见 [`ARCHITECTURE.md`](./ARCHITECTURE.md)。旧版提到的 `runtime/executor.py` / `dashboard/` / `templates/` 目录**均已不存在**。

## 1. 安装依赖

在项目根目录执行：

```bash
pip install -e .
```

依赖包含：`fastapi` / `uvicorn` / `psycopg[binary,pool]` / `pydantic-settings` / `httpx` / `jsonschema`。

## 2. 配置（控制面拉取）

**llm_service 不再从本地 `.env` 读 yaml 配置**。启动时通过 HTTP 从控制面拉取两份配置：

| 配置 | URL |
|---|---|
| Service 配置 | `${CONTROL_PLANE_BASE_URL}/api/v1/system/llm_service/raw` |
| DB 配置 | `${CONTROL_PLANE_BASE_URL}/api/v1/system/database/raw` |

### 2.1 启动必需的环境变量

```
CONTROL_PLANE_BASE_URL=http://your-control-plane
```

### 2.2 控制面 dict 内的关键字段

控制面返回一个 dict，必须包含 25 个字段路径（`config.py::_REQUIRED_PATHS`）。主要分组：

| 分组 | 关键字段 |
|---|---|
| `host` / `port` | `0.0.0.0` / `8900` |
| `provider.*` | type / api_key / base_url / models / active_model / timeout / bypass_proxy |
| `embedding.*` 或 `model_provider.embedding.*` | base_url / api_key / model / dimensions |
| `rerank.*` 或 `model_provider.rerank.*` | base_url / api_key / model |
| `worker.*` | concurrency / poll_interval |
| `persist_writer.*` | queue_size / batch_size / flush_interval / writer_count |
| `task.*` | default_max_attempts / retry_backoff_base/max / execute_timeout / lease_duration / lease_recovery_interval |
| `template.*` | cache_ttl |
| `db.pool.*` | min_size / max_size |

### 2.3 `${VAR}` 环境变量展开

`config.py` 解析 dict 时，对字符串值做 `${VAR_NAME}` → `os.environ[VAR_NAME]` 展开。典型用法：把 `provider.api_key` 在控制面写成 `${MY_LLM_KEY}`，真实 key 通过环境变量注入。

### 2.4 Provider 示例（控制面 dict 片段）

```jsonc
{
  "provider": {
    "type": "openai_compatible",
    "base_url": "https://api.deepseek.com/chat/completions",
    "api_key": "${DEEPSEEK_KEY}",     // 从环境变量展开
    "models": {
      "default": {"model": "deepseek-chat", "temperature": 0.3},
      "strong":  {"model": "deepseek-reasoner"}
    },
    "active_model": "default"
  },
  "embedding": {
    "base_url": "https://open.bigmodel.cn/api/paas/v4/embeddings",
    "api_key": "${BIGMODEL_KEY}",
    "model": "embedding-3",
    "dimensions": 1024
  },
  "rerank": {
    "base_url": "https://open.bigmodel.cn/api/paas/v4/rerank",
    "api_key": "${BIGMODEL_KEY}",
    "model": "rerank-pro"
  }
}
```

**Provider 类型可选值**：
- `openai_compatible` — DeepSeek / OpenAI / 通义千问 / 硅基流动 / Ollama 等
- `anthropic` — Anthropic Claude（原生 Messages API）
- `mock` — 测试用

> 修改 provider 后，使用系统设置中的“一键重启后台服务”使配置生效。

## 3. 启动服务

```bash
# 设置控制面地址（必需）
export CONTROL_PLANE_BASE_URL=http://your-control-plane

# 可选：被控制面 dict 中 ${VAR} 引用的密钥
export DEEPSEEK_KEY=sk-xxxxxxxxxxxx
export BIGMODEL_KEY=xxxxxxxxxxxx.xxxxxx

# 启动
python -m llm_service
```

看到以下输出说明启动成功：

```
INFO:     Uvicorn running on http://0.0.0.0:8900 (Press CTRL+C to quit)
```

启动 lifespan 顺序：
1. 从控制面拉 config + db_config
2. `pg_schema.ensure_database()` + `ensure_schema()`（幂等建库建表，含 stale 连接清理）
3. `LlmRuntimeDB` 连接池初始化（psycopg `AsyncConnectionPool`）
4. `health_check()` 验证 DB
5. 把残留的 `status='running'` 任务 re-queue
6. 构造 Provider / ModelProvider / TemplateRegistry / PersistWriter
7. 启动 Worker（`concurrency` 个 `_loop()` 协程）+ LeaseRecovery（30s 扫描）

### 数据库说明

- **类型**：PostgreSQL（库 `agent_llm_runtime`）
- **连接信息**：由控制面 `database/raw` 决定（host/port/user/password/dbname/sslmode）
- **自动建库建表**：首次启动时 `pg_schema.py` 会建库（若不存在）+ 执行 DDL（`databases/agent_llm_runtime/schemas/002_agent_llm_runtime_postgresql.sql`，幂等）
- **想重新开始**：`DROP DATABASE agent_llm_runtime;` 重启后会重建

### 验证启动

```bash
curl http://localhost:8900/health
# 返回：{"status":"ok","db":true,"tables_ok":true}
```

### 验证共享模型接口

```bash
curl -X POST http://localhost:8900/api/v1/models/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "input": ["AMF是什么"],
    "model": "embedding-3"
  }'

curl -X POST http://localhost:8900/api/v1/models/rerank \
  -H "Content-Type: application/json" \
  -d '{
    "query": "AMF 配置命令",
    "documents": ["ADD AMF ...", "UPF 介绍 ..."],
    "model": "rerank",
    "top_n": 2
  }'
```

## 4. 创建 Prompt 模板

模板定义了 system prompt、用户提示词模板和期望输出类型。

### 创建纯文本模板

```bash
curl -X POST http://localhost:8900/api/v1/templates \
  -H "Content-Type: application/json" \
  -d '{
    "template_key": "my-summary",
    "template_version": "1",
    "purpose": "中文摘要",
    "system_prompt": "你是一个助手，用中文简洁回答。",
    "user_prompt_template": "请用一句话总结以下内容：$text",
    "expected_output_type": "text"
  }'
```

### 创建 JSON 输出模板（带 Schema 校验）

```bash
curl -X POST http://localhost:8900/api/v1/templates \
  -H "Content-Type: application/json" \
  -d '{
    "template_key": "my-qa",
    "template_version": "1",
    "purpose": "生成问答对",
    "system_prompt": "你是知识挖掘助手。返回JSON格式。",
    "user_prompt_template": "根据以下内容生成3个问答对：$content",
    "expected_output_type": "json_object",
    "output_schema_json": "{\"type\":\"object\",\"properties\":{\"questions\":{\"type\":\"array\",\"items\":{\"type\":\"object\",\"properties\":{\"q\":{\"type\":\"string\"},\"a\":{\"type\":\"string\"}},\"required\":[\"q\",\"a\"]}}},\"required\":[\"questions\"]}"
  }'
```

> `output_schema_json` 是 JSON 字符串。设置后系统会自动：
> 1. 把 schema 注入到 system prompt，让模型知道输出格式
> 2. 传 `response_format={"type":"json_object"}` 给 Provider
> 3. 用 jsonschema 校验输出，不符合时返回 `parse_status="schema_invalid"`

### 查看已创建的模板

```bash
curl http://localhost:8900/api/v1/templates
```

### 模板语法说明

- `user_prompt_template` 中用 `$变量名` 引用 `input` 中的字段
- 例如模板是 `请总结：$text`，input 是 `{"text": "内容"}`，最终发给模型的就是 `请总结：内容`
- `expected_output_type` 三种值：`text`（纯文本）、`json_object`（JSON 对象）、`json_array`（JSON 数组）
- 调用时如果不指定 `expected_output_type`，会用模板里声明的类型
- 调用时如果不指定 `output_schema`，会用模板里声明的 schema

## 5. 同步调用（等结果，适合在线场景）

发送请求后等模型回复，直接返回完整结果。

### 用模板调用

```bash
curl -X POST http://localhost:8900/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{
    "caller_domain": "serving",
    "pipeline_stage": "normalizer",
    "template_key": "my-summary",
    "input": {"text": "Python是一种通用编程语言，支持面向对象和函数式编程。"}
  }'
```

返回示例：

```json
{
  "task_id": "06948ea6-...",
  "status": "succeeded",
  "attempts": 1,
  "total_tokens": 156,
  "latency_ms": 2340,
  "result": {
    "parse_status": "succeeded",
    "parsed_output": null,
    "text_output": "Python是一种支持面向对象和函数式编程的通用语言。",
    "validation_errors": []
  },
  "error": null
}
```

### 不用模板，直接发 messages

```bash
curl -X POST http://localhost:8900/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{
    "caller_domain": "serving",
    "pipeline_stage": "rerank",
    "messages": [
      {"role": "system", "content": "你是一个助手。"},
      {"role": "user", "content": "什么是FastAPI？用一句话回答。"}
    ],
    "expected_output_type": "text"
  }'
```

### 带 Schema 校验（运行时指定，不依赖模板）

```bash
curl -X POST http://localhost:8900/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{
    "caller_domain": "mining",
    "pipeline_stage": "extract",
    "messages": [
      {"role": "user", "content": "从以下文本中提取人名：张三和李四在北京开会。"}
    ],
    "expected_output_type": "json_object",
    "output_schema": {
      "type": "object",
      "properties": {
        "names": {"type": "array", "items": {"type": "string"}}
      },
      "required": ["names"]
    }
  }'
```

### 传 Provider 参数

```bash
curl -X POST http://localhost:8900/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{
    "caller_domain": "serving",
    "pipeline_stage": "normalizer",
    "template_key": "my-summary",
    "input": {"text": "..."},
    "params": {"temperature": 0.3, "max_tokens": 200}
  }'
```

## 6. 异步提交（后台执行，适合批量场景）

提交后立刻返回 task_id，Worker 后台自动执行。

### 提交单个任务

```bash
curl -X POST http://localhost:8900/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "caller_domain": "mining",
    "pipeline_stage": "enrich",
    "template_key": "my-qa",
    "input": {"content": "FastAPI是一个现代Python Web框架。"},
    "metadata": {"caller_context": {"ref_type": "section", "ref_id": "sec-001"}}
  }'
```

返回：

```json
{"task_id": "e0cd2b67-...", "status": "queued", "idempotency_key": null, "created_at": "..."}
```

### 查看任务状态

```bash
# 把上面返回的 task_id 填进去
curl http://localhost:8900/api/v1/tasks/这里填task_id
```

状态变化：`queued` → `running` → `succeeded`（成功）/ `dead_letter`（耗尽重试）

### 批量提交示例

```bash
# 循环提交多个任务
for i in 1 2 3; do
  curl -s -X POST http://localhost:8900/api/v1/tasks \
    -H "Content-Type: application/json" \
    -d "{
      \"caller_domain\": \"mining\",
      \"pipeline_stage\": \"enrich\",
      \"template_key\": \"my-qa\",
      \"input\": {\"content\": \"第${i}段内容\"},
      \"metadata\": {\"caller_context\": {\"ref_type\": \"section\", \"ref_id\": \"sec-00${i}\"}}
    }" | python -m json.tool
  echo "---"
done
```

### 幂等提交（防重复）

```bash
# 相同 idempotency_key 只会创建一次任务
curl -X POST http://localhost:8900/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "caller_domain": "mining",
    "pipeline_stage": "enrich",
    "template_key": "my-qa",
    "input": {"content": "一段内容"},
    "idempotency_key": "doc-001-sec-003"
  }'
```

### 获取结果

```bash
# 等状态变为 succeeded 后
curl http://localhost:8900/api/v1/tasks/这里填task_id/result
```

## 7. Python SDK 用法

Mining 和_Serving 通过 `LLMClient` 调用，不需要直接拼 HTTP。

### 初始化

```python
from llm_service.client import LLMClient

# 默认连接 localhost:8900
client = LLMClient()

# 指定地址
client = LLMClient(base_url="http://your-server:8900")
```

### 同步执行（Serving 场景）

```python
result = await client.execute(
    caller_domain="serving",
    pipeline_stage="normalizer",
    template_key="my-summary",
    input={"text": "一段需要摘要的文本"},
)

if result["status"] == "succeeded":
    text = result["result"]["text_output"]
    parsed = result["result"]["parsed_output"]  # JSON 输出时为 dict
    parse_status = result["result"]["parse_status"]  # succeeded / failed / schema_invalid
    tokens = result["total_tokens"]
    latency = result["latency_ms"]
```

### 异步提交 + 轮询（Mining 场景）

```python
# 1. 批量提交
task_ids = []
for seg in segments:
    tid = await client.submit(
        caller_domain="mining",
        pipeline_stage="enrich",
        template_key="my-qa",
        input={"content": seg.text},
        idempotency_key=f"seg-{seg.id}-qa",
        metadata={"caller_context": {"ref_type": "section", "ref_id": seg.id}},
    )
    task_ids.append(tid)

# 2. 轮询结果
import asyncio

while task_ids:
    remaining = []
    for tid in task_ids:
        task = await client.get_task(tid)
        if task["status"] == "succeeded":
            result = await client.get_result(tid)
            print(f"完成: {result['parsed_output']}")
        elif task["status"] == "dead_letter":
            attempts = await client.get_attempts(tid)
            print(f"失败: {attempts[-1]['error_message']}")
        else:
            remaining.append(tid)
    task_ids = remaining
    if task_ids:
        await asyncio.sleep(1.0)
```

### 不用模板，直接发 messages

```python
result = await client.execute(
    caller_domain="serving",
    pipeline_stage="rerank",
    messages=[
        {"role": "system", "content": "按相关性排序。"},
        {"role": "user", "content": f"Query: {q}\nDocs: {docs_json}"},
    ],
    expected_output_type="json_array",
)
```

### 运行时传 Schema

```python
result = await client.execute(
    caller_domain="mining",
    pipeline_stage="extract",
    input={"text": text},
    template_key="my-extract",
    output_schema={
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {"type": "object", "properties": {"name": {"type": "string"}}},
            }
        },
        "required": ["entities"],
    },
)

if result["result"]["parse_status"] == "schema_invalid":
    errors = result["result"]["validation_errors"]
    print(f"Schema 不匹配: {errors}")
```

### 调试：查看完整执行链

```python
task_id = "your-task-id"

# 任务信息
task = await client.get_task(task_id)
# → status, attempt_count, max_attempts, metadata, timestamps

# 解析结果
result = await client.get_result(task_id)
# → parse_status, parsed_output, text_output, validation_errors

# 所有尝试（含失败）
attempts = await client.get_attempts(task_id)
# → 每次: status, tokens, latency_ms, error_type, error_message

# 事件时间线
events = await client.get_events(task_id)
# → submitted → claimed → succeeded / retried → dead_letter
```

## 8. 查看结果

### 通过 API

```bash
# 任务信息
curl http://localhost:8900/api/v1/tasks/这里填task_id

# 执行结果
curl http://localhost:8900/api/v1/tasks/这里填task_id/result

# 每次尝试详情（含 token 数、延迟、错误）
curl http://localhost:8900/api/v1/tasks/这里填task_id/attempts

# 事件时间线
curl http://localhost:8900/api/v1/tasks/这里填task_id/events
```

### 通过数据库

```bash
# 用 psql 连接（连接信息从控制面 database/raw 来）
psql "host=<host> port=<port> dbname=agent_llm_runtime user=<user> sslmode=<sslmode>"

# 查看所有任务
SELECT substr(id,1,8) AS id, status, caller_service, pipeline_stage, attempt_count
FROM agent_llm_tasks ORDER BY created_at;

# 查看解析结果
SELECT substr(task_id,1,8), parse_status, text_output
FROM agent_llm_results;

# 查看原始模型输出
SELECT substr(task_id,1,8), raw_output_text FROM agent_llm_attempts
WHERE status='succeeded';

# 查看 token 用量
SELECT substr(task_id,1,8), total_tokens, latency_ms FROM agent_llm_attempts;

# 查看失败原因
SELECT substr(task_id,1,8), error_type, error_message FROM agent_llm_attempts
WHERE status='failed';

\q
```

### 通过 API 文档

浏览器打开：

| 页面 | 地址 |
|------|------|
| API 文档 | http://localhost:8900/docs |
| 健康检查 | http://localhost:8900/health |
| 任务统计 | http://localhost:8900/api/v1/stats |
| Worker 状态 | http://localhost:8900/api/v1/admin/worker-status |

> 旧版的 `/dashboard` Web 看板与 `dashboard/` 目录**已移除**；如需查任务详情请用 `/docs` Swagger 或 `/api/v1/tasks` 列表 API。

## 9. 关闭服务

终端 `Ctrl+C`。Worker 会完成当前任务后退出。

## 10. 常见问题

### Q: 启动报 `_REQUIRED_PATHS` 校验失败

控制面 dict 缺字段。`config.py::_REQUIRED_PATHS` 列了 25 个必须存在的路径（provider.* / worker.* / persist_writer.* / task.* / template.* / db.pool.*）。对照错误提示补齐。

### Q: 启动报 `CONTROL_PLANE_BASE_URL is required`

启动前没设这个环境变量。它是控制面 API 根地址。

### Q: 启动卡在 PG 连接

`pg_schema.py` 启动时会清理 `idle in transaction` 与 >30s active 连接（commit 63c0412）。若仍卡住，检查 `pg_hba.conf` / 防火墙 / 控制面 `database/raw` 配置是否正确。

### Q: 异步任务一直 queued 不执行

检查 Worker 是否正常启动。诊断端点：`GET /api/v1/admin/worker-status` 看 `active_tasks` / `queue_depth`。如果是用 MockProvider 测试则不会有真实执行。

### Q: parse_status 是 `schema_invalid` 但原始输出看起来对

检查你的 `output_schema` 是否正确。`schema_invalid` 表示 JSON 解析成功但不符合 schema 约束。常见问题：
- schema 里声明了 `required` 字段但模型没输出
- schema 里声明了 `additionalProperties: false` 但模型输出了多余字段
- 查看返回的 `validation_errors` 了解具体原因

### Q: parse_status 是 `failed`

模型输出不是合法 JSON。常见原因：
- 模型返回了纯文本而不是 JSON
- `expected_output_type` 设成了 `json_object` 但模型返回了数组
- parser 已自动剥离 markdown 代码块标记，如果仍然失败说明模型确实没返回 JSON

### Q: 数据库里 7 张表分别是干什么的

| 表名 | 作用 |
|------|------|
| `agent_llm_prompt_templates` | Prompt 模板定义（system prompt + user prompt 模板 + schema） |
| `agent_llm_tasks` | 任务主表（status / task_type / priority / 重试次数 / lease_expires_at / idempotency_key） |
| `agent_llm_requests` | 请求详情（messages / input / params / expected_output_type / output_schema_json） |
| `agent_llm_attempts` | 每次尝试记录（tokens / latency / raw_output_text / error_type） |
| `agent_llm_results` | 解析结果（parse_status / parsed_output / text_output / validation_errors） |
| `agent_llm_events` | 事件流水（submitted / claimed / succeeded / retried / dead_letter / cancelled） |
| `agent_llm_model_calls` | **Embedding/Rerank 审计**（call_type / model / input_count / latency / tokens） |

### Q: Worker 并发数怎么调

修改控制面 `worker.concurrency` 后重启后台服务。默认 4，调太高可能触发 Provider 限流。

### Q: 想切换 Provider（如 DeepSeek → Anthropic）

修改控制面 `provider.type` + 对应字段后，使用系统设置中的“一键重启后台服务”。
