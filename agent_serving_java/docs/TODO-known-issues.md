# TODO / 已知问题（serving）

本文件记录 `agent_serving_java` 中已确认但尚未修复的问题，供后续排期修复。

---

## [待修复] 语义缓存污染：降级/空结果被缓存，服务恢复后仍返回空

- **严重级别**：中高（影响检索正确性，静默返回空结果，用户无感知）
- **状态**：待修复
- **发现日期**：2026-07-22
- **涉及服务**：`agent_serving_java`（serving 检索线）

### 现象

llm_service 抖动/部分不可用期间，用户检索问题 A 返回**空结果**，该空结果被写入 `serving_query_cache`。llm_service 恢复后，在 **24 小时内、且该域 `release_id` 未变**时再次检索 A，会**直接命中缓存返回空结果**，跳过已经恢复的检索链路。

### 根因

缓存的读写只判断 `queryEmbedding == null`，**不判断检索结果是否为空、是否降级**：

- `src/main/java/com/coremasterkb/serving/application/SearchService.java:394-395`
  —— stage 9.5 `semanticCache.store(...)` 对空 `pack` 无任何 guard，空结果照样写入。
- `src/main/java/com/coremasterkb/serving/application/SemanticCacheService.java:57-68`
  —— `store()` 仅在 `queryVector == null` 时早退。
- 命中条件（`src/main/resources/mapper/SemanticCacheMapper.xml` 的 `findNearest`）：
  同 `domain` + 同 `release_id`、`expires_at > now()`、余弦相似度 ≥ `0.92`
  （`SemanticCacheService.HIT_THRESHOLD`）。同一 query 向量相似度 ≈ 1.0，必然命中。

缓存失效目前仅靠两条被动路径：

1. `release_id` 变更（发布新 release 后旧行自然对不上）；
2. 24h TTL（`SemanticCacheMapper.xml` 的 insert 写死 `now() + INTERVAL '24 hours'`）。

`SemanticCacheService.evict(domain)` 已实现，但**没有任何调用点**，未接线。

### 触发边界（关键）

- **llm_service 完全挂**（embedding 接口也不可用）→ **不触发**：
  `EmbeddingClient.embedHyDE()`（`EmbeddingClient.java:82`）兜底调用 `embed(query)`（第 102 行），
  仍走 llm_service 的 embedding 接口 → 抛异常 → 被 `SearchService.java:168-170` catch 返回 null
  → `queryEmbedding == null` → `store()` 早退，**不写缓存**，无 bug。
- **embedding 接口可用、但整体结果为空** → **触发**：
  例如 query understanding 降级为规则兜底、rerank 降级、或该 release 当时确实检索不到内容。
  此时 `queryEmbedding != null`，空 `pack` 被写入缓存。

### 建议修法

1. **主修**：在 `SemanticCacheService.store()`（或 `SearchService.java:395` 调用点）增加 guard——
   **空结果不写缓存**（`pack` 中无 `seed` 角色 item 时跳过）。
   进一步可在**任一 LLM 阶段降级**时也不写（`understanding.source() == fallback`，或 rerank 走了兜底）。
2. **兜底增强**：把 `SemanticCacheService.evict(domain)` 接到发布新 release 钩子，
   内容变更即清旧缓存（顺带解决旧 release 死行堆积）。
3. **可选**：为空/降级场景缩短 TTL，或增加「降级期禁写缓存」开关。

### 复现思路

1. 令 llm_service 的 chat/QU 阶段降级但保持 embedding 接口可用（或构造一个当前 release 检索不到内容的 query）。
2. 调 `POST /api/v1/search` 查询 A → 返回空 → 确认 `serving_query_cache` 出现该行。
3. 恢复 llm_service，24h 内重查 A → 观察是否命中缓存返回空（可看 trace 的 `semantic_cache` stage `hit=true`）。
