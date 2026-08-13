# ADR-0002：阻塞决策的默认方案（已采纳）

- 状态：**Accepted** —— 2026-08-13 在用户全权委托下按「基于原始文档决策」原则采纳；逐条采纳结果见文末「采纳决议」。
- 日期：2026-08-13
- 上游：`docs/文档解析平台化-能力规格与工作拆解.md` §15.2「阻塞 WP0 的决策」与 §15.3「阻塞 M1 文件地基的决策」

## 阅读说明

SRS §15 仍留 6 个开放决策阻塞 WP0 与 M1。本 ADR 对每条给出**建议默认值**、**理由**和**需要你确认的点**。每条可独立批准/驳回/修改；批准后我据此落 WP0 契约与 WP2 schema。

---

## O1 Snapshot 唯一指纹字段（阻塞 WP0）

**背景**：Snapshot 需要一个稳定指纹做幂等复用（相同文档内容 + 相同加工配置不重复解析/切片）和唯一约束。SRS §15.2 建议含 `document_id`，但 §8.3 又允许「相同内容被多个 Document 通过 link 复用」——两者张力在于 `document_id` 是否进指纹。

**建议默认**：

```text
snapshot_fingerprint = sha256(
  domain,                          # 隔离边界（5 个 domain 互不相通）
  source_raw_hash,                 # 冻结内容的 SHA-256，承载「内容」语义
  parser_fingerprint,              # parser 代码 + 模型版本 + parser_config_hash
                                   #   + IR schema 版本 + normalizer/reconciler 版本 + 依赖指纹
  workflow_graph_hash,             # 现有 workflow binding graph hash
  compiler_fingerprint             # segment compiler name/version/config
)
唯一约束：UNIQUE(domain, snapshot_fingerprint)
```

**与 SRS 建议的偏差**：**不把 `document_id` 放进指纹**。理由——指纹应基于「内容 + 加工方式」，这样同一份内容（相同 raw_hash）在多个 Document 间可经 `asset_document_snapshot_links` 复用同一个 Snapshot，与 §8.3 共享语义一致。`document_id ↔ snapshot` 的多对多关系由 snapshot_links 承载，不进指纹。

`source_storage_object_id` / `source_content_revision` 也不进指纹（它们是「哪个对象的字节」的定位，与 raw_hash 表达的「内容」是两层；定位写在 snapshot_links）。

**需要你确认**：
- (a) 认同「指纹不含 document_id，靠 snapshot_links 表达多对多」吗？还是你更倾向 SRS 字面建议把 `document_id` 纳入指纹（牺牲跨文档复用，换取一行一文档的直观性）？
- (b) `workflow_graph_hash` 是否复用现有 `asset_document_snapshots.workflow_*` 已有的 graph hash 字段？（我倾向复用，待 WP2 核对现有列名）

---

## O2 word / span 保存粒度（阻塞 WP0）

**背景**：粒度直接决定存储成本与引用精度。SRS §15.2 建议「第一期 element + cell + 必要字符范围，word-level 仅对 OCR/引用高要求格式开启」。

**建议默认**：

| 粒度 | v0.1 默认 | 说明 |
|---|---|---|
| Element | **必存** | 原子结构单位，含 text / normalized_text |
| Evidence Span（element text 内字符范围） | **按需存** | citation-critical 格式（PDF / DOCX / 扫描件 / HTML）存 `char_start/char_end`；TXT/Markdown 存 line/char range |
| Table Cell | **必存** | cell 级 text + source_span，§5.5/§7.6 要求 |
| word-level | **默认关** | 仅当 route 标注该页/格式为「OCR 或高引用要求」时，由该 backend 在该范围内补 word span |
| 全格式逐字 span | **不做** | 不在 v0.1 强制全量 word 关系化 |

**理由**：先用 element + cell + 必要字符范围封住 schema 能力（字段可缺不伪造）；word-level 留作 route 级可选增强，后续按评测与成本再放宽，不需要改 schema。

**需要你确认**：认同上述默认？特别是「TXT/Markdown 也存行/字符范围」（用于 §A01 line-addressable 验收）这一点是否要在 v0.1 就开。

---

## O3 跨 KB / 跨 domain 物理去重（阻塞 M1）

**背景**：相同字节（相同 SHA-256）是否在 KB 间共享同一个 Storage Object。SRS §15.3 建议「默认仅在同一部署安全域内复用；有独立密钥/保留策略的 domain 禁止共享」。

**建议默认**：**仅同一 domain 内允许物理去重；跨 domain 禁止共享 Storage Object。**

**理由**：
- 当前 5 个 domain 共用一个库 `kb_db`，但各有独立 scenario_pack 与语义；逻辑上应视为独立安全域。
- 同 domain 内去重收益清晰、隔离简单；跨 domain 去重会把删除/保留/权限耦合到物理对象层，v1 收益不抵复杂度。
- Logical Document 的权限、当前态、删除状态在任何情况下都完全独立（SRS §8.6 不变量），去重只影响「字节存几份」。

**需要你确认**：认同「同 domain 去重、跨 domain 不共享」？若未来某个 domain 有独立合规要求，再以独立 bucket + 禁止共享处理。

---

## O4 对象 retention 天数与永久删除审批（阻塞 M1）

**背景**：SRS §15.3 建议「原件跟随业务保留，staging 24 小时，物理 purge 至少双重确认并异步执行」。

**建议默认**：

| bucket class | 默认保留 | 回收触发 |
|---|---|---|
| `source` | Document/Snapshot/Build 引用期间保留 | 最后引用释放后，grace period **7 天**（可配）+ 审计，再异步物理删除 |
| `parse`（backend raw / canonical IR / quality） | 与 Snapshot/Build 同生命周期 | Snapshot 无任何 Build/Release 引用且被显式废弃后才可回收；历史 Release 引用的永不删 |
| `binary`（page render / figure / 大表） | 与所属 Snapshot 同生命周期 | 随 Snapshot 回收 |
| `staging`（未提交上传 / 临时转换） | TTL **24 小时** | orphan sweeper 按 Upload Session 过期时间回收 |

**物理 purge 策略**：必须由显式 purge request + 双重确认触发，异步执行，记录结果；失败可重试且可观测。不提供「自动永久删除」路径。

**需要你确认**：
- (a) source 的 7 天 grace 是否合适（太短/太长）？
- (b) 是否需要给 retention 天数一个集中配置项（如 `system/storage.yaml`），而不是硬编码？我倾向配置化。

---

## O5 Object Lock（阻塞 M1）

**背景**：SRS §15.3 建议「默认不开，仅在有 WORM 合规要求的独立 bucket 开启；bucket versioning 生产默认开启」。

**建议默认**：
- **Object Lock：OFF**（v1 未识别到 WORM 合规需求）。
- **Bucket versioning：生产环境 ON**（误删防护）。
- Object Lock 必须在建 bucket 时决定；若后续某 domain 出现 WORM 需求，新建独立 bucket 开启，不在现有 bucket 上补开。

**需要你确认**：当前是否有任何 domain 存在法规要求的 WORM 场景？若没有，按 OFF 推进。

---

## O6 归档默认导入语义（阻塞 M1）

**背景**：ZIP/RAR 上传时默认「保留为单文件」还是「展开为多文档」。SRS §15.3 建议「UI 明确选择，API 默认保留原归档，禁止静默展开」。

**建议默认**：
- **API 默认：保留归档为单个 Document**（不自动展开）。
- **UI 必须显式选择**「保留为单文件 / 展开为多个文档」。
- 选择展开时，受 §2.4 安全不变量约束：文件数、展开后大小、压缩比、路径穿越全部限制；每个展开成员形成独立 Document + Storage Object；按成员返回结果清单（默认允许部分成功，明确列失败项）。

**与现状的偏差（需特别确认）**：当前 `DocumentService.upload_zip()`（`document_service.py:68`）**默认自动解压**。新 MinIO 链路将把默认改为「不展开」。迁移期内旧路径可保留现有行为，但新路径上线即执行新默认。

**需要你确认**：新链路默认「不展开、需显式选择」是否可接受？这会改变用户既有上传习惯，需要 UI 配合。

---

## 审批方式

请逐条回复（例如「O1 同意 / O2 同意但 TXT 行范围 v0.1 不开 / O3 同意 …」）。全部通过后：
- 本 ADR 状态转 `Accepted`；
- O1/O2 进入 WP0.2 Parse IR 契约与 WP0.4 schema；
- O3/O4/O5/O6 进入 WP0.4 schema 与 WP1A/WP1B 实现约束。

SRS §15.4「不阻塞契约、由评测决定」的 5 项（fast path backend、中文复杂 PDF 选型、cloud fallback、PASS/WARN 阈值、PPTX/Email 支持范围）**不在 M0 冻结**，留待 WP6 路由与 WP13 评测，本 ADR 不涉及。

---

## 采纳决议（2026-08-13，自主采纳）

用户全权委托：「所有的决策请基于原始文档来决策……我不决策任何信息。」下列决议均以 SRS 条款为依据，由实施方自主采纳。

| 编号 | 采纳结果 | SRS 依据 |
|---|---|---|
| O1 | **采纳「指纹不含 document_id」**。指纹 = `sha256(domain, source_raw_hash, parser_fingerprint, workflow_graph_hash, compiler_fingerprint)`；`UNIQUE(domain, snapshot_fingerprint)`；document↔snapshot 多对多由 `asset_document_snapshot_links` 承载。子问 (b) `workflow_graph_hash` 复用现有列，待 WP2 核对。 | §8.3「相同内容跨 Document 复用」、§8.3A「UNIQUE(domain, snapshot_fingerprint)」 |
| O2 | **采纳建议粒度**。element + table cell 必存；字符范围对 PDF/DOCX/扫描/HTML/**TXT/Markdown** 存（TXT/Markdown 存 line/char range，**开启**，以支撑 §A01 line-addressable 验收）；word-level 默认关、按 route 可选；不做全量逐字。 | §15.2 建议、§A01、§7.4 |
| O3 | **采纳「同 domain 去重、跨 domain 禁止」**。 | §15.3 建议 |
| O4 | **采纳 retention 默认值，且 retention 天数走配置**（`system/storage.yaml`），不硬编码。source grace 7 天、staging TTL 24h、物理 purge 双重确认异步执行。 | §15.3 建议、§4.1A |
| O5 | **采纳 Object Lock OFF、bucket versioning 生产 ON**。 | §15.3 建议 |
| O6 | **采纳「新链路默认不展开、UI 显式选择」**。旧 `upload_zip` 自动解压行为在迁移期旧路径保留，新 MinIO 路径执行新默认。 | §15.3 建议、§4.3A |

后续实现级决策（命名、包结构、adapter 选型等）统一记入 [ADR-0003 自主决策日志](./0003-autonomy-decision-log.md)。
