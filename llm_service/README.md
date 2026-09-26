# LLM Service

> 统一 LLM 调用与审计服务，为 Mining / Serving 提供集中式的 chat、Embedding、Rerank 模型能力。
> 版本：v2.0 | 存储：PostgreSQL（`agent_llm_runtime`，7 张表）| 端口：8900 | 测试：33 个（28 active + 5 skipped 集成 stub）

## 1. 系统定位

LLM Service 是一个**独立运行的 FastAPI 服务**，使用 PostgreSQL 持久化任务/审计数据。**配置不再来自本地 `.env`**，启动时从控制面（control plane）HTTP 拉取（见 §4）。

**核心职责：**
- 统一管理 chat 类 LLM 调用的提交、执行、重试、结果解析与审计
- 统一暴露 Embedding / Rerank 模型 HTTP 接口给 Mining / Serving 复用
- 提供模板 CRUD、任务重试与取消等运维能力

Mining / Serving 不各自维护模型调用逻辑，而是通过 `LLMClient` 或 HTTP API 调用本服务。

```
┌─────────┐     ┌─────────┐
│ Mining  │     │ Serving │
│ (异步)  │     │ (同步)  │
└────┬────┘     └────┬────┘
     │ submit()      │ execute()
     │ get_result()  │ 直接返回
     └───────┬───────┘
             ▼
   ┌────────────────────┐
   │  LLM Service       │  ← 你在这里
   │  FastAPI :8900     │
   │  PostgreSQL        │
   │  Worker + Recovery │
   │  PersistWriter     │
   └─────────┬──────────┘
             │
   ┌─────────┼──────────┬─────────────┐
   ▼         ▼          ▼             ▼
OpenAI兼容  Anthropic  BigModel     Mock
(chat)      (chat)     (embed/rerank) (test)
```

> 深入架构（启动生命周期 / 数据流 / 状态机 / Provider 协议 / 存储层）请阅读 [`ARCHITECTURE.md`](./ARCHITECTURE.md)。

## 2. 快速启动

完整步骤见 [`QUICKSTART.md`](./QUICKSTART.md)，最短路径：

```bash
# 1. 安装依赖
pip install -e .

# 2. 设置控制面 URL（替代旧的 LLM_SERVICE_* env 配置方式）
export CONTROL_PLANE_BASE_URL=http://your-control-plane

# 3. 启动（自动从控制面拉配置 + 创建/迁移 PG schema + 启动 worker）
python -m llm_service

# 4. 验证
curl http://localhost:8900/health
```

> Windows 上 `__main__.py` 会自动切换到 `asyncio.SelectorEventLoop`（psycopg async 兼容）。

## 3. API 总览

所有接口前缀 `/api/v1`，完整 URL：`http://localhost:8900/api/v1/...`。共 **25 个端点**。

### 3.1 提交任务（异步队列）

| Method | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/tasks` | 异步提交 chat 任务（Mining 主用） |
| POST | `/api/v1/tasks/embed` | 异步提交 embedding 任务 |
| POST | `/api/v1/tasks/rerank` | 异步提交 rerank 任务 |

异步流程：提交立即返回 task_id → 后台 Worker claim → 调 Provider → 解析 → 落审计表。

### 3.2 同步直通（绕过队列）

| Method | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/execute` | **同步**执行 chat，阻塞等待返回（Serving 主用） |
| POST | `/api/v1/models/embeddings` | **同步** embedding（已加 `X-Deprecation-Notice`，建议改用 `/api/v1/tasks/embed`） |
| POST | `/api/v1/models/rerank` | **同步** rerank（已加 `X-Deprecation-Notice`，建议改用 `/api/v1/tasks/rerank`） |

同步路径**不走 claim / Worker**：`LLMService.execute()` 直接调 Provider → 解析 → 内存构造响应 → `PersistWriter.enqueue()` 异步落库（详见 ARCHITECTURE §3.2）。

### 3.3 任务查询

| Method | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/tasks/{task_id}` | 任务详情（含 request/result/attempts/events） |
| GET | `/api/v1/tasks/{task_id}/result` | 解析结果（parsed_output / text_output / validation_errors） |
| GET | `/api/v1/tasks/{task_id}/attempts` | 所有尝试列表（含 tokens / latency / raw_output） |
| GET | `/api/v1/tasks/{task_id}/events` | 状态变迁事件流 |
| GET | `/api/v1/tasks/{task_id}/request` | 原始请求快照 |

### 3.4 任务管理

| Method | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/tasks/{task_id}/cancel` | 取消 queued 任务（仅 queued 状态可取消） |
| POST | `/api/v1/tasks/{task_id}/retry` | 重置 failed/dead_letter/cancelled 任务为 queued（attempt_count=0） |
| POST | `/api/v1/tasks/batch-cancel` | 批量取消 |

### 3.5 模板 CRUD

| Method | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/templates` | 创建/UPSERT 模板（`(template_key, template_version, knowledge_domain)` 唯一） |
| GET | `/api/v1/templates` | 列出模板（可选 `domain` 过滤） |
| GET | `/api/v1/templates/{template_key}` | 按 key+domain 解析模板（domain-specific 优先于 NULL） |
| PUT | `/api/v1/templates/{tpl_id}` | 更新模板字段（白名单列） |
| DELETE | `/api/v1/templates/{tpl_id}` | **归档**（status='archived'，不删行） |

### 3.6 统计与诊断

| Method | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/stats` | 全局聚合统计（可选 `domain` 过滤） |
| GET | `/api/v1/stats/tokens` | token 用量细分 |
| GET | `/api/v1/tasks` | 任务列表（分页 + 多维过滤：status/task_type/domain/...） |
| GET | `/api/v1/admin/worker-status` | Worker 诊断（concurrency / active_tasks / queue_depth） |
| GET | `/health` | 健康检查（含 DB 连通性 + tables_ok） |

### 3.7 请求/响应示例（同步 execute）

**请求体（`TaskSubmitRequest`）：**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `caller_service` | string | 是* | 调用方服务标识（`mining` / `serving` / 其他）；*legacy 别名 `caller_domain` 仍兼容 |
| `pipeline_stage` | string | 是 | 管道阶段，正则 `^[a-z][a-z0-9_]{1,63}$` |
| `knowledge_domain` | string | 否 | 知识域（用于模板 domain-specific 解析） |
| `template_key` | string | 否 | 模板 key（与 `messages` 二选一） |
| `input` | object | 否 | 模板变量（与 template_key 配合） |
| `messages` | array | 否 | 直接提供 messages（优先级高于模板） |
| `params` | object | 否 | 透传 Provider（temperature / max_tokens 等） |
| `expected_output_type` | string | 否 | `text` / `json_object` / `json_array`（默认 `json_object`） |
| `output_schema` | object | 否 | JSON Schema（自动注入 prompt + 后校验） |
| `idempotency_key` | string | 否 | 幂等键（相同 key + 未终态 → 复用） |
| `metadata` | object | 否 | 调用方自定义元数据 |
| `max_attempts` | int | 否 | 最大尝试次数（默认 3） |
| `priority` | int | 否 | 优先级（默认 100，数值大优先） |

**响应（成功）：**

```json
{
  "task_id": "06948ea6-...",
  "status": "succeeded",
  "attempts": 1,
  "total_tokens": 156,
  "latency_ms": 2340,
  "result": {
    "parse_status": "succeeded",
    "parsed_output": {"summary": "..."},
    "text_output": null,
    "validation_errors": []
  },
  "error": null
}
```

> 失败时 `status="failed"`，`error.error_type` 取值：`timeout` / `connection_error` / `rate_limited` / `server_error` / `client_error` / `invalid_response` / `not_configured`。
> 注意：任务级终态只有 `succeeded` / `dead_letter` / `cancelled`；`failed` 仅是 attempt 级与同步响应中的状态。

## 4. 配置（控制面拉取）

**llm_service 不再从本地 `.env` 读 yaml 配置**。启动时通过 HTTP 从控制面拉取两份配置：

| 配置 | URL | 解析文件 |
|---|---|---|
| Service 配置 | `{CONTROL_PLANE_BASE_URL}/api/v1/system/llm_service/raw` | `config.py` |
| DB 配置 | `{CONTROL_PLANE_BASE_URL}/api/v1/system/database/raw` | `pg_config.py` |

### 4.1 启动必需的环境变量

| 变量 | 用途 |
|---|---|
| `CONTROL_PLANE_BASE_URL` | 控制面 API 根（**唯一**强制环境变量） |

### 4.2 控制面 dict 内的关键路径

`config.py::_REQUIRED_PATHS` 强制 25 个字段必须存在（缺失即启动失败）。主要分组：

| 分组 | 关键字段 |
|---|---|
| `provider.*` | type / api_key / base_url / models / active_model / timeout / bypass_proxy / extra_headers |
| `model_provider.*` 或 `embedding.*`+`rerank.*` | embedding/rerank 的 base_url / api_key / model / dimensions |
| `worker.*` | concurrency / poll_interval |
| `persist_writer.*` | queue_size / batch_size / flush_interval / writer_count |
| `task.*` | default_max_attempts / retry_backoff_base / retry_backoff_max / execute_timeout / lease_duration / lease_recovery_interval |
| `template.*` | cache_ttl |
| `db.pool.*` | min_size / max_size |

### 4.3 `${VAR}` 环境变量展开

`config.py` 解析控制面 dict 时，对字符串值做 `${VAR_NAME}` → `os.environ[VAR_NAME]` 展开。典型用途：把 `provider.api_key` 写成 `${MY_LLM_KEY}`，真实 key 通过环境变量注入而非明文存控制面。

### 4.4 多模型选择

`provider.models` 是 dict（key 为别名如 `default` / `cheap` / `strong`），`provider.active_model` 指向当前生效 key。`resolve_active_model_config()` 把 `provider.models[active_model]` 深合并覆盖 `provider` 顶层，作为运行时配置。

### 4.5 配置生效

修改控制面配置后，通过系统设置中的“一键重启后台服务”统一重启；LLM Service 会在
启动阶段重新拉取 config 与 db_config。运行中的任务会按既有停机恢复机制重新入队。

## 5. 架构深入

完整实现细节见 [`ARCHITECTURE.md`](./ARCHITECTURE.md)，涵盖：

- 模块全图（每个文件一句话职责）
- 启动生命周期（lifespan → DB → Provider → Template → PersistWriter → Worker → LeaseRecovery）
- 数据流（异步 chat / 同步 chat / 异步 embed+rerank / 同步 embed+rerank / parser）
- 任务状态机（5 个状态 + 迁移矩阵 + 退避公式 + Lease 机制）
- Provider 体系（双协议 + 能力矩阵 + BigModel rerank 三层批处理上限 + Anthropic JSON 策略 + 扩展指南）
- 存储层（PostgreSQL 7 张表 schema + PersistWriter 解耦机制）
- 配置生效（控制面拉取 + 多模型 + 重启流程）

## 6. 测试

### 6.1 运行

```bash
pytest llm_service/tests/ -v
```

### 6.2 测试清单（共 33 个）

| 文件 | 数量 | 类型 | 覆盖范围 |
|---|---|---|---|
| `test_parser.py` | 12 | unit | json_object / json_array / text / markdown 剥围栏 / schema 通过失败 / 非法 schema 不崩溃 |
| `test_providers.py` | 8 | unit | Mock 循环 + OpenAICompatible URL + Anthropic system 转换 / JSON fallback 提取 / tool_use 序列化 |
| `test_models.py` | 7 | unit | TaskSubmitRequest 默认值 / legacy caller_domain 兼容 / 校验 / ExecuteResponse / Embedding+Rerank 请求 |
| `test_client.py` | 1 + 5 skip | unit + 集成 stub | `_build_submit_payload` 真实测试 + 5 个需运行服务的集成 stub |
| `conftest.py` | — | fixture | `config` fixture + `TEST_CFG` 常量 + asyncio_mode=auto |

### 6.3 非测试脚本（不要混入 pytest）

| 文件 | 类型 | 用途 |
|---|---|---|
| `curl_test.md` | 食谱 | 三大能力（chat/embed/rerank）的 curl 示例，含错误码表 |
| `profile_execute.py` | 性能脚本 | 手动跑，分解 `/api/v1/execute` 的 handler/send/client 开销 |
| `test_live_demo.py` | live demo | 带 `__main__` 的端到端演示，**非 pytest**；注意 line 147-152 仍残留 SQLite 输出（已知漂移，见 handoff） |

## 7. 运维与排错

### 7.1 端口与启动

- 默认 `0.0.0.0:8900`（`host` / `port` 来自控制面 dict 顶层）
- 入口：`python -m llm_service`（`__main__.py` 处理 Windows event loop + uvicorn factory）
- 健康检查：`GET /health` 返回 `{status, db, tables_ok}`

### 7.2 死锁恢复（commit 63c0412）

启动时 `pg_schema._cleanup_stale_connections` 会：
- `pg_terminate_backend` 所有 `idle in transaction` 连接（前次 SIGKILL 留下的锁）
- `pg_terminate_backend` 超过 30s 的 active 查询

避免冷启动卡在锁等待上。

### 7.3 Worker 卡住

- 诊断：`GET /api/v1/admin/worker-status` 看 `active_tasks` / `queue_depth`
- Lease 机制：Worker claim 时设 `lease_expires_at = now + lease_duration`（默认 300s）
- `LeaseRecovery` 每 30s 扫 `status='running' AND lease_expires_at < now` → 触发 fail（可重试则 re-queue，否则 dead_letter）

### 7.4 任务恢复

- 启动时会把所有 `status='running'` 任务 re-queue（避免前次崩溃卡死任务）
- 失败任务可通过 `POST /api/v1/tasks/{id}/retry` 重置（attempt_count=0）

### 7.5 配置变更

修改控制面配置后，使用系统设置中的“一键重启后台服务”使配置生效。

### 7.6 日志

`logging.getLogger("llm_service")`；PersistWriter 队列满时打 WARNING 并计数 `_dropped`（前 10 条 + 每 1000 条）。

## 8. 已知边界

- 无流式输出 / WebSocket 通知
- 无批量提交 API（需逐个 POST）
- `architecture.html` 是 2026-04-23 旧版架构图，**已过时**，请读 `ARCHITECTURE.md`
- 旧 README v1.2 提到的 `runtime/executor.py`、`dashboard/`、`templates/` 目录均已不存在
- 同步 `/api/v1/models/embeddings|rerank` 已加 Deprecation header，建议迁移到异步 `/api/v1/tasks/embed|rerank`

## 9. 相关文档

- [快速上手](./QUICKSTART.md)
- [模块架构](./ARCHITECTURE.md)（v2.0 当前实现）
- [PostgreSQL DDL](../databases/agent_llm_runtime/schemas/002_agent_llm_runtime_postgresql.sql)
- [Swagger 文档](http://localhost:8900/docs)（启动服务后访问）
- [文档审计 handoff](../docs/handoffs/2026-06-18-llm-service-docs-audit-claude-handoff.md)（漂移点分级清单）
