# 检索范式 → Agent 零手工上线：开发计划

设计与理由见 `docs/mcp-dynamic-paradigm-tools-design.md`，本文只讲**怎么落地**：拆几个 PR、每个 PR 动哪些文件、验收判据、怎么验证、怎么部署、怎么回滚。

流程遵循 `docs/开发与发布流程.md`：从最新 master 切分支、按工作面分批提交、PR 用 **Create a merge commit（不 squash）**。

---

## 总览

| PR | 分支 | 内容 | 依赖 | 估时 | 部署方式 |
|---|---|---|---|---|---|
| **PR1** | `refactor/paradigm-graphs` | 抽 `ParadigmGraphs`，消除已漂移的重复 | 无 | 0.5 天 | 随 PR2 一起构建 |
| **PR2** | `feat/paradigm-mcp-catalog` | serving 的 `/mcp-catalog` 端点 | PR1 | 1 天 | **镜像重建**（Java jar 烤进镜像） |
| **PR3** | `feat/mcp-paradigm-selection` | mcp_server 两个可选参数 + 候选清单 | PR2 已部署 | 1 天 | `supervisorctl restart mcp`（bind-mount） |
| **PR4** | `feat/paradigm-agent-visibility` | 前端「Agent 可见」列 | PR2 已部署 | 0.5 天 | **镜像重建**（dist 烤进镜像） |

**PR3 与 PR4 互不依赖，可并行。** 部署上 PR3 最轻（只重启），PR4 与 PR2 可以攒成同一次镜像构建。

**建议先做 PR1**：它独立于整个方案、行为可证明中性、半天就能合，先把重复代码这笔债还掉。

---

## PR1 — `refactor/paradigm-graphs`

### 动的文件

**新增**
```
agent_serving_java/src/main/java/com/coremasterkb/serving/operator/paradigm/ParadigmGraphs.java
agent_serving_java/src/test/java/com/coremasterkb/serving/operator/paradigm/ParadigmGraphsTest.java
```

**修改**
```
operator/paradigm/ParadigmBindingService.java   删 extractKbIds / validateServable，改调 ParadigmGraphs
application/ScopeResolver.java                  删 kbIdsOfParadigm，改调 ParadigmGraphs
```

### 内容

`ParadigmGraphs` 两个静态方法：

- `kbIdsOf(JsonNode graph)` — 遍历 `nodes[]` 取 `operatorType == "scope_resolve"` 的 `params.kbIds`，末尾 `ActiveScope.normalizeKbIds`。
- `isServable(JsonNode graph)` — `graph.output.slot` 是否为 `"contextPack"`。

Javadoc 要写明：**这是唯一实现，绑定校验 / 范围解析 / catalog 三处共用**；两处对"什么叫可服务"的定义一旦分叉，就会出现"绑定得上但 catalog 里没有"这种自相矛盾的状态。

### 为什么行为中性

`ScopeResolver` 原来没做归一化，合并后会做。但它把结果原样交给 `KbAccessService.authorize`，而后者第一行就是 `ActiveScope.normalizeKbIds(requestedKbIds)`（`KbAccessService.java:51`）——归一化早做晚做，`authorize` 看到的输入完全相同。

### 验收

- `ParadigmGraphsTest` 覆盖：多个 `scope_resolve` 节点合并、去重、去空白、排序、无 `nodes` / 无 `params` / `kbIds` 非数组等畸形输入；`isServable` 对 `assemble` / `collect` / 缺 `output` 三种图。
- **既有测试全绿且一个都不用改**——需要改既有测试就说明行为变了，停下来查。
  - `ParadigmBindingServiceTest`、`ScopeResolverTest`、`ScopeResolveOperatorTest`

### 验证命令

```bash
cd agent_serving_java
rm -rf target/classes target/test-classes && mvn -o test
```

> 必须删目录强制全量重编：改了方法签名后 Maven 增量编译会 `Nothing to compile` 跳过测试源码，BUILD SUCCESS 是假的。

### 提交批次

一个 commit 就够（`refactor:` 类型），message 正文写清"两份实现已漂移：归一化只有 `ParadigmBindingService` 那侧有"。

---

## PR2 — `feat/paradigm-mcp-catalog`

### 动的文件

**新增**
```
operator/paradigm/ParadigmCatalogService.java              编排 + 三道可见性规则
operator/paradigm/ParadigmCatalogView.java（或用 Map）      响应视图
src/test/.../api/ParadigmMcpCatalogWebMvcTest.java
src/test/.../operator/paradigm/ParadigmCatalogServiceTest.java
src/test/.../operator/paradigm/ParadigmCatalogIT.java      @Tag("pg-integration")
```

**修改**
```
operator/api/ParadigmController.java    加 @GetMapping("/mcp-catalog")，位置在 /{id} 之上
```

无 DDL、无 mapper 改动——`selectPublished()` 已够用。

### 实现顺序（先写编排骨架，再填规则）

**① 先把 §4.2 的连接编排写对**，这是本 PR 唯一会静默出错的地方：

```
第 1 段  无 DomainContext：一次性读全部已发布范式 + 各自版本图     （控制库）
第 2 段  按 domain 分组，逐组 getDataSource(d) → set(d) → 批量 authorize → clear  （域库）
第 3 段  无 DomainContext：组装响应
```

**严禁在循环里交错两类读。** 类上写一段 Javadoc 引用 `ParadigmBindingService` 的同款说明——那里已经把"带着 DomainContext 读控制库表在合库生产上不会报错、只会静默读错库"这件事写清楚了。

**② 再填三道可见性规则**（已发布 / 可服务 / 匿名可读），用 PR1 的 `ParadigmGraphs`。

**③ 最后加两个降级**：
- 某域库不可达（`getDataSource` 抛 `domain_database_unavailable`）→ 该组全部进 `hidden`（`domain_unavailable`），**其它域照常返回**。
- `hidden` 块仅在请求带 `X-KB-User` 时返回，`details` 只列该用户可见的库。

### 验收

| 判据 | 怎么验 |
|---|---|
| 路由不被 `/{id}` 吞 | `ParadigmMcpCatalogWebMvcTest`，仿 `ParadigmResolveWebMvcTest` |
| 三道规则分类正确 | 各造一个范式，断言进 `paradigms` 还是 `hidden` + reason |
| **连接编排正确** | 注入 `DomainContext` 探针，断言读 `operator_paradigm` 时上下文为空、`authorize` 时等于该域 |
| 域不可达降级 | 把某域 `database:` 指向连不上的地址，断言只有该域进 `hidden` |
| `hidden` 鉴权 | 不带 `X-KB-User` 时整块缺席；带头时 `details` 只含可见库 |
| 匿名可读性 | IT：private / public KB 各一，只有 public 的进 `paradigms` |

> **连接编排那条测试是本 PR 最重要的产出。** 没有它，那个 bug 在合库的生产环境里永远不会被发现——四个域现在都指向同一个 `kb_db`。

### 验证命令

```bash
cd agent_serving_java
rm -rf target/classes target/test-classes && mvn -o test    # L1
mvn verify -Dtest=ParadigmCatalogIT                          # L2，需 PG
```

手工验：
```bash
curl "http://localhost:8081/api/v1/paradigm/mcp-catalog?domain=cloud_core_network"
curl -H "X-KB-User: admin" "http://localhost:8081/api/v1/paradigm/mcp-catalog"   # 带 hidden
```

### 提交批次

拆三批，对应上面的实现顺序：`feat:` 编排骨架 + 端点 → `feat:` 三道可见性规则 → `feat:` 降级与 `hidden` 鉴权。

### 部署

Java jar 烤进镜像、**无 volume 挂载**，必须 `bash deploy-build.sh` + 重新部署。

---

## PR3 — `feat/mcp-paradigm-selection`

**开工前提**：PR2 已部署到目标环境，`GET /api/v1/paradigm/mcp-catalog` 可访问。

### 动的文件

```
mcp_server/schemas.py     SearchInput 加 paradigm；FullTextInput 加 paradigm_id
mcp_server/client.py      catalog 拉取 + TTL 缓存 + 解析 + 四条边界 + available_paradigms + selected_by
mcp_server/server.py      两个 tool 的签名与 docstring；instructions 补两句
mcp_server/README.md      更新（顺手修掉底部那张列着已注释 health_check 的过期表）
mcp_server/tests/test_catalog.py            新增
mcp_server/tests/test_client_routing.py     扩充
mcp_server/tests/test_client_fulltext.py    扩充
```

### 实现顺序

**① 先写回归保护再动代码。** 在 `test_client_routing.py` 里加一条：不传 `paradigm` 时，请求 URL 与 payload 与今天**逐字一致**。这条先绿，后面才敢改 `client.py`。

**② catalog 拉取 + TTL 缓存**（`MCP_CATALOG_TTL` 默认 30s，`MCP_CATALOG_TIMEOUT` 默认 5s）。拉取失败静默降级，绝不抛。

**③ `search_knowledge` 的 `paradigm` 解析**，含四条边界：

| 边界 | 行为 |
|---|---|
| 缓存陈旧 | 判 `unknown_paradigm` **前强制刷新一次** |
| catalog 不可用 | 传 id（`pd-` 前缀）→ 直通；传 name → `catalog_unavailable`。**都不回落域默认** |
| 域不匹配 | `paradigm_domain_mismatch`，消息里写清两个域 |
| id 指向 `hidden` 范式 | `paradigm_not_available` |

**④ `available_paradigms` 挂到所有返回路径**——**包括每一条错误路径**。你们的域没有 active release，不指定范式的检索会直接 `no_active_release` 失败；只挂成功路径的话 agent 第一次撞墙就没线索了。清单按本次 `domain` 过滤。

**⑤ `get_segment_fulltext` 的 `paradigm_id`**：显式传了就用，没传才走今天的域 resolve。serving 侧零改动。

**⑥ 最后改 instructions 和 docstring**，保持简短——每轮都在上下文里。

### 验收

- 新增：`unknown_paradigm` 不回落；四条边界各一条；清单按 domain 过滤；**错误信封也带清单**。
- 回归：不传 `paradigm` 时行为逐字不变（第 ① 步那条）。
- fulltext：显式 `paradigm_id` 优先于域 resolve。

### 验证命令

```bash
python -m pytest mcp_server/tests/ -q      # 全部 httpx.MockTransport，不连后端
```

端到端（需 PR2 已部署）：新建范式 → 发布 → **不做任何其它操作** → 调 `search_knowledge` → 响应里出现它 → 按中文名指名调用 → `_retrieval.paradigm_id` 正确、`selected_by=explicit` → 用返回的 ids + `paradigm_id` 调 `get_segment_fulltext` 全部命中。

### 提交批次

`test:` 回归保护 → `feat:` catalog 缓存与解析 → `feat:` 候选清单与 selected_by → `docs:` README 与 instructions。

### 部署与回滚

`mcp_server` 是 bind-mount 的，改完宿主机文件 `docker compose exec app supervisorctl restart mcp` 即生效。

回滚：不传 `paradigm` 就是今天的行为，所以**不需要专门的开关**；真要一键停用，`MCP_CATALOG_TTL` 设成极大值只会影响提示新鲜度，要彻底停就回滚代码重启（30 秒内完成）。

---

## PR4 — `feat/paradigm-agent-visibility`

**开工前提**：同 PR3，PR2 已部署。

### 动的文件

```
kb-ui/src/types/operator.ts                     catalog 响应类型
kb-ui/src/api/operator.ts                       fetchMcpCatalog()，走已有的 serving 代理
kb-ui/src/views/paradigm/ParadigmListView.vue   新增「Agent 可见」列
（描述输入框所在处）                              标签改写 + 示例
```

### 内容

新增一列，五种状态：

| 状态 | 显示 |
|---|---|
| 在 `paradigms` 里 | 绿标「Agent 可见」；`isDomainDefault` 时补「默认」标 |
| `not_servable` | 灰标「不可见」+ tooltip |
| `kb_not_anonymously_readable` | 橙标「不可见」+ tooltip 列库名 |
| `unbound_kb_scope` | 橙标「不可见」+ tooltip |
| 未发布 | 「—」，不查 |

**文案直接复用 `ParadigmListView.vue:315-326` 的 `bindErrMsg`**——同一个原因在绑定对话框和列表里必须说同一句话，否则运营会以为是两回事。抽成一个共用的 `reasonText()`。

描述输入框的 label 改成「**给 AI 看的说明：什么问题该用这个范式**」并给一行示例——agent 选不选它完全取决于这段话。

### 验收

- 五种状态渲染正确。
- **catalog 请求失败时该列降级成「—」，不阻塞整个列表**（列表本身不依赖它）。
- 请求带 `X-KB-User`（否则拿不到 `hidden`）。

### 验证命令

```bash
npm run build --prefix kb-ui               # vue-tsc + vite，全绿才能合
cd kb-ui && npx vitest run --reporter=dot
```

> Node 用 v24。提交前 `git status` 确认没带上 `components.d.ts` 之类的 IDE 噪声。

### 部署

前端 dist 烤进镜像、无 volume 挂载，必须重新构建部署。可与 PR2 攒成同一次构建。

---

## 收尾（可并入 PR3/PR4，或单独一个 `docs:` PR）

- `CLAUDE.md`：补一节 catalog 端点 + MCP 的范式选择；把 mcp_server 那条注意事项里的工具表更新。
- `docs/mcp-paradigm-routing-design.md`：它写的"保持单工具 `search_knowledge`，签名不变"已被本方案取代，加一行指向新设计。
- `docs/mcp-dynamic-paradigm-tools-design.md`：状态从"设计中"改成"已落地"，并把实现中发现的偏差回写。

---

## 观察期（上线后，不写代码）

跑一段时间看 `selected_by` 的分布，再决定下一步方向（见设计文档 §7）：

- agent 常 `explicit` 且效果更好 → 这个选择该给它，可考虑把个别高频范式升格为一等具名工具。
- agent 几乎不 `explicit`，或 `explicit` 反而更差 → 策略选择本该在服务端，转向"一域 N 个候选 + 路由规则"。

**配套缺口**：范式执行那条路目前不写 `serving_query_logs`（`QueryLogAspect` 的切点是 `SearchService.search(..)`，范式走 `ParadigmExecutionService.run`）。要做上面的判断就得先补上，否则只有 MCP 侧日志、没有服务端口径。这件事**不在本计划的 4 个 PR 里**，需要单独排。

---

## 完成定义（Definition of Done）

- [ ] PR1：`ParadigmGraphs` 是唯一实现，三处调用点全部改指它，既有测试一个没改且全绿
- [ ] PR2：catalog 端点上线，连接编排有测试钉住，域不可达只影响该域
- [ ] PR3：新建并发布一个范式后，**不做任何其它操作**，agent 下一次调用就能看到并按中文名调用它
- [ ] PR3：显式选错范式报错而不静默换引擎；错误响应里也带候选清单
- [ ] PR4：范式列表能看出每个范式对 agent 是否可见、不可见的原因是什么
- [ ] 文档：CLAUDE.md 与两份设计文档同步
