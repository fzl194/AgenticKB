# 里程碑 M3A：多格式原生解析 fast-path（纯代码混合路线）—— 交付报告

> **⚠️ 2026-08-17 整改轮状态声明**：本里程碑原名 "M3"，经全格式审计后
> **重定义为 M3A（原生解析 fast-path）**，不构成 SRS §14 原规划 M3
> （"至少 native/Docling 两条 route"）的完成。未达成项见文末 §M3 缺口。
> 本轮整改内容（跨格式 IR 不变量 / 逐格式修复 / Reconciler / Quality
> Gate / raw replay / golden corpus）见审计文档与 ADR-0003 D-032。

> 日期：2026-08-17
> 分支：`feat/doc-parse-platform-m0`
> 决策留档：ADR-0003 **D-028 / D-028A**（契约演进 + 用户路线拍板）
> 上游：M0（契约）+ M1（MinIO 文件地基）+ M2（影子链路）

## 1. 范围与需求对齐（用户拍板）

SRS §14 M3 = WP5/WP6/WP7 初版，退出条件"至少 native/Docling 两条 route 可运行，结果统一进入 IR"。

**用户对齐结论（2026-08-14/17，详见 ADR D-028A）**：
1. **纯代码混合路线**：不引入本地 AI 模型（Docling 3-6GB 暂缓），全部用工业级成熟库
2. **不重复造轮子**：自研代码只做"库输出 → Parse IR"映射，不写解析算法
3. **OCR 暂不做，预留云端接口**（backend_kind="cloud" 槽位，用户将来配置模型即插即用）
4. **验收语料**：自造 fixture 自动化 TDD + 用户后续提供真实文档人工验收

## 2. 交付物

### 2.1 契约演进 v1.1（M3.0，TDD：RED 25 failed → GREEN 46 passed）
- `DocumentParser.parse(data: bytes)`——二进制格式必需；decode 责任移入文本适配器（坏字节包 `ParserAdapterError`）
- `BackendBlock` 增加结构化定位字段（`container_ref` / `bbox` / `native_ref`，全可选，M2 零破坏）
- `ParserDescriptor.note` 可选字段（占位槽位说明）

### 2.2 File Inspector（M3.1，C03，23 用例）
`file_inspector/inspect.py`：`DocumentProfile`（格式/容器数/加密/文本层）——复用 `safe_intake.detect_mime` 签名探测，ZIP→OOXML 消歧，PDF 经 pdfplumber 取页数+抽样文本层（BytesIO 不落盘），加密标记不伪造。

### 2.3 Parser Router + 云端槽位（M3.2，C05，13+6 用例）
- `ParserRouter.plan(profile) -> RouteDecision`：确定性规则 + reason codes（`no_text_layer_needs_ocr`、`ocr_reserved_cloud`、`unsupported_format`）；按 registry 查 local+license=ok 的 backend，**不硬编码 parser_id**
- Registry 槽位：`docling`（pending_review，不会被选中）+ `cloud_vlm`（unconfigured，note 注明用户配置位置）

### 2.4 原生适配器 ×5（M3.3/M3.4，各 7-15 用例）
| 格式 | 库 | 保真点 |
|---|---|---|
| DOCX | python-docx | style API 标题树、gridSpan/vMerge 合并格、paragraph_index 证据 |
| XLSX | openpyxl | workbook→sheet 容器层级、双读（公式/展示值分离）、合并区域展开、cell A1 native_ref 证据 |
| PPTX | python-pptx | slide 容器（不伪造页码）、EMU bbox、title placeholder→heading |
| HTML | lxml | DOM 容器、xpath 证据、rowspan/colspan 直读、无 charset 强制 UTF-8 防 mojibake |
| PDF | pdfplumber | 页容器、行级 bbox 证据、find_tables 表格网格+span 推导、字号启发式 heading（confidence.type=0.6 如实降权） |

公共骨架 `native/_base.py`（类型映射/heading 弹栈/阅读序/stable id/强制 validate），M2 文件零改动。

### 2.5 集成层（M3.5）
- `parse_adapters/factory.py`：parser_id → (parser, normalizer) 成对解析——实现类↔descriptor 的单一事实源
- `build_default_registry()` 注册全部 7 个已实现 parser + 2 占位槽位
- 全链路集成测试（12 用例）：Inspector→Router→工厂→ShadowParseService ×7 格式

## 3. 验证结果

```text
单元+集成：parse_adapters + file_inspector + shadow_parse → 133 passed, 1 skipped
scoped 回归（M0-M3 全模块）：517 passed, 6 skipped   （M2 时 429 → +88）
真实环境 e2e（真 MinIO + 真 PG kb_db，7 格式）：
  md:  route=legacy_markdown  elements=3  containers=[section]
  txt: route=legacy_txt       elements=2  containers=[section]
  docx:route=native_docx      elements=4  containers=[section]      （标题树✓）
  xlsx:route=native_xlsx      elements=1  containers=[sheet,workbook]（层级✓）
  pptx:route=native_pptx      elements=1  containers=[slide]        （EMU✓）
  html:route=native_html      elements=3  containers=[dom_document] （xpath✓）
  pdf: route=native_pdf       elements=2  containers=[page]         （bbox✓）
  全部 sha_ok=True；发布表零污染；cleanup done
```

## 4. SRS M3 退出条件核对

| 退出条件 | 结果 |
|---|---|
| 至少 native/Docling 两条 route 可运行 | ✅ **7 条** route（legacy×2 + native×5）全链路真实环境可运行 |
| 结果统一进入 IR | ✅ 全部经 Parse IR schema validation + round-trip，统一落 parse bucket + 投影 |

## 5. TDD 执行记录（用户要求）

每个工作包严格 RED→GREEN，证据留存于各代理交付报告：
- M3.0 契约演进：先改 25 处测试调用（RED）→ 实现（GREEN）
- M3.1/3.2：36 用例先写（RED=ModuleNotFoundError）→ 实现（GREEN）
- M3.3：28 用例分格式 RED→GREEN（HTML rowspan 行号 bug 在 RED 阶段被测试抓住）
- M3.4：15 用例 RED→GREEN（fixture 为手写最小 PDF 构造器）
- M3.5：3 用例 RED（registry 未注册 native）→ GREEN

## 6. 已知缺口

1. **PDF heading 启发式**是映射级规则（字号 1.15×众数），复杂版面误判率未在真实语料上评估——待用户提供真实文档验收
2. **无文本层 PDF**（扫描件）产 warning 不解析——待云端 OCR 槽位配置后接入
3. PDF 跨页表格 continuation、页眉页脚去重——M4 Reconciler 职责
4. **openpyxl 库级边界**：`mergeCell A1:XFD1048576` 巨型合并区域会让 openpyxl **打开期**挂死（发生在适配层截断逻辑之前）；适配层已防"能打开但网格巨大"形态（稀疏远端格→截断 10k+可见标记），该形态依赖上游 M1 intake 文件级限制兜底
5. 保真缺口（评审 LOW，M4 范围）：PPTX group shape、DOCX 嵌套表格/内联图片未遍历；figure 无 FigureAsset
6. 真实文档验收（用户提供 2-3 份）待做
7. golden corpus benchmark 未建（用户已明确不需要评测体系）

## 6A. 评审与修正记录（ADR-0003 D-029）

评审发现 2 HIGH + 5 MEDIUM，**全部于合入前修复**并补回归用例：
- HIGH-1 DOCX 矩形合并（多列同源 vMerge）row_span 多计 → 按行去重 + 回归测试
- HIGH-2 HTML/XLSX 不可信声明几何 DoS → span/网格/合并面积上限 + `clamped_*` 可见标记 + 回归测试
- MEDIUM：迭代期第三方异常归一（4 适配器）、HTML 嵌套表格行归属、IR 表格数据双份存储、PPTX 跨 slide 父链污染
- 评审另提示 auth.yaml 含真实密钥（会话早期用户侧改动）——**本次提交继续排除该文件**，建议后续将密钥改为部署侧注入

## 7. 如何验证

```bash
cd knowledge_mining && python -m pytest tests/parse_adapters/ tests/file_inspector/ tests/shadow_parse/ -q
# 133 passed, 1 skipped
```

## 8. 留给 M4+

- Quality Gate（C09）+ Reconciler（C08）：质量决策 PASS/WARN/REPAIR/FAIL、跨页表格、页眉页脚
- Snapshot 正式提交（WP9）：影子制品转正 + SUPERSEDED 语义
- 云端 OCR/VLM 槽位实现（用户提供模型配置后）
- Docling 真实接入（如未来需要复杂版面保真增强）

---

## 整改轮（2026-08-17/18）：M3 → M3A 重定义与地基整改

全格式审计（`docs/文档解析平台化-全格式审计与整改-2026-08-17.md`）发现
8 条跨格式不变量违规与逐格式结构缺陷，按用户指令整改：

### 修复汇总

| 领域 | 内容 |
|---|---|
| 契约 v1.2 | `ParseRuleConfig`（阈值指纹）+ `BackendParseArtifact.to_dict/from_dict`（持久化/replay 前提）+ `effective_pipeline_fingerprint()` + bbox 顺序校验（`invalid_bbox_order`） |
| 不变量 I-1..I-8 | 跨格式 contract tests 统一断言（bbox 角点、表格 Element.text 统一渲染且可由 TableAsset 重算、cell 独立 source_span_id、指纹敏感性、replay 等价、结构诊断不静默、类型不泄漏） |
| DOCX | w:numPr 列表语义（list_item 层级）；cell 级 OOXML 证据；嵌套表 XML 层抽取；图片/页眉页脚/脚注/批注/文本框计数诊断 |
| XLSX | Excel Table → 连续数据区域 → used_range 三级识别（不再整 Sheet 一张表）；隐藏行列注记；图表/图片诊断；公式/展示值双读保持 |
| PPTX | bbox 改 (x0,top,x1,bottom)；text_frame 逐段落拆分（bullet→list_item）；几何带阅读序（不再等同 XML 序）；notes 保留；group 递归；picture→FigureAsset+binary（sha256）；chart/SmartArt 诊断 |
| HTML | 嵌套列表独立成元素（父项不再吞子项文本）；links 进 annotations；figcaption/caption→caption 元素；语义容器路径；rowspan×colspan **面积上限**（防 10⁸ occupied DoS，实测修复前 105s） |
| PDF | 双栏跨栏粘连（跨沟行才当通栏锚）；数字开头真标题误杀（"3D Printing…"）；跨栏同带标题误杀（dense_frags 只用行内字符）；表格 bbox/cell 一致（收缩框过滤+紧凑重排+cell bbox 证据）；纯散文页不再进 text 表格回退；家具规则迁出 |
| MD/TXT | 图片/链接计数诊断；TXT 无 token 切片（守卫固化）；退化输入用例 |
| Reconciler（C08 最小） | furniture_typing（迁入）、caption_binding、table_continuation、paragraph_continuation；patch log；reconciler_version 回写 |
| Quality Gate（C09 最小） | 六类指标（字符覆盖率/结构准确率/表格完整率/证据可定位率/阅读序/warning 分布）+ PASS/WARN/FAIL |
| shadow 链路 | backend raw artifact 持久化（artifact_class=backend_raw）+ `renormalize()` replay（不重跑 parser，§9.5/A09）；reconciler/quality_gate 可选注入 |
| golden corpus | 50 份（7 格式 × 正例/反例/复杂/退化）+ `tools/golden_benchmark.py` + 阈值守卫测试 |

### 基准结果（50 份）

```text
字符覆盖率 1.000 | 结构准确率 0.993 | 表格 cell 证据 0.944 | 网格一致性 1.000
证据可定位率 0.898 | 阅读序单调性 1.000
决策分布：PASS 43 / WARN 1 / FAIL 5（全部为退化空样本）/ PARSE_FAILED 1（负例）
```

### M3 缺口（原规划 M3 未达成项——不得以"已预留"冒充"已支持"）

1. **无第二真实后端**：只有 native fast-path 七条 route；Docling/云端
   parser 未接入（cloud_vlm 仍是 license!=ok 的占位槽位）；
2. **Router policy 未版本化**：路由规则内嵌代码，无版本化 policy 配置；
3. **无 fallback attempts**：单 primary 失败即终态，无 Parse Plan 回退链；
4. **无 Parse Operator（WP7）**：完整 Parse Run 状态机/attempt 事件/
   超时取消未实现（影子链路仍为 SUCCEEDED/FAILED 两态）；
5. ~~backend raw replay~~（本轮已补）；~~golden corpus benchmark~~（本轮已补）。

以上 1-4 归 M3B/M4。
