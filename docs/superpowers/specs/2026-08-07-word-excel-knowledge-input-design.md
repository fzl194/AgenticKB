# Word 与 Excel 作为知识库挖掘输入的方案设计

日期：2026-08-07  
状态：设计已确认，待实施计划

## 1. 背景与现状

知识库当前允许上传任意普通文件，旧上传接口也已显式接受 `.doc` 和 `.docx`。挖掘摄取链路已经具备以下 Word 能力：

- `.docx` 通过 `python-docx` 解析为结构树；
- `.doc` 先通过 LibreOffice，或 Windows Word COM，转换为临时 `.docx`，再复用 `.docx` 解析器；
- 转换或预处理失败会写入文档元数据，但目前主要按“跳过”呈现，错误信息对上层暴露不够完整。

当前尚未识别或解析 `.xls`、`.xlsx`，因此这些文件即使能够上传，也不会成为有效挖掘输入。

## 2. 目标

本次改造实现：

1. `.doc`、`.docx` 可作为知识库挖掘输入，并对转换器缺失或转换失败提供明确、可查询的错误。
2. `.xls`、`.xlsx` 可作为知识库挖掘输入。
3. Excel 先转换成 Markdown 中间态，再复用已有 Markdown 结构解析、分段、实体抽取、检索单元、向量化和持久化链路。
4. 保留工作簿、工作表、表格区域、表头和原始行列范围等上下文，使表格数据可检索、可追溯。
5. 单个文件或单个工作表出错时，将错误暴露给 API/UI，同时尽量继续处理其他可用内容和其他文档。
6. Linux 离线部署不依赖在线下载；Excel 解析不新增 LibreOffice、Java 或云端服务依赖。
7. 除非发现现有字段无法承载信息，否则不修改数据库结构。

## 3. 非目标

第一阶段不处理：

- Excel 图表和嵌入图片；
- VBA 宏及宏执行；
- 密码保护或加密的 Excel/Word 文件；
- Excel 公式计算；
- Excel 外部链接刷新；
- `.xlsm`、`.xlsb`、`.ods`；
- 对原始 Excel 文件进行修改或回写；
- 把 Excel 转换成可下载的永久 Markdown 副本。

以上内容必须显式忽略或报告，不能静默伪装为完整解析。

## 4. 总体架构

### 4.1 组件边界

新增独立的 Excel 预处理模块，例如：

`knowledge_mining/mining/ingestion/excel_preprocessing.py`

该模块只负责：

- 打开 `.xls` 或 `.xlsx`；
- 标准化单元格值和合并单元格；
- 识别非空工作表与独立表格区域；
- 将内容确定性地渲染为 Markdown；
- 返回结构化统计、告警和错误。

它不负责上传、数据库写入、挖掘任务状态或向量化。摄取模块负责调用预处理器，并将结果装配为现有 `RawFileData`。

### 4.2 依赖选择

- `.xlsx`：`openpyxl`；
- `.xls`：`xlrd`；
- `.docx`：继续使用现有 `python-docx`；
- `.doc`：继续使用现有 LibreOffice/Word COM 转换链。

不采用 Java/Apache Tika、云端 Document AI 或将 Excel 交给 LibreOffice 转换。`openpyxl` 与 `xlrd` 可作为 Python wheel 在联网构建环境中提前下载，并随离线安装介质或内部制品仓库分发。

### 4.3 主数据流

```text
原始 .xls/.xlsx
  -> 扩展名识别与原始字节哈希
  -> Excel 预处理器
  -> Markdown 字符串 + 解析摘要/告警
  -> RawFileData(file_type="markdown", source_format="xls|xlsx")
  -> 现有 MarkdownParser
  -> 现有 segment/enrich/retrieval/embedding/persist 流程
```

原始文件仍是知识库文档身份、下载和溯源的唯一文件。Markdown 中间态仅存在于单次摄取/挖掘进程的内存中，不写回上传目录。

## 5. 格式接入

需要在现有格式入口统一加入：

- `.xls -> excel`
- `.xlsx -> excel`

同时补充：

- 旧上传接口 `accepted_extensions`；
- 摄取扩展名映射；
- MIME 映射：
  - `.xls`: `application/vnd.ms-excel`
  - `.xlsx`: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- API 和前端可见的支持格式说明；
- ZIP 解压后的递归摄取，无须建立第二套处理路径。

知识库文档上传接口当前没有普通文件白名单，实施时不得只改 UI 或旧上传接口，而遗漏真正决定能否挖掘的摄取扩展名映射。

## 6. Excel 到 Markdown 的转换规则

### 6.1 工作表

- 按原始顺序遍历工作表；
- 跳过全空工作表，并记录在摘要中；
- 隐藏工作表仍解析，在标题和元数据中标记 `hidden=true`；
- 去除工作表外围完全为空的行和列；
- 工作表名称作为二级标题，文件名作为一级标题。

示例：

```markdown
# 设备台账.xlsx

## 工作表：核心网设备
```

### 6.2 表格区域识别

在工作表有效范围内，使用完全空白的行或列作为区域分隔线，递归切分相互独立的数据区域。这样可以处理同一工作表中的上下排列或左右排列的多张表，而不是将整个 Sheet 强制视为一张表。

每个区域保留：

- 工作表名；
- Excel 范围，如 `A4:F28`；
- 起止行列；
- 行列数量；
- 是否包含合并单元格；
- 分块序号。

仅有一列的区域可渲染为段落或列表，避免产生无意义的单列表格。完全孤立的说明性文本应作为所在工作表的普通段落保留。

### 6.3 合并单元格

合并区域使用左上角单元格的值填充到该合并范围的逻辑视图中。该操作只发生在中间态，不修改原文件。这样可以让多级表头的上级字段在每一列上保留上下文。

### 6.4 表头识别

每个表格区域最多识别前三行为表头。判断依据包括：

- 行内非空文本比例；
- 后续行的数据类型稳定性；
- 合并单元格关系；
- 字段值在后续行中的重复/唯一模式。

多行表头按 `上级/下级` 组合为最终字段名。若无法可靠识别，则不消费第一条数据，改用 `列A`、`列B` 等稳定列名。识别规则必须是确定性的，相同文件重复挖掘产生相同 Markdown 和标准化哈希。

### 6.5 单元格值

- 字符串去除无意义的首尾空白，保留内部换行语义；
- Markdown 管道符、反斜杠和换行进行转义；
- 日期和时间输出稳定的 ISO 风格文本；
- 百分比按百分数语义输出；
- 布尔值输出统一文本；
- 数字使用稳定、无科学计数法抖动的表示；
- 错误值保留为可读错误标记并产生告警；
- 空单元格输出为空，不填造业务值。

`.xlsx` 使用 `data_only=true` 读取文件最后保存的公式计算结果。必要时以第二个只读工作簿实例读取公式文本，用于判断“存在公式但无缓存值”。不执行公式，不访问外部链接。`.xls` 读取文件内已保存的值；由于旧格式读取器无法在所有情况下可靠区分空值与缺失的公式缓存，只保证值提取，不承诺完整公式诊断。

### 6.6 Markdown 表格与大表分块

小表直接输出标准 Markdown 表格。大表在预处理阶段按完整行切块，不允许把单行拆到两个表格块中。每个分块重复：

- 文件名和工作表上下文；
- 表格区域；
- 完整表头；
- 原始行号范围。

表格块目标不超过约 420 tokens，为现有默认 512-token 分段上限预留标题和结构上下文。若单行自身已超过目标，则该行独立成块，后续现有超长文本保护逻辑继续兜底。

示例：

```markdown
### 表格 A4:F28（第 1/3 块，原始行 5-12）

| 设备名称 | 厂家 | 型号 | 状态 |
| --- | --- | --- | --- |
| AMF01 | 华为 | ... | 运行中 |
```

## 7. 解析结果模型

Excel 预处理器返回一个内部结果对象，至少包含：

- `markdown: str`
- `status: success | partial | failed`
- `source_format: xls | xlsx`
- `sheet_count`
- `parsed_sheet_count`
- `skipped_empty_sheet_count`
- `table_region_count`
- `nonempty_cell_count`
- `warnings[]`

每条告警至少包含：

- `code`
- `message`
- `sheet_name`（若适用）
- `cell_range`（若适用）

该结果由摄取层转写到 `RawFileData.content` 与 `RawFileData.metadata_json`。不把 Excel 专用字段加入通用数据库列。

## 8. 错误处理与可观测性

### 8.1 稳定错误码

至少定义：

- `excel_dependency_missing`
- `excel_password_protected`
- `excel_corrupt_file`
- `excel_no_usable_content`
- `excel_limits_exceeded`
- `excel_sheet_parse_failed`
- `excel_formula_cache_missing`
- `doc_converter_unavailable`
- `doc_conversion_failed`

### 8.2 成功、部分成功与失败

- `success`：所有可处理工作表均成功转换；
- `partial`：至少生成一个有效内容块，但存在工作表、单元格或公式告警；继续挖掘并持久化告警；
- `failed`：工作簿无法打开、被加密、依赖缺失、文件损坏、超过安全限制或无任何可用内容。

预处理致命失败的文档应标为 `failed`，而不是仅标为含义模糊的 `skipped`。单个文档失败不终止整次运行；其他文档继续处理，总体汇总准确增加失败计数。

### 8.3 上层暴露

复用现有 `mining_run_documents.error_message` 和 `metadata_json`：

```json
{
  "preprocess_status": "partial",
  "preprocess_error_code": null,
  "preprocess_error": null,
  "preprocess_warnings": [
    {
      "code": "excel_formula_cache_missing",
      "message": "公式没有已保存的计算结果",
      "sheet_name": "汇总",
      "cell_range": "F18"
    }
  ],
  "excel_summary": {
    "sheet_count": 4,
    "parsed_sheet_count": 3,
    "table_region_count": 7,
    "nonempty_cell_count": 1850
  }
}
```

运行文档列表与详情 API 增加派生响应字段：

- `preprocess_status`
- `error_code`
- `error_detail`
- `warnings`
- `excel_summary`

这些字段从现有 JSON 元数据展开，不新增数据库列。前端运行详情显示“解析成功 / 部分成功 / 解析失败”，并允许展开工作表级告警。

### 8.4 日志

服务端日志包含：

- 稳定错误码；
- `run_id`；
- 运行文档 ID 或文档键；
- 文件名；
- 工作表名和范围（若适用）；
- 完整异常堆栈。

日志不得输出单元格业务内容。API 只返回安全、简短、可操作的错误说明，不返回内部文件系统绝对路径或完整堆栈。

## 9. 数据库策略

本方案不需要数据库迁移。

复用字段：

- `mining_run_documents.status`
- `mining_run_documents.error_message`
- `mining_run_documents.metadata_json`
- 文档快照及链接已有的 `metadata_json`

工作表摘要、告警和预处理状态均写入 JSON。只有在实施中证明现有字段存在无法规避的查询或容量约束时，才单独提出数据库变更，不在本功能中预先增加 Excel 专用表或列。

## 10. 安全与资源限制

沿用现有普通文件 100 MB 上传上限，并在预处理器增加内存/CPU 防护。默认值通过现有 `main_control_service/config/system/mining.yaml` 的可选 `excel` 配置段提供，缺省时使用代码默认值，不涉及数据库：

```yaml
excel:
  max_sheets: 200
  max_nonempty_cells: 1000000
  table_chunk_target_tokens: 420
```

超过限制时返回 `excel_limits_exceeded`，不得截断后伪装为完整成功。读取 `.xlsx` 时禁止外部链接刷新和宏执行；读取器仅做本地只读解析。临时对象随单次文档处理释放，不生成永久中间文件。

## 11. 离线部署设计

### 11.1 Python 依赖

`pyproject.toml` 作为声明源，同时同步运行环境实际使用的依赖清单。联网构建机提前下载与目标 Linux/Python 版本匹配的 wheel：

- `openpyxl`
- `et-xmlfile`
- `xlrd`

离线环境通过内部制品仓库或 wheelhouse 安装，并使用 `--no-index --find-links`。交付文档必须说明 Python 版本、平台架构和 wheel 校验方式，避免在目标机器上触发源码构建或公网下载。

### 11.2 LibreOffice

Excel 功能不依赖 LibreOffice。`.doc` 转换在 Linux 上仍需要系统级 LibreOffice：

- 推荐在基础镜像或离线系统镜像制作阶段安装；
- 离线介质需包含目标发行版及版本匹配的 RPM/DEB 和依赖；
- 运行时通过 `soffice`/`libreoffice` 可执行文件探测；
- 缺失时只使对应 `.doc` 文档失败，并暴露 `doc_converter_unavailable`；
- `.docx`、`.xls`、`.xlsx` 和其他文档不受影响。

部署文档应提供能力自检命令或应用内预检结果，使运维在首次挖掘前即可发现依赖缺失。

## 12. 前端行为

- 上传入口允许选择 `.doc`、`.docx`、`.xls`、`.xlsx`；
- 支持格式提示与后端 `/api/uploads/config` 保持一致；
- 运行列表延续现有文档状态；
- 运行文档详情增加预处理信息区域；
- `partial` 使用告警样式，不误显示为完全失败；
- `failed` 显示错误码对应的用户可操作建议；
- 原文件预览能力不在本次范围，仍可下载后使用本地 Office 软件查看。

## 13. 测试策略

### 13.1 单元测试

`.xlsx`：

- 单工作表与多工作表；
- 空工作表、隐藏工作表；
- 单行与多行表头；
- 合并单元格；
- 多个上下/左右数据区域；
- 日期、百分比、布尔、数字、错误值；
- Markdown 特殊字符与单元格换行；
- 有缓存值和无缓存值的公式；
- 大表按 token 目标切块并重复表头；
- 加密、损坏、超限和空工作簿。

`.xls`：

- 与 `.xlsx` 等价的基础工作表、表头、合并单元格和数据类型用例；
- 旧格式日期系统；
- 损坏和超限文件。

`.doc`：

- LibreOffice 可用时成功转换；
- LibreOffice 不存在时稳定错误码；
- 转换命令失败时安全错误信息；
- 原有 Word COM 回退测试保持通过。

### 13.2 集成测试

- 通过知识库 API 上传 `.xls/.xlsx` 并触发挖掘；
- ZIP 中包含 Excel 时可被发现和挖掘；
- Excel 被转为 `markdown`，同时保留原始 `source_format`；
- 成功文档生成快照、分段和检索单元；
- 部分成功文档完成挖掘并在 API 返回 warnings；
- 致命失败文档状态为 `failed`，其他文档继续；
- 运行列表和详情 API 返回新增派生字段；
- 不执行数据库迁移即可通过现有数据库测试。

### 13.3 前端测试

- 文件选择器支持新增扩展名；
- success/partial/failed 显示正确；
- 错误详情与工作表告警可展开；
- 缺失字段时兼容旧运行记录。

### 13.4 离线部署验证

- 在禁止公网访问的 Linux 环境中从 wheelhouse 安装并运行 Excel 测试样例；
- 未安装 LibreOffice 时，Excel 与 `.docx` 正常，`.doc` 返回预期错误；
- 安装离线 LibreOffice 包后，`.doc` 转换测试通过；
- 应用启动和运行期间没有隐式联网下载。

## 14. 验收标准

1. 用户可上传 `.doc`、`.docx`、`.xls`、`.xlsx` 并选择其作为知识库挖掘输入。
2. 典型 `.xls/.xlsx` 的所有非空工作表均生成带工作表与范围上下文的知识片段。
3. 大表分块后每块保留表头，任一检索结果都能定位到原始工作表和行范围。
4. 图表、图片、宏不被处理；密码保护文件明确失败。
5. 公式不在服务端计算，使用已保存结果；缺少结果时产生可见告警。
6. 解析失败和部分失败可从 API 与前端查看，并在服务端日志中关联到运行和文档。
7. 单个坏文件不会终止同批其他文档。
8. 不新增数据库表或列，不执行数据库迁移。
9. Linux 断网环境中可从准备好的 wheelhouse 安装 Excel 依赖并完成挖掘。
10. 缺少 LibreOffice 时 `.doc` 给出可操作错误，且不影响 `.docx/.xls/.xlsx`。

## 15. 风险与取舍

- 自动表头与数据区域识别无法覆盖所有人为排版的 Excel；设计选择确定性回退，而不是用 LLM 猜测结构。
- Markdown 适合知识检索，但不等同于数据库查询；精确聚合、排序和计算不在本阶段目标中。
- `.xls` 对公式和格式的可观测能力弱于 `.xlsx`，因此两者保证相同的内容可挖掘能力，但不保证相同的公式诊断能力。
- 预处理大表会增加 CPU 和内存消耗，因此必须保留上传大小与单元格数量双重限制。
- `.doc` 的 Linux 支持仍受系统级 LibreOffice 影响；本次通过离线部署说明、能力预检和明确错误暴露降低运维风险，而不引入新的转换实现。
