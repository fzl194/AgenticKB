# 检索范式 → Agent 可调用：零手工上线设计方案

**要解决的痛点**：每发布一个检索范式，就要手工在 `mcp_server/server.py` 里加一个 tool 去包装它。

**目标**：范式发布后，agent 无需任何手工步骤即可发现并调用它；运营能在前端看出"这个范式 agent 到底看不看得见"。

**结论**：可行，且**不需要新增任何 MCP 工具**。最终改动是 **2 个可选参数 + 1 个只读端点 + 前端 1 列**。

---

## 0. 现状：八条必须先知道的事实

全部来自当前代码，是设计约束的来源。

1. **范式 id 对 LLM 毫无信息量。** `ParadigmService.java:54`：`"pd-" + UUID.randomUUID().toString().substring(0, 8)`，形如 `pd-3f2a1b7c`。

2. **范式 name 是自由文本且已唯一。** `001_operator_paradigm.sql`：`name VARCHAR(200) NOT NULL UNIQUE`，`create()` 还会 `selectByName` 查重报 `paradigm_name_exists`。实际使用中会写中文名。

3. **MCP 工具名不能是中文。** Anthropic / OpenAI 的 tool schema 都要求工具名匹配 `^[a-zA-Z0-9_-]{1,64}$`。**而返回体里的字符串、JSON Schema 的 enum 值都不受此限。** 这条差别是本方案选型的支点。

4. **`search_knowledge` 已经必带 domain。** `server.py:52`，instructions 里还花三行强调"必须显式指定 domain"。而 `ParadigmExecutionService.RunArgs` 执行一个范式只要 `{query, domain, channel?, debug, username}`——**执行所需的上下文，调用方已经全给了**。

5. **范式不需要绑定就能执行。** `POST /api/v1/paradigm/{id}/search` 按 id 直调（`ParadigmController.java:195`）。`bound_domain`/`is_default` 决定的是"该域的默认是谁"，不是"能不能调"。

6. **匿名可读性是真实约束。** MCP 不发 `X-KB-User`，`ScopeResolveOperator.execute` 每次执行都 `authorize(domain, kbIds, null)`，非 public 的 KB 一律 `kb_not_found`。`ParadigmBindingService.validateAnonymouslyReadable` 已在绑定时做了这个检查。

7. **⚠️ 从图里取 kbIds 的逻辑已经有两份，且已漂移。** `ParadigmBindingService.extractKbIds:183` 与 `ScopeResolver.kbIdsOfParadigm:88` 逐字平行，但**前者末尾调了 `ActiveScope.normalizeKbIds` 归一化，后者没有**。本方案需要第三个调用点——先合并再加（§5.1）。

8. **⚠️ 纯 KB 部署下，不指定范式的检索必然失败。** KB 挖掘 `publish=False` 永不产 release，所以没有域级 active release，`/api/v1/search` 与"未绑定范式的域"都会报 `no_active_release`。**这决定了错误路径也必须携带候选清单**（§3.3）——否则 agent 第一次调用就撞墙，且无从得知还有别的选择。

---

## 1. 目标与非目标

**目标**

- 范式发布后零手工：不改代码、不填表单、不重启，agent 即可发现并调用。
- 不传范式时行为与今天**逐字相同**（域默认 → 回落 legacy）。
- 原文下钻落在与检索同一个范式的语料上。
- 前端能看出每个范式对 agent 是否可见，不可见时给出原因。
- 留下可观测的痕迹，以便日后判断"该不该让 agent 做这个选择"（§7）。

**非目标**

- 不做 per-agent 可见性（MCP 匿名，事实 6）。
- 不改范式执行（`ParadigmExecutionService` / `ParadigmExecutor` 零改动）。
- 不新增 MCP 工具、不做动态工具注册（§2）。
- 不新增表、列、迁移、后台线程。

---

## 2. 为什么不是"一范式一工具"

这是最初的直觉方案。读完代码后它不成立，理由三条：

**① 工具名无法自动生成得有用。** 工具名必须是 ASCII 标识符（事实 3），而范式名是中文（事实 2）。从 id 推 → `search_pd_3f2a1b7c`，agent 无法据此选择；从中文名 slug → 结果为空；音译 → 引入分词/拼音依赖且不稳定。**所以只剩"让人填一个英文名"——那是把手工从代码挪到表单，痛点原封不动。**

**② 工具列表变更不保证到达 agent。** MCP 的 `notifications/tools/list_changed` 客户端支持参差，不少客户端只在建连时拉一次 `tools/list`。"发布即可见"在这条路上做不实。

**③ 上下文成本随范式数量线性增长。** 每个工具的 name + description + schema 在**每一轮**都进 prompt。

**本方案把三条全部消掉**：范式名是返回**数据**不是标识符（中文随便写）；返回值永远新鲜，不依赖客户端刷新；工具恒为 2 个。

> 业界对"能力太多"的标准答案是渐进式披露（Anthropic 的 tool search、MCP 的工具过滤）。本方案是它在"能力只有个位数"这个规模下的最简形态——**连独立的发现工具都不需要**（§3.3）。

---

## 3. 方案

```
serving   GET /api/v1/paradigm/mcp-catalog?domain=…     ← 唯一的 Java 新增（只读）
                          │
        ┌─────────────────┴──────────────────┐
        ↓                                    ↓
mcp_server                              kb-ui
  search_knowledge(…, paradigm?)          范式列表新增「Agent 可见」列
  get_segment_fulltext(…, paradigm_id?)   不可见时显示原因
  响应里附 available_paradigms
```

### 3.1 `search_knowledge` 加 `paradigm`

```python
def search_knowledge(query, domain, paradigm: str | None = None,
                     scope=None, entities=None, debug=False)
```

解析优先级，全部在 `client.py`，**Java 侧零改动**：

1. `paradigm` 非空 → 用 catalog 解析成 `paradigm_id`（**接受 name 或 id**：name 是 UNIQUE 的，且 agent 刚从清单里看到的就是它；id 也接受，便于程序化调用）→ `POST /api/v1/paradigm/{id}/search`。
2. **解析不到 → 报错，不猜、不回落**：`{"error":"unknown_paradigm","available":[…]}`。回落会让 agent 以为选中了 A 实际用了 B，而它无从察觉——与 `_search_via_paradigm` 执行失败不回落 legacy 是同一条原则。
3. `paradigm` 为空 → **今天的路径一字不变**：`_resolve_paradigm(domain)` → 域默认 → 无绑定则 legacy。

四条边界，都会真实发生：

- **缓存陈旧导致刚发布的范式"不存在"。** TTL 最长 30 秒，而"发布即可用"是本方案的核心承诺。所以在判定 `unknown_paradigm` **之前必须强制刷新一次缓存**，刷新后仍找不到才报错。少了这一步，用户发布完立刻试就会被告知没有这个范式——正是最伤信任的那一次体验。
- **catalog 拉不到时不能一刀切。** 传的是 **id**（`pd-` 前缀可识别）→ 直接透传给 `/{id}/search`，不需要 catalog；传的是 **name** → 无法解析，报 `catalog_unavailable`（一个与 `unknown_paradigm` **不同**的错误码，前者是"暂时查不了"、后者是"确实没有"，agent 的应对不同）。**两种情况都不回落域默认**——静默换引擎正是要避免的。
- **域与范式不匹配要在 MCP 侧拦掉。** catalog 里该范式的 `domain` 是 `odn`，而调用方传的 `domain` 是 `cloud_core_network`：执行下去会在 `scope_resolve` 处因 kb id 跨域而报 `kb_not_found`（`selectAccessibleKbIds` 按 `kb.domain` 过滤），agent 拿到的是一个指向知识库权限的错误，与真实原因相差甚远。**在 MCP 侧比对并报 `paradigm_domain_mismatch`**，把两个域名字都写进消息里。
- **执行前用 catalog 校验 id。** 显式传的 id 可能指向一个在 `hidden` 里的范式（agent 从别处看到的、或旧会话里的）。serving 侧执行时仍会拦住（`ScopeResolveOperator` 每次都 `authorize`，非 public KB 报 `kb_not_found`；`collect` 终点的范式会返回 `candidates` 而 `_normalize_paradigm_body` 已有告警分支），所以**不是安全漏洞**，但错误信息很难懂。MCP 侧先比对 catalog，直接报 `paradigm_not_available` 更清楚。

### 3.2 `get_segment_fulltext` 加 `paradigm_id`

**不加就会静默出错。** 今天它按 domain 重新 resolve（`client.py:115`）；一旦检索走的是显式指定的范式，重新 resolve 会解析到另一个范式、另一批 KB，所有 id 报 `found:false`——而这个 reason 的语义是"内容被重新挖掘或库不可见"，agent 会照着它解释，察觉不到是范围配错了。

- `FullTextInput` 加 `paradigm_id: str | None = None`；显式传了就用，没传才走今天的域 resolve 回落。
- docstring 明写："把上一次 `search_knowledge` 结果里 `_retrieval.paradigm_id` 的值原样传回来。"
- **serving 侧零改动**——`FullTextRequest.paradigmId` 已带 `@JsonAlias("paradigm_id")`，`ScopeResolver` 已会从范式图读 `kbIds` 并照常鉴权。
- 别同时传 `kbIds`（会被 `conflicting_scope_source` 拒）。MCP 从不发 `kbIds`，天然安全。

**两个参数的名字不一致是刻意的，别"顺手统一"**：`search_knowledge` 的 `paradigm` 接受 name 或 id，因为那是**人/模型做的选择**，名字才有意义；`get_segment_fulltext` 的 `paradigm_id` 只接受 id，因为那是**机器的原样回传**，多一种取值就多一次解析失败的机会。名字本身就在提示这个差别。

### 3.3 发现是副产品，不是一个工具

每次 `search_knowledge` 的响应都附带精简清单：

```json
"_retrieval": {
  "engine": "paradigm",
  "paradigm_id": "pd-3f2a1b7c",
  "selected_by": "domain_default",
  "available_paradigms": [{"name": "ODN 拓扑排障", "description": "…"}, …]
}
```

于是流程变成：agent 直接检索 → 结果顺带告诉它还有哪些范式 → 不满意时第二次调用指名换一个。**不需要独立的发现工具，也不需要 agent 记得"先调发现"。**

**这份清单必须按本次请求的 `domain` 过滤**（只保留 `domain` 等于它、或为 `null` 的），否则 agent 会看到别的域的范式并选中它，然后撞上 `paradigm_domain_mismatch`。给出一个用不了的选项，比不给更糟。

**`domain: null` 那一类（图里没有 kbIds）在纯 KB 部署下实际不可用**——它们走域级 active release，而 KB 挖掘不产 release（事实 8）。当前先照常列出（它们在别的部署形态下是有效的）；如果实测发现 agent 老是选中它们然后失败，就在 MCP 侧按"该域有没有 active release"再筛一道。

**⚠️ 错误信封也必须带这份清单。** 事实 8：你们的域没有 active release，不指定范式的检索会直接 `no_active_release` 失败。如果只在成功响应里附清单，agent 第一次撞墙后就没有任何线索了。所以 `_search_via_legacy` 和 `_search_via_paradigm` 的**每一条错误返回路径**都要带上它——这是本方案在你们当前部署形态下能否自洽的关键。

实现约束：清单来自**短 TTL 缓存**（`MCP_CATALOG_TTL`，默认 30 秒），**拉取失败就静默省略这个字段**。它是提示信息，绝不能成为检索变慢或失败的原因。

> 与 `_resolve_paradigm` 刻意**不**缓存的差别：后者决定"这次用哪个引擎"，陈旧会导致行为错误；前者只是提示，30 秒陈旧最多让 agent 晚半分钟知道有个新范式。

### 3.4 `selected_by` 是为了日后能做决定

三个取值：`explicit`（agent 指名）/ `domain_default`（吃了绑定）/ `fallback`（无绑定走 legacy）。

它不只是调试字段。**跑一段时间后，它回答的是"该不该让 agent 做这个选择"**：如果 agent 几乎从不 `explicit`，或 `explicit` 的结果反而更差，那就说明检索策略的选择本该留在服务端（§7）。没有这个字段，这个判断只能靠猜。

### 3.5 instructions 要改

`server.py:23` 那段现在假定只有一条检索路径。补两句即可，**必须简短**（每轮都在上下文里）：多范式的存在、以及"不确定就先不传，看 `_retrieval.available_paradigms` 再决定第二次"。

---

## 4. serving 侧：唯一的新端点

```
GET /api/v1/paradigm/mcp-catalog?domain={可选}
→ {
    "paradigms": [
      {"id":"pd-3f2a1b7c","name":"ODN 拓扑排障","description":"…",
       "domain":"odn","version":3,"isDomainDefault":true}
    ],
    "hidden": [
      {"id":"pd-9c11ab02","name":"评测基线","reason":"not_servable"},
      {"id":"pd-77de01f4","name":"内部资料检索","reason":"kb_not_anonymously_readable",
       "details":["kb-3f2a…"]}
    ]
  }
```

**必须映射在 `/{id}` 之上。** `ParadigmController.java:76-78` 已经为 `/resolve` 记录过这条：Spring 的 pattern comparator 里字面量段优先于路径变量，否则会被当作 `id="mcp-catalog"` 吞掉。新端点同理，并同样用 WebMvc 测试钉住。

### 4.1 可见性规则（三道，全部复用已有逻辑）

| 条件 | 复用 | 不满足时 |
|---|---|---|
| 已发布 | `selectPublished()` 的谓词 `status='active' AND current_version>=1` | 不出现在任何列表 |
| 可服务 | `validateServable`：出口 slot 必须是 `contextPack` | `hidden`，`not_servable` |
| 匿名可读 | `KbAccessService.authorize(domain, kbIds, null)` | `hidden`，`kb_not_anonymously_readable` + `details` 列库名 |

匿名可读性检查需要 domain（`selectAccessibleKbIds` 按 `kb.domain` 过滤），于是分三种情况：

- **图里没有 kbIds** → 走域级 release，任何域可跑 → 列出，`domain: null`（执行时以调用方传的 domain 为准）。
- **有 kbIds 且已绑定域** → 用 `bound_domain` 查 → 通过则列出并带上该 domain。
- **有 kbIds 但未绑定域** → **无法核验**（kb id 域内唯一，脱离域无从判断）→ `hidden`，`unbound_kb_scope`，提示"绑定一个域即可"。

### 4.2 ⚠️ 连接编排：两类读必须分段，不能交错

**这是本端点最容易写错、且错了不会报错的地方。**

catalog 要读两类数据，它们在**不同的库**上：

| 读什么 | 在哪个库 | 对 `DomainContext` 的要求 |
|---|---|---|
| `operator_paradigm` / `operator_paradigm_version` | **控制库**（非路由的 `defaultDataSource`） | 必须**清空** |
| `knowledge_bases` / `kb_users` / `kb_members`（匿名可读性校验） | **域库**（按 domain 路由） | 必须**设为该 domain** |

`ParadigmMapper` 的 Javadoc 明写"callers invoke these with no `DomainContext` set"；`KbAccessService` 的 Javadoc 明写"Runs on the caller's thread so it uses the domain-routed DataSource that `DomainContext` has already selected"。**两者要求相反。**

后果之所以严重，是因为它**不会抛异常**：生产上所有域都指向同一个物理 `kb_db`，带着 `DomainContext` 去读 `operator_paradigm` 照样读得到——`ParadigmService.applyBinding` 的 Javadoc 已经把这个陷阱写清楚了（"the mistake would not raise an error, it would silently write to the wrong logical store"）。真正分库的那天才会炸，且到时候没人记得是这里。

**因此实现必须分三段，严禁在循环里交错：**

```
① 无 DomainContext：一次性读出全部已发布范式 + 各自的版本图      （控制库）
② 按 domain 分组，逐组：getDataSource(d) → set(d) → 批量 authorize → clear   （域库）
③ 无 DomainContext：组装响应
```

第 ② 段刻意**按域分组批处理**而不是逐个范式设置/清除——同一个域的多个范式共用一次上下文切换，也让"一个域一次连接验证"这件事只发生一次。

这与 `ParadigmBindingService` 的做法是同一条规矩（它把所有域库读放在前面、清空后才开控制库事务），只是方向相反。**建议在 catalog 的实现类上写一段同样的 Javadoc 并引用 `ParadigmBindingService`**，否则下一个改这里的人没有任何提示。

### 4.3 域不可达时的降级

`DomainPoolManager.getDataSource(domain)` 在域库连不上时抛 `IllegalStateException("domain_database_unavailable")`（建池时就 `conn.isValid(3)`），映射成 503。

**catalog 绝不能因此整体失败**——一个域的库挂了，不该让另外三个域的范式列表也看不见。第 ② 段每组用 try/catch 包住，失败的那组全部进 `hidden`，reason 用 `domain_unavailable`。这与"清单拉取失败不拆工具"是同一条原则：局部故障不该表现为全局消失。

### 4.4 `hidden` 存在的理由

不可见的范式如果只是"不出现"，运营会遇到最难查的那类问题——"我明明发布了，agent 为什么看不见"。`hidden` 把每一条排除都写明原因。

**MCP 侧不透传 `hidden`**（对 agent 是噪音），只有前端和运维读它。这与"no silent caps"是同一条原则：可以不给 agent 看，但不能让人也看不见。

**⚠️ 但 `hidden.details` 会列出非公开知识库的 id，这和既有的不泄露原则冲突。** `KbAccessService` 刻意让"不存在"和"无权限"共用同一个错误、不暴露 KB 是否存在（`KbAccessService.java:43-44` 的注释），而 serving 本身没有鉴权。

**解决：`hidden` 块仅在请求带 `X-KB-User` 时返回**，且 `details` 只列该用户本来就能看见的库（复用 `selectAccessibleKbIds`，把不可见的折叠成"N 个不可见的知识库"）。前端本来就带这个头（`proxyClient.ts` 对 `/api/kb*` 注入，这里要显式加上），MCP 从不带、也本来就不读 `hidden`。不带头时只返回 `paradigms`。

### 4.5 性能

每次调用要对每个已发布范式读一次版本行 + 解析 JSONB + 可能一次 KB 鉴权查询。范式是个位数到几十，且 MCP 侧有 30 秒 TTL，够用。

真成为热点时的优化路径（**现在不做**）：发布时把出口 slot 与 kbIds 摘要写进 `operator_paradigm` 的两个新列，catalog 退化成单表扫描。写在这里是为了说明"不加列"不是死路。

---

## 5. 实现要点

### 5.1 先合并 kbIds 提取，再加第三个调用点

事实 7：两份实现已经存在且**已经漂移**（归一化那步只有一边有）。catalog 需要第三份。

抽成 `ParadigmGraphs.kbIdsOf(JsonNode graph)`（含 `ActiveScope.normalizeKbIds` 那步），三处调用点全部改指它。**这件事独立于本方案也该做，可以先合入。**

**合并是可证明行为中性的**：唯一的差异是归一化，而 `ScopeResolver` 那侧把结果原样交给 `KbAccessService.authorize`，后者第一行就是 `ActiveScope.normalizeKbIds(requestedKbIds)`（`KbAccessService.java:51`）——归一化早做晚做，`authorize` 看到的输入完全相同。所以 Phase 0 不需要行为回归的担忧，只需要既有测试全绿。

### 5.2 可服务性判断同样抽出去

`validateServable` 现在是 `ParadigmBindingService` 的私有方法。抽成 `ParadigmGraphs.isServable(graph)`，绑定与 catalog 共用——两处对"什么叫可服务"的定义一旦分叉，就会出现"绑定得上但 catalog 里没有"这种自相矛盾的状态。

### 5.3 MCP 侧的 name → id 解析

放在 `client.py`，用 catalog 缓存做映射，**大小写与首尾空白不敏感**（agent 复制粘贴很容易带空格）。解析失败返回 `unknown_paradigm` 并附可用名字列表——这是 agent 唯一能自我纠正的信息。

---

## 6. 前端：范式列表加「Agent 可见」列

现在的空白：`ParadigmListView.vue` 有「绑定域 / 自动匹配」两列（`:33-44`），但**看不出可见性**。引用了 private KB 的范式，只有在点绑定时才会被 `paradigm_requires_identity` 拒绝并给出提示（`:322-323` 的文案已经写得很好）；**没绑定的范式在列表上看不出任何异常**。

新增一列，数据来自 `/mcp-catalog`，经**现有的 serving 代理**取（`SERVICE_MAP` 里已有 `serving`，零新代理配置）：

| 状态 | 显示 |
|---|---|
| 在 `paradigms` 里 | 绿标「Agent 可见」；`isDomainDefault` 时补一个「默认」标 |
| `not_servable` | 灰标「不可见」+ tooltip：终点不是 assemble，collect 输出的是评测用裸候选 |
| `kb_not_anonymously_readable` | 橙标「不可见」+ tooltip 列出具体库名 + 一句"改为公开或从图中移除" |
| `unbound_kb_scope` | 橙标「不可见」+ tooltip："引用了知识库但未绑定知识域，无法核验可见性" |
| 未发布 | 不查，直接显示「—」 |

文案直接复用 `bindErrMsg`（`:315-326`）里已有的那几句——**同一个原因在绑定对话框和列表里说同一句话**，否则运营会以为是两回事。

这一列答的是运营真正会问的问题（"我发的范式 agent 能不能用"），而不是"MCP 有几个工具"——后者答案永远是 2，很久不变，做成页面价值不大。若确实需要，README 里有完整的接入信息可以做一个静态说明页，但那与本方案无关。

---

## 7. 观察期与下一步（不要现在做）

上线后跑一段时间，看 `selected_by` 的分布：

- **agent 经常 `explicit` 且结果更好** → 说明这个选择该给它。可以再考虑把个别高频范式升格为一等具名工具（届时才需要为那一两个填英文名）。
- **agent 几乎不 `explicit`，或选了反而更差** → 说明检索策略的选择本该在服务端。转向：把域绑定从"一域一默认"扩成"一域 N 个候选 + 选择规则"（复用 `RetrievalRouter` 已有的分层思路），**发布新范式 = 加一条路由规则，MCP 侧零改动、agent 侧零成本**。这是最彻底解决痛点的形态，但要先建路由和观测，不该在验证需求之前做。

配套缺口：范式执行那条路**目前不写 `serving_query_logs`**（`QueryLogAspect` 的切点是 `SearchService.search(..)`，范式走 `ParadigmExecutionService.run`）。要做上面的判断就得补上，否则只有 MCP 侧日志、没有服务端口径。

---

## 8. 测试

**Java（L1）**
- `ParadigmMcpCatalogWebMvcTest`：`/mcp-catalog` 不被 `/{id}` 吞掉（与 `ParadigmResolveWebMvcTest` 同款）；三道可见性规则各造一个范式验证进 `paradigms` 还是 `hidden`；`domain` 参数过滤。
- `ParadigmGraphsTest`：合并后的 kbIds 提取与 servable 判断，用两处实现的既有用例喂它，确保行为不变（**含归一化那步的差异**，这是合并时最可能改变行为的地方）。

**Java（L2，需 PG）**
- 匿名可读性：private / public KB 各造一个，验证只有 public 的进 `paradigms`，private 的进 `hidden`。
- `hidden` 的鉴权：不带 `X-KB-User` 时整个 `hidden` 块缺席；带头时 `details` 只列该用户可见的库。
- **连接编排（§4.2）**：这是最该有测试却最难测的一条。可行的钉法是给 catalog 的实现注入一个记录型 `DomainContext` 探针，断言"读 `operator_paradigm` 时上下文为空、`authorize` 时上下文等于该域"。没有这个测试，§4.2 那个错误在合库的生产环境里**永远不会被发现**。
- 域不可达降级：把某域的 `database:` 指向一个连不上的地址，断言该域范式进 `hidden`（`domain_unavailable`）而其它域照常返回。

**Python**
- `test_catalog.py`：name/id 双向解析、大小写与空白容错、`unknown_paradigm` **不回落**（最重要：显式选错必须报错而非静默换引擎）。
- 四条边界（§3.1）各一条：陈旧缓存下先强制刷新再判 unknown；catalog 不可用时 id 直通 / name 报 `catalog_unavailable`；域不匹配报 `paradigm_domain_mismatch`；指向 `hidden` 范式的 id 报 `paradigm_not_available`。
- `available_paradigms` 按 domain 过滤（§3.3）。
- `test_client_routing.py` 扩充：不传 `paradigm` 时请求 URL 与 payload 与今天**逐字一致**（回归保护）；TTL 过期后重新拉取；catalog 拉取失败时 `available_paradigms` 缺席而检索照常成功；**错误信封里也带 `available_paradigms`**（对应 §3.3 的 ⚠️）。
- `test_client_fulltext.py` 扩充：显式 `paradigm_id` 优先于域 resolve。

**前端**
- 五种可见性状态的渲染；catalog 请求失败时该列降级成「—」而不是报错阻塞整个列表。

**端到端**
新建范式 → 发布 → **不做任何其它操作** → 调 `search_knowledge` → 响应里出现该范式 → 按中文名指名调用 → `_retrieval.paradigm_id` 正确、`selected_by=explicit` → 用返回的 ids + `paradigm_id` 调 `get_segment_fulltext` 全部命中 → 前端列表显示「Agent 可见」。

---

## 9. 风险

**① Agent 选得对不对，完全取决于 `description`。** 前端那个描述输入框的标签要改成"**这段是给 AI 看的：说清什么问题该用这个范式**"并给示例。这是本方案唯一真正依赖人的地方，但每个范式只写一次，且写的是中文自然语言不是英文标识符。

**② 30 秒提示滞后。** 只影响 `available_paradigms` 的新鲜度，执行路径的正确性不受影响。

**③ 匿名可读性是天花板。** 引用非 public KB 的范式对 MCP 不可用。真解法是给 MCP 加身份（Phase-2 鉴权），不在本方案内。

**④ 范式变多后清单会变长。** 只放 name + 一句话描述，并对 description 做长度上限（建议 200 字，前端提示）。到几十个范式时再考虑按 domain 过滤或截断 + 提示。

**⑤ 顺带解掉一个坑**：本方案**不依赖动态注册工具**，所以 `pyproject.toml:19` / `Dockerfile:65` 声明 `fastmcp>=2.0` 而代码 import `mcp.server.fastmcp`（官方 SDK 1.x，`README.md:94` 写的也是 `mcp>=1.0,<2.0`）这处依赖漂移不再是前置阻塞。**但它仍该修**——现在是靠"fastmcp 依赖 mcp 所以碰巧 import 得到"在工作。

---

## 10. 分期

| 阶段 | 内容 | 判据 | 估时 |
|---|---|---|---|
| **Phase 0** | 抽 `ParadigmGraphs`（kbIds 提取 + servable 判断），三处调用点改指它 | 既有测试全绿，行为零变化 | 0.5 天 |
| **Phase 1** | `/mcp-catalog` 端点 + 三道可见性规则 + `hidden` + Java 测试 | 端点返回正确分类 | 1 天 |
| **Phase 2** | `search_knowledge` 的 `paradigm`；`get_segment_fulltext` 的 `paradigm_id`；`available_paradigms`（**含错误路径**）+ TTL 缓存；`selected_by`；instructions；Python 测试 | 端到端跑通 | 1 天 |
| **Phase 3** | 前端「Agent 可见」列 + 描述输入框标签改写 | 运营能自查为什么某范式 agent 看不到 | 0.5 天 |
| **观察期** | 看 `selected_by` 分布，再决定 §7 的方向 | —— | —— |

依赖关系：Phase 0 可独立先合；Phase 2 和 **Phase 3 都只依赖 Phase 1**（catalog 端点），彼此无关，可并行。表里的顺序是建议顺序不是依赖顺序——如果想先让运营看到「Agent 可见」列，Phase 1 → 3 就能交付一半价值。

**总计约 3 天，零 DDL、零数据迁移、零需要回滚的状态变更。** 关掉的方式也简单：不传 `paradigm` 就是今天的行为，catalog 端点没人调就是一段死代码。
