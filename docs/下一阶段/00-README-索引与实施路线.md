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
