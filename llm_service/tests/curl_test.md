# LLM Service Curl 测试手册

> 基于源码整理，覆盖三大模型能力：**Chat（LLM）**、**Embedding**、**Rerank**

## 前置

```bash
# 设置服务地址（按实际修改）
export BASE="http://localhost:8920"
```

---

## 1. 健康检查

```bash
# 1.1 基础健康检查（含数据库连接状态）
curl -s "$BASE/health" | python3 -m json.tool
```

## 2. Chat（LLM 对话）— 异步队列模式

```bash
# 2.1 提交异步 chat 任务（需要 caller_service + pipeline_stage）
TASK_ID=$(curl -s -X POST "$BASE/api/v1/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "caller_service": "test",
    "pipeline_stage": "test_chat",
    "messages": [
      {"role": "system", "content": "你是一个有帮助的助手"},
      {"role": "user", "content": "用一句话介绍5G核心网"}
    ],
    "params": {"temperature": 0.7, "max_tokens": 200},
    "expected_output_type": "text"
  }' | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['task_id'])")

echo "task_id=$TASK_ID"

# 2.2 查询任务状态（等待几秒后查询）
sleep 5
curl -s "$BASE/api/v1/tasks/$TASK_ID" | python3 -m json.tool

# 2.3 获取任务结果
curl -s "$BASE/api/v1/tasks/$TASK_ID/result" | python3 -m json.tool

# 2.4 获取任务请求详情
curl -s "$BASE/api/v1/tasks/$TASK_ID/request" | python3 -m json.tool

# 2.5 获取任务尝试记录
curl -s "$BASE/api/v1/tasks/$TASK_ID/attempts" | python3 -m json.tool

# 2.6 获取任务事件
curl -s "$BASE/api/v1/tasks/$TASK_ID/events" | python3 -m json.tool
```

## 3. Chat（LLM 对话）— 同步执行模式

```bash
# 3.1 同步执行（阻塞等待结果，适合快速测试）
curl -s -X POST "$BASE/api/v1/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "caller_service": "test",
    "pipeline_stage": "test_execute",
    "messages": [
      {"role": "user", "content": "说一个数字"}
    ],
    "params": {"temperature": 0.5, "max_tokens": 50},
    "expected_output_type": "text"
  }' | python3 -m json.tool
```

## 4. Embedding（向量嵌入）

```bash
# 4.1 同步 embedding（直接返回向量）
curl -s -X POST "$BASE/api/v1/models/embeddings" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "5G核心网是第五代移动通信的核心网络架构",
    "caller_service": "test"
  }' | python3 -m json.tool

# 4.2 批量 embedding
curl -s -X POST "$BASE/api/v1/models/embeddings" \
  -H "Content-Type: application/json" \
  -d '{
    "input": [
      "AMF负责接入和移动性管理",
      "SMF负责会话管理功能",
      "UPF负责用户面功能"
    ],
    "caller_service": "test"
  }' | python3 -m json.tool

# 4.3 指定 dimensions 的 embedding
curl -s -X POST "$BASE/api/v1/models/embeddings" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "测试指定维度",
    "dimensions": 512,
    "caller_service": "test"
  }' | python3 -m json.tool

# 4.4 异步 embedding 任务（走 worker 队列）
curl -s -X POST "$BASE/api/v1/tasks/embed" \
  -H "Content-Type: application/json" \
  -d '{
    "input": ["测试异步embedding"],
    "caller_service": "test",
    "pipeline_stage": "test_embed"
  }' | python3 -m json.tool
```

## 5. Rerank（重排序）

```bash
# 5.1 同步 rerank（直接返回排序结果）
curl -s -X POST "$BASE/api/v1/models/rerank" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "5G核心网有哪些网元",
    "documents": [
      "AMF负责接入和移动性管理功能",
      "HTTP是一种应用层协议",
      "SMF负责会话管理，控制UPF的数据转发",
      "Python是一种编程语言",
      "UPF是用户面功能，处理数据包转发"
    ],
    "caller_service": "test"
  }' | python3 -m json.tool

# 5.2 同步 rerank 指定 top_n
curl -s -X POST "$BASE/api/v1/models/rerank" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "SMF的功能是什么",
    "documents": [
      "AMF负责接入管理",
      "SMF负责会话管理功能，控制策略执行",
      "UPF处理数据面转发",
      "SMF支持UPF的选择和管理"
    ],
    "top_n": 2,
    "caller_service": "test"
  }' | python3 -m json.tool

# 5.3 异步 rerank 任务（走 worker 队列）
curl -s -X POST "$BASE/api/v1/tasks/rerank" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "什么是网络切片",
    "documents": [
      "网络切片是5G的重要特性",
      "Docker是容器技术",
      "网络切片允许在同一物理网络上创建多个虚拟网络"
    ],
    "caller_service": "test",
    "pipeline_stage": "test_rerank"
  }' | python3 -m json.tool
```

## 6. 任务管理

```bash
# 6.1 列出任务（支持分页和过滤）
curl -s "$BASE/api/v1/tasks?page=1&page_size=10" | python3 -m json.tool

# 6.2 按状态过滤
curl -s "$BASE/api/v1/tasks?status=succeeded&page_size=5" | python3 -m json.tool

# 6.3 按 task_type 过滤
curl -s "$BASE/api/v1/tasks?task_type=embedding&page_size=5" | python3 -m json.tool

# 6.4 取消单个任务（需要先拿到一个 task_id）
curl -s -X POST "$BASE/api/v1/tasks/<TASK_ID>/cancel" | python3 -m json.tool

# 6.5 重试失败任务
curl -s -X POST "$BASE/api/v1/tasks/<TASK_ID>/retry" | python3 -m json.tool

# 6.6 批量取消
curl -s -X POST "$BASE/api/v1/tasks/batch-cancel" \
  -H "Content-Type: application/json" \
  -d '{"task_ids": ["<ID1>", "<ID2>"]}' | python3 -m json.tool

# 6.7 批量状态查询（挖掘异步通道轮询用：一次请求替代 N 次 GET /tasks/{id}；
#     succeeded 任务内联 result，failed/dead_letter 挂最新失败 attempt 的 error，
#     未知 id 进 not_found；task_ids 上限 256）
curl -s -X POST "$BASE/api/v1/tasks/batch-status" \
  -H "Content-Type: application/json" \
  -d '{"task_ids": ["<ID1>", "<ID2>"], "include_results": true}' | python3 -m json.tool
```

## 7. 统计与管理

```bash
# 7.1 全局统计
curl -s "$BASE/api/v1/stats" | python3 -m json.tool

# 7.2 按 domain 过滤统计
curl -s "$BASE/api/v1/stats?domain=cloud_core" | python3 -m json.tool

# 7.3 Token 使用统计
curl -s "$BASE/api/v1/stats/tokens" | python3 -m json.tool

# 7.4 Worker 状态诊断
curl -s "$BASE/api/v1/admin/worker-status" | python3 -m json.tool

# 7.5 热重载配置（修改 YAML 后调用）
curl -s -X POST "$BASE/api/v1/admin/reload-config" | python3 -m json.tool
```

## 8. 模板管理

```bash
# 8.1 创建模板
curl -s -X POST "$BASE/api/v1/templates" \
  -H "Content-Type: application/json" \
  -d '{
    "template_key": "test_summary",
    "purpose": "测试摘要模板",
    "system_prompt": "你是摘要专家",
    "user_prompt_template": "请摘要以下内容：{{content}}",
    "expected_output_type": "text"
  }' | python3 -m json.tool

# 8.2 列出模板
curl -s "$BASE/api/v1/templates" | python3 -m json.tool

# 8.3 用模板提交任务（template_key 方式）
curl -s -X POST "$BASE/api/v1/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "caller_service": "test",
    "pipeline_stage": "test_template",
    "template_key": "test_summary",
    "input": {"content": "5G核心网采用SBA架构，支持网络切片和边缘计算"},
    "expected_output_type": "text"
  }' | python3 -m json.tool
```

---

## 常见错误排查

| HTTP 状态码 | 含义 | 可能原因 |
|------------|------|---------|
| 200 | 成功 | — |
| 400 | 请求参数错误 | 缺少必填字段、pipeline_stage 格式不对 |
| 404 | 任务/资源不存在 | task_id 错误 |
| 429 | 上游限速 | LLM provider rate limit |
| 502 | 上游错误 | provider 超时/连接失败/服务端错误 |
| 503 | 服务未配置 | embedding/rerank 的 api_key 为空 |
