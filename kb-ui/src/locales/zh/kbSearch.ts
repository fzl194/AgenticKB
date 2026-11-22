/** 检索质量簇文案（kb 组件·检索质量簇 8 文件）。 */
export const zhKbSearch = {
  // components/kb/KbSearchPanel.vue：检索面板
  // （「重试」复用 common.retry；「当前文档」复用 kbStruct.currentDoc；「文档」复用 kbSearch.graph.kindDocument）
  'kbSearch.panel.paradigmLabel': '检索范式',
  'kbSearch.panel.followOfficialPlaceholder': '跟随官方默认',
  'kbSearch.panel.bindingInvalid': '库级绑定失效，已降级',
  'kbSearch.panel.saving': '保存中…',
  // 绑定说明（源文件两行 → 按行各成一 key；模板里两段之间的换行折叠成一个空格，渲染结果与转换前一致）
  'kbSearch.panel.bindingHint1': '绑定后，这个库（含 MCP 检索）默认走所选管线；清除则跟随官方默认。',
  'kbSearch.panel.bindingHint2': '范式的检索范围保持"留空"即可随库注入——在这里测试即真实链路。',
  'kbSearch.panel.queryPlaceholder': '输入问题，用当前管线测试这个知识库的检索…',
  'kbSearch.panel.searchButton': '检索',
  'kbSearch.panel.anyDirectory': '不限目录',
  // 范围徽标（整句入字典；全角括号随语言切换）
  'kbSearch.panel.scopeTag': '范围：{title}（{mode}）',
  'kbSearch.panel.scopeWholeDoc': '整篇',
  'kbSearch.panel.scopeExact': '本节',
  'kbSearch.panel.scopeDescendants': '本节及子节',
  'kbSearch.panel.backToWholeDoc': '改回整篇',
  'kbSearch.panel.searchWholeKb': '搜索整个知识库',
  // 生效管线横幅
  'kbSearch.panel.effectiveLine': '本次由「{name}」管线执行',
  'kbSearch.panel.effectiveMeta': '（{source} · v{version}）',
  // 结果区
  'kbSearch.panel.evidenceCount': '证据 {n} 条',
  'kbSearch.panel.evidenceHasMore': '（预算内未全列，has_more）',
  'kbSearch.panel.evidence': '证据',
  'kbSearch.panel.truncatedTitle': '内容被预算截断；点「查看完整」取回全文',
  'kbSearch.panel.truncated': '截断',
  'kbSearch.panel.viewFull': '查看完整',
  'kbSearch.panel.collapse': '收起',
  'kbSearch.panel.sourcePrefix': '来源：',
  'kbSearch.panel.openOriginalTitle': '打开原文（原始预览）',
  'kbSearch.panel.locateSectionTitle': '在大纲中定位该章节',
  'kbSearch.panel.kbParens': '（{name}）',
  'kbSearch.panel.viewTable': '查看表格',
  'kbSearch.panel.goMining': '去挖掘出检索单元',
  // 脚本内提示与来源标签
  'kbSearch.panel.fetchFullFailed': '取回完整原文失败，请稍后重试',
  'kbSearch.panel.sourceLibrary': '库级绑定',
  'kbSearch.panel.sourceOfficial': '官方默认',
  'kbSearch.panel.sourceDegraded': '{source}（降级）',
  'kbSearch.panel.sourceWithKb': '{base}（{name}）',
  'kbSearch.panel.emptyNoUnits': '这个库还没有可检索的内容——先完成挖掘（全量基线），检索单元生成后即可搜索',
  'kbSearch.panel.emptyNoHit': '没有命中，换个问法或调整检索范式试试',
  'kbSearch.panel.bound': '已绑定本库检索范式',
  'kbSearch.panel.unbound': '已清除绑定，跟随官方默认',
  'kbSearch.panel.noParadigm': '该知识域未配置检索范式（库级/官方默认均缺失），请联系管理员在「检索范式」页发布范式。',
  // 位置徽标（A1）
  'kbSearch.panel.locatorPage': '第 {page} 页',
  'kbSearch.panel.locatorLines': '第 {first}–{last} 行',
  'kbSearch.panel.locatorLine': '第 {first} 行',
  'kbSearch.panel.noSourceRef': '该证据没有可定位的来源引用',
  'kbSearch.panel.selectedSection': '选中章节',

  // components/kb/KbSearchWorkspace.vue：检索工作台
  // （「检索记录」复用 title.retrievalRecords）
  'kbSearch.workspace.switchLabel': '知识库检索',
  'kbSearch.workspace.onlineTab': '在线检索',
  'kbSearch.workspace.fullRecords': '查看完整记录 →',

  // components/kb/TbTableQueryPanel.vue：表格查询面板
  // （「下一页」复用 records.pager.next；错误码 unknown_field 等是与服务端比对的匹配数据，留在代码里）
  'kbSearch.table.filterLabel': '筛选',
  'kbSearch.table.fieldPlaceholder': '字段',
  'kbSearch.table.fieldOption': '{name}（{type}）',
  'kbSearch.table.operatorPlaceholder': '操作',
  'kbSearch.table.addCondition': '+ 条件',
  'kbSearch.table.sortLabel': '排序',
  'kbSearch.table.noSort': '（不排序）',
  'kbSearch.table.ascending': '升序',
  'kbSearch.table.descending': '降序',
  'kbSearch.table.aggregationLabel': '统计',
  'kbSearch.table.noAggregation': '（不统计）',
  'kbSearch.table.countOption': '计数 count',
  'kbSearch.table.run': '查询',
  'kbSearch.table.aggregateRows': '（符合条件 {count} 行）',
  'kbSearch.table.visibleColumns': '显示列',
  'kbSearch.table.rowCount': '{n} 行',
  'kbSearch.table.moreUnlisted': '（更多未列）',
  'kbSearch.table.rerunAfterChange': '条件已修改，重新查询',
  'kbSearch.table.noMatchingRows': '无符合条件的行——调整筛选条件试试。',
  'kbSearch.table.schemaSummary': '列类型与可用操作',
  // 列能力面：<b>列名</b>（类型）：操作 —— 类型后的全角括号与冒号随语言切换
  'kbSearch.table.schemaTypeSuffix': '（{type}）：',
  // 类型化错误标题与兜底（标题与兜底文案共用）
  'kbSearch.table.queryFailed': '查询失败',
  'kbSearch.table.errorUnknownField': '字段不存在',
  'kbSearch.table.errorTypeMismatch': '类型不匹配',
  'kbSearch.table.errorUnsupportedOperation': '不支持的操作',
  'kbSearch.table.errorResultTooLarge': '结果过大',
  'kbSearch.table.errorUnavailable': '该表格暂不可精确查询',
  // 筛选操作符（符号类 = ≠ < ≤ > ≥ 语言中性，留在代码里）
  'kbSearch.table.opIn': '属于',
  'kbSearch.table.opContains': '包含',
  'kbSearch.table.opIsEmpty': '为空',
  // 字段类型（下拉标注、值输入占位、列能力面共用）
  'kbSearch.table.typeNumber': '数值',
  'kbSearch.table.typeDate': '日期',
  'kbSearch.table.typeText': '文本',
  'kbSearch.table.valueHintIn': '多个值，逗号分隔',
  'kbSearch.table.initFailed': '表格查询面板初始化失败',

  // components/kb/OnenetRefPanel.vue：一张网引用面板
  // （「操作」复用 common.actions；「{n} 篇」复用 dashboard.kbCard.docCount；
  //   「加载失败」「加载中…」复用 common.loadFailed / common.loading；「文档」复用 kbSearch.graph.kindDocument）
  'kbSearch.onenet.readonlyTitle': '只读视图：引用管理需要库主/编辑者权限',
  'kbSearch.onenet.referencedTitle': '本库引用的一张网文档（{n}）',
  'kbSearch.onenet.colSectionDirectory': '章节目录',
  'kbSearch.onenet.colSourceKb': '来源库',
  'kbSearch.onenet.preview': '预览',
  'kbSearch.onenet.unref': '取消引用',
  'kbSearch.onenet.emptyRefs': '尚未引用任何一张网文档——从下方导入池勾选一键引用',
  'kbSearch.onenet.importsTitle': '本域已导入的产品文档（管理员在「一张网接入」页维护）',
  'kbSearch.onenet.colFile': '文件',
  'kbSearch.onenet.colDirectory': '目录',
  'kbSearch.onenet.referenceSelected': '引用选中（{n}）',
  'kbSearch.onenet.expandHint': '展开加载产物文档…',
  'kbSearch.onenet.emptyImports': '本域还没有已导入的一张网文档（等管理员完成导入）',
  // 引用 / 取消引用的结果提示（「，跳过 {n}」是条件片段，自带标点）
  'kbSearch.onenet.added': '已引用 {n} 篇',
  'kbSearch.onenet.addedSkipped': '，跳过 {n}',
  'kbSearch.onenet.addFailed': '引用失败',
  'kbSearch.onenet.unreferenced': '已取消引用 {n} 篇',
  'kbSearch.onenet.unrefFailed': '取消引用失败',
  'kbSearch.onenet.previewTitle': '逻辑文档预览',
  'kbSearch.onenet.previewLoadFailed': '预览加载失败：',

  // components/kb/KbQualityPanel.vue：质量报告
  // （被 <b> 切开的句子按片段 key 入字典——R4；片段切分点=源码行边界：行尾逗号独立成 key，
  //   模板换行位置不变，zh 渲染逐字节一致；「检索单元」复用 dashboard.tile.units）
  'kbSearch.quality.refresh': '刷新质量报告',
  'kbSearch.quality.loading': '加载质量报告…',
  'kbSearch.quality.structureTitle': '结构完整度',
  'kbSearch.quality.sectionsTotal': '章节共',
  'kbSearch.quality.comma': '，',
  'kbSearch.quality.sectionsWithOrdinal': '有同级顺序',
  'kbSearch.quality.sectionsBridged': '大纲可跳转',
  'kbSearch.quality.unitsWithSection': '归属章节',
  'kbSearch.quality.unitsUnassignedHint': '（未归属章节可能与资料没有标题、格式支持或加工尚未完成有关）',
  'kbSearch.quality.tablesTitle': '表格完整度',
  'kbSearch.quality.tablesTotal': '表格',
  'kbSearch.quality.tablesQueryable': '可精确查询',
  'kbSearch.quality.cellsTotal': '单元格',
  'kbSearch.quality.cellsTyped': '类型化',
  'kbSearch.quality.cellsUntypedHint': '（未识别类型可能与空值、资料格式或加工状态有关）',
  'kbSearch.quality.unqueryableTitle': '不可精确查询的表格（原因）：',
  'kbSearch.quality.locatorTitle': '来源定位覆盖',
  'kbSearch.quality.locatorResolved': '可解析',
  'kbSearch.quality.locatorDenominatorHint': '（分母=声明可定位格式的片段）',
  'kbSearch.quality.degradedCount': '降级说明 {n} 条',
  'kbSearch.quality.degradedHint': '（native/section_only/unavailable——有原因记录）',
  // 报告脚注（源文件两行 → 按行各成一 key）
  'kbSearch.quality.footnote1': '报告统计当前可搜索知识。存在缺口时，请先查看原文件和挖掘记录，',
  'kbSearch.quality.footnote2': '确认资料结构、格式支持和加工情况，再决定是否补充资料或重新挖掘。',

  // components/kb/DocumentStructureGraph.vue：文档结构图
  // （「未命名文档」复用 kbStruct.untitledDoc；内容组类型 正文/列表/代码/公式/图片说明 复用 kbStruct.block.*；
  //   「切片 {n}」复用 kbView.detail.readinessSegments。kind* 是文档/章节/表格/内容组的通名，
  //   KbSearchPanel 的来源回落名与 OnenetRefPanel 的「文档」列头也复用 kindDocument）
  'kbSearch.graph.title': '文档结构浏览',
  'kbSearch.graph.intro': '点击章节或表格直接查看当前版本返回的真实内容；不包含实体、本体或业务关系推断。',
  'kbSearch.graph.statsLabel': '结构化数据统计',
  'kbSearch.graph.statSections': '章节 {n}',
  'kbSearch.graph.statTables': '表格 {n}',
  // 部分返回提示（源文件两行 → 按行各成一 key）
  'kbSearch.graph.partialNotice1': '当前工作台仅展示接口已返回的内容：切片 {returned}/{total}，',
  'kbSearch.graph.partialNotice2': '元素 {returned}/{total}。完整列表请查看高级信息。',
  'kbSearch.graph.navigationTruncated': '文档结构较大，左侧仅展示前 {max} 个入口；可通过高级信息查看完整清单。',
  'kbSearch.graph.filterLabel': '筛选结构',
  'kbSearch.graph.searchPlaceholder': '搜索章节标题或表格字段',
  'kbSearch.graph.localHint': '中间仅展示当前节点的上下文，避免大文档一次铺满',
  'kbSearch.graph.outlineLabel': '文档结构大纲',
  // 大纲 / 画布节点的一字标记（英文用缩写）
  'kbSearch.graph.markerDocument': '文',
  'kbSearch.graph.markerSection': '节',
  'kbSearch.graph.markerTable': '表',
  'kbSearch.graph.markerUnassigned': '未',
  'kbSearch.graph.markerGroup': '组',
  'kbSearch.graph.documentOverview': '文档概览',
  'kbSearch.graph.searchMissTruncated': '未在已展示的前 {max} 个入口中匹配到',
  'kbSearch.graph.searchMiss': '没有匹配的章节或表格',
  'kbSearch.graph.contextLabel': '当前结构上下文',
  'kbSearch.graph.localTitle': '局部结构',
  'kbSearch.graph.nodeCount': '{n} 个节点',
  'kbSearch.graph.localTruncated': '当前节点共 {total} 个直接下级，画布仅展示前 {shown} 个，完整层级见左侧导航',
  'kbSearch.graph.inspectorTitle': '内容详情',
  'kbSearch.graph.documentHint': '选择左侧章节或表格，在这里查看当前所选版本的真实内容。',
  'kbSearch.graph.columnFallback': '列 {n}',
  'kbSearch.graph.tablePreviewOnly': '当前仅显示预览数据，共 {n} 行。',
  'kbSearch.graph.tableParentUnavailable': '该表格的所属章节暂不可用，未根据标题猜测归属。',
  'kbSearch.graph.noBodyChunks': '当前返回数据中没有该节的正文切片。',
  'kbSearch.graph.partialContent': '这是当前接口已返回的内容，不代表完整章节。',
  // 版本眉标
  'kbSearch.graph.versionLatestInSearch': '最新解析 · 已进入搜索',
  'kbSearch.graph.versionLatestNotInSearch': '最新解析 · 尚未进入搜索',
  'kbSearch.graph.versionCurrentServing': '当前可搜索版本 · 确定性结构层',
  'kbSearch.graph.versionSelectedResult': '当前所选解析结果 · 确定性结构层',
  // 节点类型通名（Inspector 角标 / 摘要 <dt> / kindName 共用）
  'kbSearch.graph.kindDocument': '文档',
  'kbSearch.graph.kindSection': '章节',
  'kbSearch.graph.kindTable': '表格',
  'kbSearch.graph.kindContentGroup': '内容组',
  'kbSearch.graph.chunk': '切片',
  'kbSearch.graph.kindStructure': '结构',

  // components/kb/PipelineFlow.vue：阶段流水线
  // （阶段名与 Run 详情 / Run 文档详情共用 kbView.run.stage.* / kbView.runDoc.stage.*；
  //   「检索单元」复用 dashboard.tile.units；「向量化」复用 common.miningOperator.embedding；
  //   「失败」复用 common.runStatus.failed）
  'kbSearch.pipeline.lineOntology': '本体线',
  'kbSearch.pipeline.lineDiscourse': '篇章线',
  'kbSearch.pipeline.statusRunning': '运行中...',
  'kbSearch.pipeline.statusPending': '等待中',
  'kbSearch.pipeline.stageBuild': '构建&发布',

  // components/kb/PreprocessNotice.vue：预处理结果提示
  // （「已解析」复用 kbView.detail.readinessLevel.parsed）
  'kbSearch.notice.titleSuccess': '预处理成功',
  'kbSearch.notice.titlePartial': '部分解析成功',
  'kbSearch.notice.titleFailed': '解析失败',
  'kbSearch.notice.titleDefault': '预处理结果',
  'kbSearch.notice.hintDocConverterUnavailable': '请在 Linux 环境离线安装 LibreOffice，或先将文件转换为 .docx 后重新上传。',
  'kbSearch.notice.hintExcelPassword': '暂不支持密码保护的 Excel 文件，请移除密码后重新上传。',
  'kbSearch.notice.summarySheets': '工作表',
  'kbSearch.notice.summaryEmptySheets': '空工作表',
  'kbSearch.notice.summaryTableRegions': '表格区域',
  'kbSearch.notice.summaryNonEmptyCells': '非空单元格',
  // 告警位置：全角括号随语言切换，内部是 "sheet / 单元格"
  'kbSearch.notice.warningLocation': '（{location}）',
} as const
