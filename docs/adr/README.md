# ADR 索引 — 文档解析平台化

> 上游规格（SRS）：
> - `docs/文档解析平台化-能力规格与工作拆解.md`（能力规格 / 实施前 SRS）
> - `docs/文档入库解析地基-工业调研与演进方案.md`（工业调研与选型）
>
> 本目录记录架构决策记录（ADR），用于在 WP0「契约冻结」阶段把口头共识固化为可审计的契约。
> ADR 只冻结「是什么 / 边界在哪」，不包含具体 SQL 与实现代码；物理命名与迁移脚本由对应 WP 的实现 PR 落地。

## 状态约定

| 状态 | 含义 |
|---|---|
| `Accepted` | 已拍板，后续 WP 据此实现 |
| `Proposed` | 已提出默认方案，等待审批；审批通过后转 `Accepted` |
| `Superseded` | 被后续 ADR 取代 |
| `Deferred` | 留待样本评测或后续里程碑决定，不在 M0 冻结 |

## M0 冻结的 ADR

| ADR | 主题 | 状态 | 对应 SRS |
|---|---|---|---|
| [0001](./0001-locked-foundational-decisions.md) | 已锁定的地基不变量（对象存储 / 文档无版本 / Snapshot 为唯一知识根 / Parse IR 为事实源 / 算子分离） | Accepted | SRS §15.1、§10 |
| [0002](./0002-open-decisions-proposed-defaults.md) | 阻塞 WP0/M1 的开放决策默认方案（指纹字段 / word-span 粒度 / 跨 KB 去重 / retention / Object Lock / 归档语义） | Accepted | SRS §15.2、§15.3 |
| [0003](./0003-autonomy-decision-log.md) | 自主执行决策日志（D-001… 持续追加） | Accepted | 自主委托 |

## 后续 ADR（按 WP 推进时追加）

- 对象 key 命名与 bucket 划分（WP1A）
- 物理表最终命名与外键策略（WP2）
- 路由策略版本化（WP6）
- SBOM / 许可门禁细则（WP13）
