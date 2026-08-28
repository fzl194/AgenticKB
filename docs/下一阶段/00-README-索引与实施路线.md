# 下一阶段整改：索引与实施路线

> 生成：2026-08-24 · 分支 master @ `5d8d40c`
> 输入：`docs/文档解析平台化-当前问题清单-2026-08-24.md`（P01-P13，共 13 项）
> 性质：**只读审查产物——本目录所有文档不含任何代码改动**；每份方案均已通过独立子代理对抗式审查并回填审查记录（各文档末节）。

---

## 1. 文档索引与分组逻辑

13 个问题按功能模块归并为 8 份方案（同模块联动的问题合并考虑，避免方案间打架）：

| 文档 | 覆盖问题 | 模块 | 核心结论 |
|---|---|---|---|
| [01-上传通道工业化](01-P01-上传通道工业化-审查与整改方案.md) | P01 | 上传/对象存储写入 | 直传通道可用但整读内存+无上限；UploadSessionService 零接线（连 router 都没挂）；multipart 是空壳 |
| [02-解析编排与质量门禁](02-P02+P09-解析编排与质量门禁-审查与整改方案.md) | P02+P09 | 解析计划/质量 | fallback 框架完整但链恒 1；char_coverage 生产恒 None；质量指标连落库都没有（实测 22 run 全空） |
| [03-解析热路径](03-P03+P11-解析热路径-审查与整改方案.md) | P03+P11 | 冻结输入/解析读取 | 生产链"双空转"（手搓 freeze + _no_stale）；源对象四跳路径内存整包；修复素材（materialize_temp）已在库未接 |
| [04-检索就绪与图片资产](04-P04+P10-检索就绪与图片资产-审查与整改方案.md) | P04+P10 | 范式/检索/二进制 | 三 KB 实测 units=0、embeddings=0、binary=0——"validated 但不可检索"；figure 切片结构性为空（caption 无生产者） |
| [05-结构化结果读路径](05-P05+P06-结构化结果读路径-审查与整改方案.md) | P05+P06 | Parse IR 读取/死码 | 一次请求三重全量成本（磁盘 tmp + 内存 bytes + 对象图）；旧路由死码可零风险删除 |
| [06-Run执行模型](06-P07-Run执行模型-审查与整改方案.md) | P07 | 任务执行 | daemon 线程共生，**实测僵尸 Run 已滞留 3 天**；interrupted 有入口无出口；无租约无接管 |
| [07-文档删除与存储GC](07-P08+P12-文档删除与存储GC-审查与整改方案.md) | P08+P12 | 删除/撤回/物理清理 | 硬删借 FK cascade 改写历史 Build；operations 表连生产者都没有；两断头互为等待，顺序必须先语义后 GC |
| [08-API鉴权统一](08-P13-API鉴权统一-审查与整改方案.md) | P13 | 安全边界 | 实测 legacy 族匿名 200；**仓库内 secret 可伪造 admin 头通过 KB 防线**；四个端口全裸 |

## 2. 运行时实测证据（2026-08-24，docker 直连 + 真库只读）

- **P13**：8 个 legacy 路由族直连 8901 全部匿名 200（含 `/api/config` 泄漏 DB 坐标、44 个 Build、工作流定义）；KB 族正确 401；用仓库 auth.yaml 的 secret 伪造 `X-KB-User: admin` **通过了 KB 鉴权**（422=已越过 401 进入参数校验）。
- **P07**：`mining_runs` 有一条 2026-08-21 起 `running`、`finished_at IS NULL` 的**活体僵尸 Run**（3 天无人接管）。
- **P04/P10**：test/test2/test23 三 KB：16 文档 / 216 切片 / **0 检索单元 / 0 向量**；binary bucket **0 对象**（source 10 / parse 24）。
- **P02/P09**：22 条 parse attempt **全部 primary**（fallback 0、多 attempt run 0）；22 条 parse run 含 quality_metrics 的 **0 条**。
- **P03**：21 文档最新快照与当前内容**真实错配 0**（无数据事故）；但 6 个 legacy 文档链接 revision 为 NULL、1 个文档零快照。

## 3. 优先级矩阵（综合风险×成本×依赖）

| 级别 | 事项 | 出处 | 理由 |
|---|---|---|---|
| **P0-立即** | S0 部署止血：四端口回环绑定 + secret/bootstrap 密码轮换 + CORS | 08 | 秘钥在库+端口全裸，是当前唯一"正在漏"的面；纯配置变更 |
| **P0-立即** | S1 全局默认拒绝中间件（白名单含登录端点） | 08 | 机制性堵住第 9 个漏网族 |
| **P0-立即** | S1 启动自愈扫描（interrupted + 按引擎分流 re-enqueue） | 06 | 当天可上，直接清理实测僵尸；多实例前必须先有它 |
| **P0-立即** | S1 真 freeze + 真 check_stale 接线（含 SKIP 语义保持） | 03 | 两处接线消灭一类一致性事故，性价比全卷第一 |
| **P0-下迭代** | S1 质量基准接线（source_text + 指标落库，先 WARN 灰度） | 02 | 质量门禁当前是"结构合法检查器"；灰度防存量行为回归 |
| **P0-下迭代** | S1 软删替代硬删（9 点读面过滤 + Java 过滤） | 07 | 止住"改写历史 Build"的事故面 |
| P1 | 质量档位生效 / S2 租约认领 / P04 范式决策+readiness / P05 前端懒加载 / P06 死码删除 / P08 KB removal Build | 02/06/04/05/07 | 各方案正文 |
| P2 | 契约 v1.2（path 输入 + binaries 引用）/ 独立 worker / P05 后端读模型 / P12 GC 执行器 / P10 caption 闭环 | 03/04/06/05/07/04 | 依赖前置项 |

## 4. 依赖关系图（谁阻塞谁）

```text
08 鉴权 S0/S1 ──────────┐
   │（挂载前必须先有鉴权）│
   ▼                    │
01 上传 S2(挂 file_management router)─┘
01 S1 第0项(MinIO put_stream 适配器重写) ──► 01 S2 multipart / 03 S2（同一适配器）
03 S2 ──同批──► 02 S1（source_text 接口共用读取现场）
03 S2 ──归并──► 05 S2-4（get_to_file 同一改动）
04 P10-S1(契约 v1.2) ──同批定稿──► 03 S2（一次 bump：path 输入 + binaries 引用）
06 S2(租约) ──► 06 S3(worker) ──► 07 P12-S2(GC 可挂同一 worker)
07 P08-S1(软删) ──► 07 P08-S2(KB removal Build) ──► 07 P08-S3(purge) ──► 07 P12-S2(真删)
02 S1(质量基准) ──► 02 S3(fallback 链) ──► 02 S4(第二后端，按质量数据分布决策)
04 S1-A(范式切换) ──前置：处理该 KB 未终态 Run（06 S1 之上）
05 P06(死码删除) ──► 08 S2（路由清单更干净后再逐族处置）
```

**三个"同批定稿"点**（拆开做会返工）：① MinIO 适配器（put_stream 流式 + get_to_file）；② Parser 契约 v1.2（path 输入 × binaries 引用输出一次定稿，fingerprint 契约测试整批过）；③ 租约/worker 基建（06 S2 与 07 P12-S2 共用 SKIP LOCKED + lease 模式）。

## 5. 对抗式审查总账

8 份方案共捕获并已回填修订：**CRITICAL 11 项 / HIGH 14 项 / MEDIUM 25 项**。推翻初稿关键论断的硬伤包括：

- 01："put_stream 已流式"（实现整包 join）、complete 状态机断环、multipart 空壳
- 02：REPAIR 分支是死代码（"接链自动生效"错误）、TIMEOUT 与 DDL 词表矛盾、§6 残留错误 fallback 配对
- 03：freeze 替换丢失 SKIP 语义、S4 补数规则有 file_migration 反例
- 04："增量补 RU 作业"机制不存在（正确路径是签名 auto force_redo）、include_figure_captions 默认值写反（True 非 False）
- 05：删 ParseResult 接口会断编译（它是生产链路类型）、fingerprint 非 IR 内容哈希（ETag 方案重做）
- 06：advisory lock 三重选型错误（xact/session/key 粒度）、re-enqueue 对 legacy 引擎会 400
- 07：选择性挖掘下照抄域级 withdrawal 必失败、parse IR 保留策略初稿反置
- 08：白名单漏登录端点会死锁全站、S0 漏另三个裸奔端口

每份文档末节有完整审查记录表（发现→处置逐项对应）。锚点核验总计 ~150 处 file:line，错误率约 2%（已全部勘误）。

## 6. 实施顺序建议（整合所有依赖）

```text
第一批（立即，配置+小接线）：08-S0 → 08-S1 → 06-S1 → 03-S1 → 05-P06
第二批（下迭代）：02-S1(灰度) → 07-P08-S1 → 01-S1(适配器重写+上限) → 04-S1(范式决策)
第三批：02-S2 → 06-S2 → 05-S1 → 01-S2 → 07-P08-S2
第四批（契约与架构批次）：03-S2 + 04-P10-S1 契约 v1.2 同批 → 06-S3 + 07-P12 → 05-S2 → 02-S3 → 04-S2
```

> 所有方案在实施前需用户对四个产品决策点拍板：① 04-S1-A KB 默认范式是否切 fast_retrieval；② 02-S4 第二解析后端路线（docling/云/暂缓）；③ 07 删除语义（quick/withdraw 默认策略）；④ 08 legacy 各族下线 vs 收编清单。

---

## 7. 决策记录（2026-08-27，用户拍板）

| 决策点 | 结论 |
|---|---|
| ② 第二解析后端 / 扫描件 OCR（02-S4） | **暂缓**——先修确定性 bug，待 P09 质量数据积累后按分布决策（届时在本地 Docling vs 云 VLM 间选） |
| ① KB 挖掘范式（04-S1-A） | **建库默认 `system-full-baseline`**（范式可后改），消除「不选范式无法挖掘」的 UX 陷阱 |
| 新增：批次挖掘通道 | **退役**——挖掘必须基于知识库（整库或部分文档）；`POST /api/runs` 的 `upload_batch_id` 入口及其 UI 路径移除（实测该通道 v2 解析必败 `document_parse_unavailable`，无修复价值） |
| 新增：多实例 / P07-S3 | **暂缓**——单实例 + 租约 + 延迟复扫已自洽，横向扩容需求出现时再做 |
| ③ 删除语义 | 方案既定（软删优先、GC 后置）无需再拍；仅 P12 批次时的「保留期时长」参数届时确认 |
| ④ legacy 路由族 | 维持 P05/P06 批次时按需处置，不单独拍板 |

## 8. 修订后实施顺序（2026-08-27，决策落定版）

```text
批次1 止血+速赢（无需决策，立即可做）
 ├─ P08-S1 软删替代硬删（唯一剩的 P0：硬删改写历史 Build）+ 9 点读面过滤 + Java 侧过滤
 ├─ .md mime 扩展名回落（kb/routes/documents.py:104 盲信 multipart content_type → .md 记为 octet-stream 被 parser 拒收）
 ├─ 失败文档 mining_run_documents 终态回写（卡 processing、document_id=None、run 计数黑洞 total≠committed+failed+skipped）
 ├─ resume 已 finalize Run 的终态回写（runtime.resume() 快速路径 ~4s 内存返回 completed 无人写 DB → 行滞留 running、重启回弹）
 └─ 批次挖掘通道退役（决策：KB 唯一）——/api/runs upload_batch_id 变体 + /api/uploads 消费面 + UI 批次挖掘入口

批次2 上传通道工业化（P01）
 ├─ S1 put_stream 流式适配器（消整读内存）+ 大小上限
 └─ S2 UploadSessionService 接线 router + multipart 真实现（复用 S1 适配器；批次通道退役后按 KB 上传路径收口）

批次3 读路径与加固（P05 + 小项）
 ├─ Parse IR 读路径三重全量成本（磁盘 tmp + 内存 bytes + 对象图）→ 流式/按需 + 前端懒加载
 ├─ KB 认证池加固（每请求 upsert_user_by_username 直查 DB、请求风暴下池耗尽 → 缓存/扩容）
 └─ ParserRouter 接线（has_text_layer 保护进生产链，为 OCR 决策铺路）

批次4 检索就绪（P04+P10，范式默认已定）
 ├─ 范式切换 + readiness 门控（units/embeddings=0 不得称 validated；KB 建库默认范式同批落地）
 ├─ 失败文档重试闭环
 └─ figure/caption 闭环（随 OCR 决策）

批次5 删除链闭环（P08-S2/S3 + P12，依赖批次1）
 └─ KB removal Build → purge → GC 执行器（含保留期参数，届时确认）

批次6 视需求
 ├─ OCR / 第二解析后端（决策已定：暂缓，待质量数据）
 ├─ P07-S3 独立 worker + SKIP LOCKED 队列认领（多实例时）
 └─ legacy 路由族收尾
```

已完成批次对照（截至 2026-08-27，均经真实运行时审查后合并 master）：
08-S0/S1（P13 鉴权）✅ · 06-S1（P07 启动自愈）✅ · 06-S2（P07 租约）✅ · 03-S1（P03 冻结输入）✅ · 05-P06（幽灵路由退役）✅ · 02-S1（质量基准+落库）✅ · 02-S2（质量档位）✅ · 02-S3（fallback 链）✅

---

## 9. 检索架构决策（2026-08-28，用户拍板）

| 决策点 | 结论 |
|---|---|
| 检索链路统一 | **只保留范式编排链路**；固定检索链路（SearchService 九步管线）在能力补齐算子化 + 官方默认范式复刻后**彻底删除**（不留冻结兼容期） |
| 范式与 KB 的关系 | **菜谱+运行时范围**两层解耦：范式不含 KB（通用菜谱，可复用分享）；KB 可设默认范式（库级偏好）；搜索时现场指定库组合。scope_resolve 演进为：图内可写死（专属用法）或留空（运行时按 显式指定 > 库级默认 > 领域默认 注入） |
| MCP 用户化 | **单实例 + 用户级接入密钥**：用户在 UI 生成/吊销密钥、管理开放库范围与工具描述；Agent 持钥访问只见该用户的库（复用 KbAccessService 授权模型） |
| MCP 工具族 | list_knowledge_bases / search_knowledge / list_documents / get_document / get_segment_fulltext |
| 实施顺序 | 检索就绪（批次4 原计划）→ A（身份与绑定模型）→ B（MCP 工具族+管理）→ C（链路统一+固定链路删除） |

现状调研报告（双链路/范式模型/KB scope/MCP 全貌）见调研结论存档于本会话；关键锚点：
范式表 `operator_paradigm(_version)`+域绑定 002 DDL；范围算子 kbIds 设计期冻结 ScopeResolveOperator.java:17-22；
MCP 零鉴权匿名（仅 public KB）mcp_server/server.py；固定链路四能力缺口=树导航/语义缓存/多查询扩展/级联重排。
