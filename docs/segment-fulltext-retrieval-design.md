# 检索结果下钻原文设计方案

从检索范式的输出结果，回查某个片段的**未压缩全文**，以及取回该片段所属文档的**原始文件**。

不引入 MinIO / 对象存储；不新建表。

---

## 0. 现状：六条必须先知道的事实

1. **返回的 `text` 不是原文。** `ContextAssembler` 有 3000 token × 4 chars 的总预算：seed 项走 `truncateText` 硬截断（尾部加 `...`），其余项走 `extractiveSummarize` 只保留与 query 最相关的句子（`ContextAssembler.java:775-880`）。`collect` 终点算子也按 `maxItems` 截断候选。**调用方手上只有残文 + 一个 id。**

2. **id 足够回查。** `kind=retrieval_unit`（seed）项的 `id` 是 `asset_retrieval_units.id`，`citation.raw_segment_ids` 带底层段落 id；`kind=raw_segment`（context/support）项的 `id` 是 `asset_raw_segments.id`，`sourceId` 是文档 id。全文就在 `asset_raw_segments.raw_text` / `asset_retrieval_units.text`，未截断。

3. **整篇原文不需要拼接。** KB 已有 `GET /api/kb/{kb_id}/documents/{document_id}/download`（`kb/routes/documents.py:125`），`download_path` 按 `document_id` 解析、行里读 `kb_id`、做 `relative_to(upload_root/{kb_id})` 穿越防护。拼 segments 是有损重建（解析归一化文本、丢版式/表格结构、`table_row` 单元乱序），不做。

4. **serving 能直接读到那些文件。** 6 个服务在**同一容器**（supervisord），`uploads` 是命名卷挂在 `/app/uploads`；`asset_documents.storage_path` 是绝对路径，serving 已能查该表。所以 serving 可以自己 stream 原件，不必让调用方二跳到 mining:8901——这对直连 serving、绕过控制面的 `mcp_server` 尤其重要。

5. **⚠️ `selectWithMeta` 的 scope 过滤是可选的。** `AssetRawSegmentMapper.xml` 里 `snapshotIds` 包在 `<if test="... size() > 0">` 内——**传空列表 = 不过滤 = 全库可读**。新接口绝不能让空 scope 退化成"不限"。

6. **⚠️ 一个 snapshot 可能属于多个文档。** `asset_document_snapshots` 上是 `UNIQUE (domain, normalized_content_hash)`，内容相同的文件共享 snapshot，`asset_document_snapshot_links` 因此是 1:N——这正是 `selectWithMeta` 行放大的根因。**片段不能唯一确定文档**，归属必须用本次 scope 的 `documentSnapshotMap` 收窄。

---

## 1. 目标与非目标

**目标**

- 给定检索结果里的 unit id / segment id，取回该片段的完整原文与定位元信息（章节路径、块类型、所属文档）。
- 可选取回前后邻近片段（窗口），解决"截断处正好是关键句"。
- 给定文档 id，从 serving 一跳取回原始文件。
- 以上全部受与检索**同一套**可见性约束。

**非目标**

- 不做整篇文本拼接（见 §0.3）。
- 不做原文高亮定位。`asset_raw_segments.source_offsets_json` 存在但由解析器填充、可能为空，不可依赖；留作后续。
- 不改检索管线、不改算子、不动 `ContextPack` 结构（只补两个字段，见 §4）。
- 不接语义缓存、不写 `serving_query_logs`（不是检索行为）。

---

## 2. 鉴权与范围模型（本方案的核心）

**唯一不变式：任何被返回的片段，其 `document_snapshot_id` 必须落在本次请求解析出的 `scope.snapshotIds` 里；`snapshotIds` 为空则请求失败，绝不退化成"不过滤"。**

### 2.1 scope 从哪来

调用方必须能复现检索时的范围。三种来源，优先级从上到下：

| 入参 | 行为 |
|---|---|
| `paradigmId`（+ 可选 `paradigmVersion`） | 服务端从存图里读 `scope_resolve` 节点的 `kbIds` 参数，再 authorize。**推荐**——调用方不用自己声明范围，也就无法声明一个更宽的范围 |
| `kbIds` | 显式传，走 `KbAccessService.authorize()` |
| 都不传 | 域级 active release scope，与不带 `kbIds` 的 `/api/v1/search` 一致 |

`paradigmId` 与 `kbIds` 同时传 → `400 conflicting_scope_source`。不做合并：两者语义不同，静默取其一会让越权变成配置问题。

### 2.2 授权在哪做

- `X-KB-User` 头 → `KbAccessService.authorize(domain, kbIds, username)`，与 `scope_resolve` 完全同一条路径。无头 = 匿名 = 只剩 public KB（`mcp_server` 就是这种）。
- 任一 kbId 不可读 → 整个请求 `400 kb_not_found`，不做静默子集（沿用现有策略）。
- scope 解析出来后，**片段查询把 `snapshotIds` 作为强制 SQL 条件**，而不是查完再在内存里过滤。

### 2.3 逐条降级 vs 整体失败

`kbIds` 越权 → **整体失败**（沿用现状）。
`refs` 里某个 id 查不到 → **逐条标记 `found:false`**，不整体失败。

两者策略不同是有意的：`kbIds` 是调用方声明的范围，越权必须硬失败；而 `refs` 里的 id 来自上一跳自己的检索结果，逐条降级更可用（一次回查 20 条不该因为 1 条过期而全废）。

但**不区分原因**：不存在、越权、已被新 build 移出 scope，一律 `reason: "out_of_scope"`。区分开就会变成存在性探测器。

---

## 3. 接口设计

### 3.1 片段全文 —— `POST /api/v1/segments/fulltext`

批量。一次检索结果通常要回查多条，逐条 GET 是 N 次往返。

请求：

```json
{
  "domain": "cloud_core_network",
  "channel": "stable",
  "paradigmId": "p-7f3a",
  "refs": [
    { "type": "retrieval_unit", "id": "ru-001" },
    { "type": "raw_segment",    "id": "seg-9f2" }
  ],
  "granularity": "segment",
  "windowRadius": 0
}
```

- `refs` 最多 **50** 条，超出 `400 too_many_refs`。
- `type` ∈ `retrieval_unit | raw_segment`。缺省按 id 前缀猜是不可靠的，必须显式传。
- `granularity` ∈ `segment | window`。`window` 时 `windowRadius` ∈ [1,5]，默认 1。
- `channel` 缺省走 `ServingProperties` 默认值，与 `/search` 一致。

响应：

```json
{
  "scope": { "releaseId": "kb:kb-a", "buildId": null, "snapshotCount": 12 },
  "items": [
    {
      "ref": { "type": "retrieval_unit", "id": "ru-001" },
      "found": true,
      "unit": {
        "id": "ru-001", "unitType": "qa", "title": "AMF 注册流程",
        "text": "（完整未截断文本）"
      },
      "segments": [
        {
          "id": "seg-9f2", "role": "target", "segmentIndex": 42,
          "text": "（完整 raw_text）",
          "blockType": "paragraph", "semanticRole": "parameter",
          "sectionPath": ["3", "3.2"], "sectionTitle": "注册管理",
          "documentSnapshotId": "snap-1",
          "documentId": "doc-7", "documentKey": "doc:/3gpp/23501.pdf",
          "documentName": "23501.pdf", "kbId": "kb-a",
          "hasRawFile": true
        }
      ]
    },
    {
      "ref": { "type": "raw_segment", "id": "seg-dead" },
      "found": false, "reason": "out_of_scope"
    }
  ]
}
```

- `role` ∈ `target | before | after`（`window` 模式下邻居标 before/after，按 `segment_index` 判定）。`segments` 按 `(snapshotId, segmentIndex)` 排序返回——**按原文顺序而不是"命中在前、上下文在后"**，因为要窗口的理由正是那段话跨了切分边界，乱序就白给了。
- `unit` 仅在 `type=retrieval_unit` 时出现。
- `hasRawFile` = 该文档 `kb_id` 与 `storage_path` 均非空——直接告诉调用方能不能调 §3.2，省一次试错。

**`retrieval_unit` → segments 的解析顺序**（复用 `ContextAssembler.resolveCandidateSources` 的既有优先级，避免两套语义）：
`source_refs_json.raw_segment_ids` → `target_ref_json` → 空。单元自身的 `text` 无论如何都返回，所以即使解析不出段落也不是失败。

### 3.2 原件直取 —— `GET /api/v1/documents/{documentId}/raw`

用 GET 而非 POST：前端 `<a download>` / `<iframe>` 预览要直接用 URL。

Query 参数：`domain`、`channel`、`paradigmId` 或重复的 `kbIds`（语义同 §2.1）。

处理链：

1. 解析 + 授权 scope（§2）。
2. `documentId` 必须出现在 `scope.documentSnapshotMap` 的 key 集合里——即它在本次可见范围内。否则 `404 document_not_found`。
3. 查 `asset_documents` 拿 `kb_id` / `storage_path` / `document_name`。
4. `kb_id` 为空或 `storage_path` 为空 → `404 raw_file_unavailable`（legacy `/api/runs` 挖的文档就是这种，从来没经过 KB 上传）。
5. **穿越防护**：`Path(storage_path).toRealPath()` 必须落在 `uploadRoot/{kb_id}` 之下，照 `document_service.download_path:185-189` 复刻。不通过 → `404 document_not_found`（不是 500——这多半意味着数据被改过，不该把细节回给调用方）。
6. 文件不存在 → `404 raw_file_unavailable`。
7. `200` + `Content-Disposition: attachment; filename*=UTF-8''...`，Content-Type 按扩展名映射，未知走 `application/octet-stream`。流式返回，不整块读进内存。

**配置**：新增 `serving.upload-root`，`application.yml` 里 `${SERVING_UPLOAD_ROOT:/app/uploads}`。

> 实现补充：scope 解析被抽成了独立的 `ScopeResolver`，两个端点共用。它不是为了少写几行——**步骤顺序本身就是安全属性**（读 KB ids → 授权 → 解析 scope → 拒绝空 scope），复制一份然后漂移一步，编译器不会有任何反应。

> ⚠️ 这个值必须与 mining 的 `main_control_service/config/system/mining.yaml` 里 `upload.root`（当前 `./uploads`，容器内 cwd=/app 即 `/app/uploads`）一致。两处配置**没有共享真相源**，会漂移。
> 缓解：serving 启动时若 `upload-root` 不存在则打 WARN，且该端点直接返回 `503 raw_file_storage_unavailable`——把"配置错了"和"这个文档恰好没有原件"区分开，否则会得到一片查不出原因的 404。
> 更彻底的做法是把 upload root 加进 `/api/v1/serving-config` 快照下发，但那要动 `MainControlClient.parseDatabase` 那对**平行复制**的解析逻辑（改一侧不改另一侧会让 HTTP 路径与本地回落路径静默分叉，只有 `MainControlClientTest` 拦得住）。**本期不做**，用配置项 + 启动校验，风险记在案。

---

## 4. 结果侧要补的两个字段

不补的话，调用方拿到检索结果**根本走不到** §3.1/§3.2：

1. **seed 项的 `sourceId` 恒为 null。** `ContextAssembler.java:381-397` 构造 `kind=retrieval_unit` 的 seed 时 `sourceId` 直接传 `null`——命中的那条恰恰没带文档标识，得绕 `citation.raw_segment_ids` → 查 segment → 才拿到 documentId。
   **改**：seed 构造时用 `resolveCandidateSources` 已解析出的段落，经 `sourceSegments` 映射回文档 id 填进 `sourceId`。多文档共享 snapshot（§0.6）时取 scope 内的那个；仍不唯一则留 null（不猜）。

2. **全链路没有 `kb_id`。** `SourceRef` 只有 `id/documentKey/title/relativePath`。
   **改**：`SourceRef` 加 `kbId` 字段，`DocumentSourceRow` + `AssetDocumentMapper.selectDocumentSources` 的 SQL 带上 `d.kb_id`。
   顺带填掉 CLAUDE.md 记的"结果里没有 KB 来源标注"这个缺口。

> `SourceRef` 是 record，加字段会破坏所有构造点。**改完必须 `rm -rf target/classes target/test-classes && mvn -o test`**——Maven 增量编译看时间戳，不动测试源码就 `Nothing to compile`，`test-compile` 的 BUILD SUCCESS 是假的。

---

## 5. 数据访问层改动

### 5.1 `AssetRawSegmentMapper`

```java
/** scope 强制：snapshotIds 为空时调用方应提前失败，SQL 侧不再兜底。 */
List<SegmentFullRow> selectFullByIds(
        @Param("segmentIds") List<String> segmentIds,
        @Param("snapshotIds") List<String> snapshotIds);

/** window 模式：一次查完所有目标的邻居窗口（OR 拼接），而不是每个目标一次往返。 */
List<SegmentFullRow> selectWindows(
        @Param("windows") List<SegmentWindow> windows,
        @Param("snapshotIds") List<String> snapshotIds);
```

> 实现修正：邻居查询按 `List<SegmentWindow>`（`snapshotId + fromIndex + toIndex`）批量 OR 拼接。一次结果里常有多条命中落在同一文档、窗口彼此重叠，逐个查是 N 次往返。
> 服务层判定邻居时**必须同时比对 snapshotId**，不能只看 `segment_index` 距离——mapper 只做 scope 过滤，返回的行里会有别的文档同样索引的段落。

**SQL 与 `selectWithMeta` 的三点不同**（不要复用它）：

- **不 JOIN `asset_document_snapshot_links`** → 从根上避开 1:N 行放大。文档归属改由 Service 层用 `scope.documentSnapshotMap` 反查（snapshotId → 该 scope 内的 documentId），这既去了重，又天然把归属限制在可见范围内。
- **`snapshotIds` 条件不包 `<if>`**，无条件 `IN`。
- 多带 `segment_index`、`section_title`、`token_count`（定位与前端展示要用）。

新 `SegmentFullRow`：`id / documentSnapshotId / segmentKey / segmentIndex / rawText / blockType / semanticRole / sectionPath / sectionTitle / tokenCount / metadataJson`。

### 5.2 `AssetRetrievalUnitMapper`

现有 `fetchDetailsByIds(ids)` **无 scope 过滤**，直接暴露就是越权后门。新增：

```java
List<FtsResultRow> fetchDetailsByIdsInScope(
        @Param("ids") List<String> ids,
        @Param("snapshotIds") List<String> snapshotIds);
```

不改老方法——它在 hydrate 阶段被调用，那时 scope 已由检索路径保证，改签名会波及一片。

### 5.3 `AssetDocumentMapper`

```java
List<DocumentFileRow> selectFileLocations(
        @Param("documentIds") List<String> documentIds,
        @Param("snapshotIds") List<String> snapshotIds);   // 经 links 关联，保证在 scope 内
```

返回 `id / kbId / storagePath / documentName / documentKey`。§3.1 的 `hasRawFile` 和 §3.2 的 stream 都用它。

### 5.4 `AssetRepository`

新增三个方法，签名上**强制**带 `snapshotIds`，且入口处 `if (snapshotIds.isEmpty()) throw new IllegalArgumentException("empty_scope")`：

```java
List<SegmentFullRow>  resolveSegmentsFull(List<String> ids, List<String> snapshotIds);
List<SegmentFullRow>  resolveSegmentWindow(String snapshotId, int from, int to, List<String> snapshotIds);
List<DocumentFileRow> resolveFileLocations(List<String> documentIds, List<String> snapshotIds);
```

### 5.5 DDL

**不需要新表、不需要新索引。**

`window` 模式按 `(document_snapshot_id, segment_index BETWEEN ?)` 查，现有 `idx_asset_raw_segments_snapshot(document_snapshot_id)` 已能收敛到单文档的段落集（几百到几千行），再排序代价可忽略。等真出现慢查询再评估复合索引——而且那要改 Python 侧 `databases/asset_core/schemas/`（serving 对 `asset_*` 只读，**不在 Java 侧建 asset 表 DDL**）。

---

## 6. 应用层与 Web 层

```
api/FullTextController.java        新增   两个端点 + 参数校验
application/FullTextService.java   新增   scope 解析 → 授权 → 取数 → 组装
application/RawFileService.java    新增   路径解析 + 穿越防护 + 流式返回
api/GlobalExceptionHandler.java    改     补错误码
```

**`FullTextService` 的执行顺序**（顺序本身是安全约束，不能重排）：

```
1. 解析 domain / channel（缺省走 ServingProperties）
2. DomainContext.set(domain)            ← try/finally，见下
3. scope 来源解析：paradigmId → 存图 scope_resolve.kbIds ；或显式 kbIds
4. KbAccessService.authorize(...)        ← 越权在这里硬失败
5. assetRepository.resolveActiveScope(domain, channel, kbIds)
6. snapshotIds 为空 → 抛 empty_scope
7. 按 type 分组取数（unit / segment），全部带 snapshotIds
8. 用 scope.documentSnapshotMap 反查文档归属（去重 + 收窄）
9. window 模式：按 segment_index ± radius 取邻居
10. 组装响应，未命中的 ref 标 found:false / out_of_scope
11. DomainContext.clear()
```

**⚠️ `DomainContext` 必须 set/clear**（照 `SearchService.search:144,152`）。漏了不会报错：`DomainRoutingDataSource` 把 null 域静默当 default，配了 inline `database:` 的域会**悄悄查默认库**，返回一片 `out_of_scope`，而且看起来像数据问题。本方案全程在调用方线程上跑，**不新起并行分支**，所以不涉及虚拟线程包装；后续若要并行取数，必须用 `DomainContext.wrapCallable`。

**错误码**（`GlobalExceptionHandler` 沿用现有 `IllegalArgumentException(code)` 风格）：

| code | HTTP | 触发 |
|---|---|---|
| `kb_not_found` | 404 | 任一 kbId 不可读（复用现有映射，既有代码里就是 404） |
| `conflicting_scope_source` | 400 | 同时传 `paradigmId` 和 `kbIds` |
| `too_many_refs` | 400 | `refs` > 50 |
| `empty_scope` | 400 | scope 解析出零 snapshot |
| `no_active_release` / `no_active_kb_build` | 400 | 复用 `AssetRepository` 既有抛出 |
| `document_not_found` | 404 | 文档不在 scope 内，或穿越校验失败 |
| `raw_file_unavailable` | 404 | legacy 文档无 `kb_id`/`storage_path`，或文件不存在 |
| `raw_file_storage_unavailable` | 503 | `serving.upload-root` 配置错/不可达 |

**不接的东西**：不写 `serving_query_logs`（`QueryLogAspect` 切的是检索，别误伤），不查语义缓存。只打 INFO 日志：`domain / user / refCount / hitCount`，**不打片段文本**。

---

## 7. MCP 集成

`mcp_server` 直连 serving:8081，因此 §3.1/§3.2 都是一跳可达——这正是把原件端点放在 serving 而非让 agent 跳 mining 的理由。

- `client.py` 加 `get_segment_fulltext(...)`，`server.py` 加第二个 `@mcp.tool()`。
- **`raw` 端点不暴露成 MCP tool**：agent 拿二进制文件没有意义，且会把整份 PDF 灌进上下文。改为在 `_retrieval` meta 里给出可点击的 URL，由人或前端去取。
- **匿名调用**：MCP 不带 `X-KB-User`，只能读 public KB——与 `search_knowledge` 行为一致，符合"绑定为域默认的范式不得引用私有 KB"这条既有约束。
- `search_knowledge` 的返回本来就整条透传 items（`_normalize_paradigm_body` 只做信封扁平化），id 已经能到 agent 手里，**无需改动**。
- tool 描述里要写清"当返回文本以 `...` 结尾或需要完整条款原文时调用"，否则模型不会主动用。
- `mcp_server/README.md` 补第二个 tool 的说明。

---

## 8. 前端集成

- `SearchView.vue` 每条结果加「查看全文」→ 抽屉展示完整 `text`，`window` 模式下把前后段落灰显。
- 有 `hasRawFile` 时加「下载原件」。

> ⚠️ 实现修正：**下载必须走 axios 取 blob，不能给 `<a href>` 拼 URL**。`X-KB-User` 是 `proxyClient` 在请求拦截器里注入的，浏览器直接发起的导航根本不经过拦截器，会以匿名身份到达后端——私有知识库的文档就会莫名其妙 404。复用已有的 `utils/download.ts`（`saveBlob` + `filenameFromDisposition`）。
>
> 另一处：`kbIds` 作为 query 参数时要设 `paramsSerializer: { indexes: null }`。axios 默认发 `kbIds[]=a`，Spring 的 `@RequestParam List<String>` 不认，会当成没传而**静默退回全域范围**。
>
> 还有：前端必须**钉住产生当前结果的那次检索所用的 scope**（`resultScope`），不能用选择器的当前值——用户改了选择但没重新检索时，下钻会把结果全部查成 `out_of_scope`。
- **注意原件是"当前文件"，快照是"挖掘时的内容"**。文件在挖掘后被重新上传过就对不上。UI 上标一句"原件可能已更新"，别让人误以为是引证不符。

---

## 9. 测试计划

**L1 单测**

- `FullTextServiceTest`
  - 越权 kbId → `kb_not_found`，且**一条数据都不返回**
  - scope 外的 segment id → `found:false / out_of_scope`，与"不存在的 id"**响应完全一致**（防存在性探测）
  - `snapshotIds` 为空 → `empty_scope`，而不是查出全库
  - 同时传 `paradigmId` + `kbIds` → `conflicting_scope_source`
  - 一个 snapshot 挂多文档时，归属取 scope 内那个；不唯一则不猜
- `RawFileServiceTest`：`storage_path` 指向 `uploadRoot/{kb_id}` 之外 → `document_not_found`；`kb_id` 为 null（legacy 行）→ `raw_file_unavailable`
- `FullTextWebMvcTest`：请求/响应契约、`X-KB-User` 透传、`refs` 上限

**L2 集成（需 PG）**

`AssetRepositoryFullTextIT`，放在 `AssetRepositoryKbScopeIT` 旁边，**自带 fixture**（按 token 造数据、`@AfterEach` 删干净），所以不依赖库里已有 release——这点很重要：其余多数 IT 在没有 active release 的库上会整体 skip。

- `selectFullByIds` 在「一个 snapshot 挂两个文档」时仍只返回一行（不 JOIN links 的回归锁）
- 三个 mapper 的 scope 过滤真的过滤
- `selectWindows` 的 OR 拼接：区间闭合、多窗口不重复、**不跨 snapshot 串**
- `selectFileLocations` 的 `DISTINCT`：一个文档挂多个在范围内的 snapshot 只出一行
- legacy 文档 `kb_id`/`storage_path` 为 null，`selectDocumentSources` 带回 `kb_id`

> 写 fixture 时的两个坑（都是真跑一次才会知道的）：
> ① 实际库里 JSON 列是 **JSONB**（`002_asset_core_postgresql` 把 001 里声明的 TEXT 迁走了），字面量必须显式 `?::jsonb`，否则参数按 varchar 绑定、PG 直接拒绝赋值；
> ② `unit_type`/`target_type`/`block_type`/`semantic_role` 都有 CHECK 约束，随手写个 `'qa'` 会插不进去。

**回归**

- 改了 `SourceRef` record → `rm -rf target/classes target/test-classes && mvn -o test`（§4 注）
- `mcp_server/tests/` 加 tool 路由用例

---

## 10. 实施顺序与工作量

| 阶段 | 内容 | 估时 |
|---|---|---|
| P1 | §5 数据访问层 + §6 `FullTextService`/Controller + `granularity=segment` + L1 测试 | 1 天 |
| P2 | §4 补 `sourceId` / `SourceRef.kbId`（含全量重编译回归） | 0.5 天 |
| P3 | §3.2 原件端点 + 配置项 + 启动校验 | 0.5 天 |
| P4 | `granularity=window` | 0.5 天 |
| P5 | MCP tool + README | 0.5 天 |
| P6 | 前端抽屉 + 下载入口 | 0.5 天 |

P1+P2 是最小可用集（agent 能拿到片段全文并知道出处）。P3 起可独立发布。

---

## 11. 风险台账

| 风险 | 影响 | 处置 |
|---|---|---|
| `serving.upload-root` 与 mining `upload.root` 漂移 | 原件端点全量 404，难定位 | 启动校验 + 专用 503 码（§3.2）；长期考虑并入 serving-config 下发 |
| 单容器/共享卷假设 | 拆容器部署后 §3.2 失效 | 已在 §3.2 记；届时该端点是唯一需要改的点，`hasRawFile` 可直接置 false 降级 |
| `SourceRef` 加字段破坏构造点 | 增量编译假成功 | §4 的强制全量重编译 |
| 一个 snapshot 挂多文档 | 归属歧义 | 用 scope 收窄；仍不唯一则留 null，不猜 |
| 原件与快照版本不一致 | 用户误判引证不符 | 前端提示（§8）；引证校验类场景应以片段全文为准，不用原件 |
