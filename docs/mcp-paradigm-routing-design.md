# MCP ↔ 检索范式打通设计方案

**目标**：发布检索范式后，MCP 无需任何配置改动，自动用上该域对应的范式。

**已定语义**（三个岔路口的选择）：

| 决策点 | 选定 |
|---|---|
| 匹配依据 | **按 `domain` 绑定**，每域至多一个生效默认范式。MCP 调用本来就必填 domain，匹配是确定性的，不依赖 LLM 判断 |
| MCP 工具形态 | **保持单工具 `search_knowledge`**，签名不变，`client.py` 内部换后端。对所有已接入 Agent 完全透明 |
| 老管线 `/api/v1/search` | **保留，仅作回落**。没绑定范式的域行为逐字不变；前端搜索页不受影响 |

---

## 0. 现状：五条必须先知道的事实

这些是设计约束的来源，不是背景介绍。

1. **`operator_paradigm` 表没有任何绑定维度**。没有 `domain`、没有 `kb_id`，DDL 注释明写「paradigms are domain-agnostic global config」，表建在**控制库**（非路由的 `defaultDataSource`）。所以「范式 ↔ 域」这个对应关系目前**根本不存在**，本方案的核心就是补它。
2. **MCP 完全不知道范式存在**。`mcp_server/client.py` 是纯透传：`POST {SERVING_URL}/api/v1/search`，`SERVING_URL` 直连 `localhost:8081`（supervisord 覆盖），**绕过控制面**。
3. **两条执行路径的响应结构不同**。`/api/v1/search` 返回**裸展开**的 ContextPack 字段（`query/items/relations/sources/evidence_groups/...`）；`/api/v1/paradigm/{id}/search` 返回**包了一层**的 `{"contextPack": {...}}` 或 `{"candidates": [...]}`。
4. **查询日志和语义缓存只挂在老管线上**。`QueryLogAspect` 是 AOP 切点 `execution(* SearchService.search(..))`，`SemanticCacheService` 也只在 `SearchService` 内部调用。范式路径（`ParadigmExecutionService.run` → `ParadigmExecutor`）**既不写 `serving_query_logs`、也不过语义缓存**。
5. **范式执行的入参比 MCP 的工具参数窄**。`ParadigmExecutionService.RunArgs` 只有 `{query, domain, channel, debug, username}`；MCP 的 `search_knowledge` 还有 `scope` 和 `entities`。

---

## 1. 数据模型

给 `operator_paradigm` 加三列 + 一个部分唯一索引。新建 `db/operator/002_paradigm_domain_binding.sql`：

```sql
ALTER TABLE operator_paradigm ADD COLUMN IF NOT EXISTS bound_domain VARCHAR(64);
ALTER TABLE operator_paradigm ADD COLUMN IF NOT EXISTS is_default   BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE operator_paradigm ADD COLUMN IF NOT EXISTS bound_at     TIMESTAMP;

-- 「一个域至多一个生效默认范式」——与 asset_publish_releases 的 active 部分唯一索引同构
CREATE UNIQUE INDEX IF NOT EXISTS uq_paradigm_domain_default
    ON operator_paradigm (bound_domain)
    WHERE is_default AND status = 'active' AND bound_domain IS NOT NULL;
```

**语义**：

- `bound_domain IS NULL` = 未绑定。范式照常可以被 `/{id}/search` 显式调用，只是不参与自动匹配。这保住了现有的测试/评测用法（模板里 8 个 `collect` 范式就属于这类）。
- 绑定信息是范式的**可变元数据**，**不进版本快照**。`operator_paradigm_version.graph_json` 保持不可变、只存图——`paradigmId + version` 仍然永远重放出同一结果。改绑定不产生新版本。
- 部分唯一索引里带 `status='active'`，所以 archive 一个范式会自动让出该域的默认位。

> ⚠️ **`ParadigmSchemaInitializer` 只 `addScript` 了 `001`**，且 `setContinueOnError(false)`。加 `002` 必须在 initializer 里**显式追加一行**，否则新列永远不会建、所有绑定接口在生产上报 `column does not exist`。这与 `pg_schema.py` 按显式常量列表加载是同一套路——**不存在按目录扫描**。

---

## 2. 服务端（agent_serving_java）

### 2.1 绑定端点

```
PUT  /api/v1/paradigm/{id}/binding    body: {"domain": "odn", "isDefault": true}
DELETE /api/v1/paradigm/{id}/binding                     # 解绑
```

同时让 `POST /{id}/publish` 的 body 可选接 `{"domain": "...", "setDefault": true}`——「发布即生效」最贴合需求原话，但绑定端点独立存在，便于先发布后绑定 / 换绑不重发。

**绑定时必须做的四项校验**（全部前置，不要留到运行时）：

| 校验 | 失败错误码 | 为什么必须在绑定时挡 |
|---|---|---|
| `domain` 存在于 `DomainRegistry` | `unknown_domain` | 否则打字错的域名静默绑定成功，MCP 永远匹配不到，排查起来毫无线索 |
| 范式已发布（`current_version >= 1` 且 `status='active'`） | `paradigm_not_published` | 绑定一个 draft 等于绑定了空 |
| **终点算子必须是 `assemble`** | `paradigm_not_servable` | 见 §4.1 |
| **`scope_resolve.kbIds` 引用的 KB 必须全部 `public`** | `paradigm_requires_identity` | 见 §4.2 |

### 2.2 切换默认必须先清后设，且同事务

```java
@Transactional
public ParadigmEntity bind(String id, String domain, boolean isDefault) {
    // ...校验...
    if (isDefault) {
        paradigmMapper.clearDefaultForDomain(domain);   // 必须先执行
    }
    paradigmMapper.updateBinding(id, domain, isDefault);
}
```

顺序反了会直接撞 `uq_paradigm_domain_default` 报 23505。这与 `activate_release()` 在同一事务里退旧启新是同一个模式，照抄即可。

### 2.3 解析端点（MCP 唯一新依赖）

```
GET /api/v1/paradigm/resolve?domain=odn
  → 200 {"bound": true,  "paradigmId": "pd-xxx", "name": "...", "version": 3}
  → 200 {"bound": false}                      # 未绑定，不是错误
```

**返回 200 而不是 404**：「这个域没绑范式」是正常状态，不是异常。让 MCP 靠状态码分支会把网络故障和未绑定混为一谈。

查的是控制库单行，走非路由 `defaultDataSource`，**不需要 `DomainContext`**（和现有所有范式 CRUD 一致）。

### 2.4 不做的事

- 不动 `/api/v1/search` 一行代码。
- 不改 `ParadigmExecutor` / 编译器 / 算子。
- 不给 `operator_paradigm_version` 加任何列。

---

## 3. MCP 侧（mcp_server）

### 3.1 client.py 的新流程

```python
def search_knowledge(inp: SearchInput) -> dict:
    if PARADIGM_ROUTING:
        target = _resolve_paradigm(inp.domain)      # GET /paradigm/resolve
        if target:
            return _run_paradigm(target, inp)       # POST /paradigm/{id}/search
    return _legacy_search(inp)                      # POST /api/v1/search（原逻辑）
```

**不做缓存**。resolve 是控制库单行查询，MCP 与 serving 同容器走 localhost，相对 `SEARCH_TIMEOUT=120s` 完全可忽略。换来的是「发布后立即生效」这个需求原话里的核心承诺——加了 TTL 缓存就得解释「多久生效」，不值得。

`_resolve_paradigm` **失败必须返回 None 而不是抛异常**：resolve 挂了应该降级到老管线继续可用，而不是让整个检索不可用。但**要 `logger.warning`**——这个仓库已经有 `LlmClient.submit_task` 失败返 `None` 导致整条链路静默 no-op 的前科，降级可以静默对用户，不可以静默对日志。

### 3.2 响应归一化（必做，否则 Agent 会拿到两种结构）

`_run_paradigm` 必须把 `{"contextPack": {...}}` **拆平**成老管线那种裸字段形状（`query/items/relations/sources/evidence_groups/issues/suggestions`），让 tool 的返回契约恒定。

同时注入一个 `_retrieval` 元信息块，便于排查「这次到底走了哪条路」：

```json
"_retrieval": {"engine": "paradigm", "paradigm_id": "pd-xxx", "name": "...", "version": 3}
"_retrieval": {"engine": "legacy"}
```

### 3.3 配置与回退

```
MCP_PARADIGM_ROUTING=1     # 默认开；置 0 一键回到纯透传
```

与 `MINING_RUN_SUBMISSION_ENGINE=legacy` 同构的一键回退开关。第二道回退是业务侧的：把该域的 `is_default` 取消，MCP 下一次调用自动回落，**不需要重启任何服务**。

### 3.4 README 需要改

`mcp_server/README.md` 现在写「**纯透传**：不做任何语义判断或加工」和「2 个 tool」（实际只暴露 1 个，`health_check` 被注释掉了）。加了路由和响应归一化后，「纯透传」不再成立，必须改，否则又是一份和源码冲突的文档。

---

## 4. 五个必须处理的坑

### 4.1 绑定为域默认的范式，终点算子必须是 `assemble`

`collect` 是**给测试系统算 recall/MRR/NDCG 用的**——它返回的是裸候选（id/score/source/metadata），没有经过 `ContextAssembler` 的源文档下钻、图扩展、证据角色分组、压缩。喂给 Agent 质量会显著劣于老管线，而且结构对不上。

在绑定时校验 `graph.output.slot == "contextPack"`，报 `paradigm_not_servable`。**把问题挡在发布时，而不是让它在 MCP 运行时变成一个结构诡异的响应。**

### 4.2 MCP 是匿名调用，绑定的范式不能引用私有 KB

MCP 不发 `X-KB-User`（`client.py` 里根本没这个头）。`KnowledgeBaseMapper.selectAccessibleKbIds` 的 `LEFT JOIN kb_users` 在 username 为 null 时只留 `visibility='public'` 的 KB。

后果：如果绑定的范式里 `scope_resolve.kbIds` 指向了 private/shared KB，`KbAccessService.authorize()` 会抛 `kb_not_found`，**该域的 MCP 检索 100% 失败**。而且错误信息刻意不告诉你是哪个 KB（防存在性泄露），排查会很痛苦。

两个选项，建议先做前者：

- **A（推荐，本期）**：绑定时校验 kbIds 全为 public，否则拒绝并明确报 `paradigm_requires_identity`，错误详情里列出违规的 kb 名称（这是管理面操作，不是检索面，不涉及泄露）。
- **B（后续）**：给 MCP 配服务账号身份（`MCP_KB_USER` 环境变量 + 在 `kb_users` 建一个只读账号，按 `kb_members` 授权）。等真有「MCP 要读私有库」的需求再做。

### 4.3 切过去会丢查询日志和语义缓存

`QueryLogAspect` 只切 `SearchService.search()`，`SemanticCacheService` 也只在 `SearchService` 内被调用。MCP 一旦走范式路径：

- `serving_query_logs` 不再有 MCP 的查询记录——**线上问题排查和效果分析会突然出现盲区**
- 不过语义缓存——**重复查询的延迟会上升**（但反过来说，也天然绕开了 `docs/TODO-known-issues.md` 里那个「降级期空结果被缓存、恢复后仍命中返空」的污染 bug）

处理建议，按性价比排序：

1. **本期必做**：把 `QueryLogAspect` 的切点扩到 `ParadigmExecutionService.run(..)`，或在 `ParadigmExecutionService` 里显式调 `QueryLogService.record()`。日志盲区不能接受。注意 `record()` 的签名吃 `SearchRequest` + `ContextPack`，范式路径需要构造等价对象。
2. **本期不做**：语义缓存。范式路径接缓存需要 query 向量（只有图里有 `query_embed` 节点才有）、且缓存键要带 `paradigmId+version`（否则改范式后旧结果仍命中），复杂度不低。先在方案里明确记录「范式路径无语义缓存」，别让它成为下一个没人知道的行为差异。

### 4.4 `scope` / `entities` 是死参数——两条路径都不读（实施时更正）

> **本节原先的判断是错的，已按核查结果重写。** 原文说「切到范式路径会丢失 `scope`/`entities`」，前提是老管线在用它们。实际不是。

核查结论：`SearchRequest` 的 **`scope` / `entities` / `mode` 三个字段在整个 main 树里从未被读过**。

- `SearchService` 只读 5 个字段：`query` / `domain` / `channel` / `debug` / `kbIds`。
- 查询理解是 `quEngine.understand(request.query(), profile)`（`SearchService.java:184`）——**只传 query 字符串**，请求体里的实体和 scope 根本没有入口。
- 检索器里的 `query.scope()` / `query.entities()`（`FtsRetriever`、`EntityExactRetriever`、`DenseVectorRetriever`、`EntityGraphRouteRetriever`）全部是 **`QueryUnderstanding`** 的字段，由 LLM/规则自己抽出来，与请求体同名但无关。

所以 MCP 的 `search_knowledge` 对 Agent 暴露 `scope` 和 `entities` 已久，它们**一直什么也没做**；切到范式路径**不构成回退**。

**决定：不把它们塞进 `RunArgs`。** 在新路径上复制一份同样的死参数，正是本文档反对的那件事。改为在 MCP 侧显式化（并入 §3 / 第 6 步）：

- 保留参数（删掉会让传参的既有 Agent 调用直接报错，而这些参数本来无害），但**一旦调用方传了非空值**，在响应的 `_retrieval.ignored_args` 里回报，并 `logger.warning`。
- `search_knowledge` 的 docstring 写明这两个参数当前不影响检索。

**真正实现它们**（把调用方 entities 合并进 `QueryUnderstanding`、scope 下推成 `scope_json` 过滤）是一个独立的功能项，会改变现有检索行为，不属于本次打通的范围。

### 4.5 `DomainContext` 与虚拟线程

范式路径的 `ParadigmExecutor` 每节点都已经正确 `wrapRunnable`，这条**不是新增风险**。但 §2.3 的 resolve 端点走的是**非路由**的 `defaultDataSource`（控制库），而执行走**域路由**池——两者不在一个库。加代码时别顺手给 resolve 设 `DomainContext`，会把控制库查询路到域库去。

---

## 5. 前端（kb-ui）

`ParadigmListView.vue` 加：

- 列表列：「绑定域」+「默认」标记
- 行操作：绑定 / 换绑 / 解绑（域下拉取自控制面的域列表，与其他页面一致）
- 发布对话框里加可选的「发布后设为该域默认」勾选

`ParadigmEditorView.vue` 不用改（绑定不是图的一部分）。

一个 UI 上的提醒值得做：当范式终点是 `collect` 时，绑定按钮置灰并提示「仅 `assemble` 终点的范式可绑定为域默认」——把 §4.1 的约束在编辑期就告诉用户，而不是等绑定时报错。

---

## 6. 测试

| 层 | 用例 |
|---|---|
| Java 单测 | 绑定校验四项（unknown_domain / not_published / not_servable / requires_identity）；切换默认的先清后设（**必须有一个「A 是默认 → 把 B 设为默认」的用例**，否则撞唯一索引这个坑测不出来）；archive 让出默认位 |
| Java web 层 | `GET /paradigm/resolve` 的 bound / unbound 两态均返 200 |
| MCP | resolve 命中 → 打范式端点；resolve 未绑定 → 打老端点；resolve 网络失败 → 回落老端点且记 warning；响应归一化后字段与老管线逐键一致 |
| 端到端 | 发布 + 绑定 → MCP 调用立即走新范式（验证「无缓存 = 立即生效」） |

> `knowledge_mining` 那套「库名必须以 `_test` 结尾」的护栏是 Python 侧的；Java 侧集成测试注意 `DomainRoutingIT` 刚重写过（`0092a79`）以切断对生产库的连接，新增 IT 照它的写法来。

---

## 7. 实施顺序

按可独立验证的粒度切，每步都能单独合：

1. **DDL + Initializer 注册** — 加 `002` 并在 `ParadigmSchemaInitializer` 显式 `addScript`。最容易漏，先做。
2. **绑定端点 + 四项校验 + 事务化默认切换**（Java）
3. **resolve 端点**（Java）— 到这里服务端自洽，可用 curl 验证
4. **查询日志覆盖范式路径**（§4.3.1）— 与 5 解耦，先补上避免盲区
5. ~~**`RunArgs` 扩 scope/entities**~~ — **取消**，见 §4.4：这两个参数在任何路径上都从未生效，不存在要防的回退。改为在第 6 步做 `ignored_args` 显式标注。
6. **MCP client 路由 + 响应归一化 + `ignored_args` + 开关**（Python）— bind-mount 生效，`supervisorctl restart mcp` 即可，无需重新 build 镜像
7. **前端绑定 UI**（Vue）— 注意**前端 dist 烤进镜像、无 volume 挂载**，必须 `deploy-build.sh` 重新构建
8. **README / CLAUDE.md 更新**

1–5 是 Java，改完需要 `deploy-build.sh` 重新打包（jar 无挂载）；6 是纯 Python，可热更新。**如果想最快看到效果**，可以先把 1–3 部署上去，第 6 步单独热更 MCP 验证主链路，再补 4/5/7。
