/** kb 视图文案（kb 视图 5 文件 + Run 详情的挖掘轨迹 MiningWorkflowTrace）。 */
export const zhKbView = {
  // views/kb/kbMeta.ts：可见性 / 角色 / 文档派生状态
  // （已入库/处理中/已发布/已撤回 复用 common.docStatus.*；系统管理员/域管理员 复用 header.roleAdmin/roleDomainAdmin）
  'kbView.visibility.public': '公开',
  'kbView.visibility.private': '私有',
  'kbView.role.owner': '拥有者',
  'kbView.role.editor': '编辑者',
  'kbView.role.viewer': '只读',
  'kbView.docStatus.minedUnchanged': '已入库（未变化）',
  'kbView.docStatus.uploaded': '未挖掘',
  'kbView.docStatus.updateFailed': '更新失败，仍用上一版本',
  'kbView.docStatus.failed': '挖掘失败，等待重试',
  'kbView.docStatus.unknown': '未知',
  // 检索错误三分型（api/serving.ts localizeSearchError）/ 任务详情部分加载（stores/mining.ts）
  'kbView.search.noActiveBuild': '所选知识库暂无可检索内容：尚未完成挖掘，或全部文档挖掘失败。请先完成一次成功的挖掘（部分文档成功即可检索）。',
  'kbView.search.kbNotFound': '无法访问所选知识库：库不存在，或当前账号无读取权限（需库主/成员/public/站点管理员）。',
  'kbView.search.forbidden': '没有访问所选知识库的权限，请确认知识库可见性或联系管理员。',
  'kbView.runDetail.partialLoad': '任务已加载，但部分进度或文档明细暂时不可用',
  // 我的角色标签前缀（列表卡片与详情页头共用）
  'kbView.role.my': '我：{role}',

  // views/kb/KbListView.vue：知识库列表
  // （「新建知识库」复用 dashboard.action.newKb；「重试」「取消」「保存」「永久删除」复用 common.*；
  //   「排队中」复用 common.runStatus.queued；「公开/私有」「拥有者/只读…」复用 kbView.visibility.* / kbView.role.*）
  'kbView.list.kbCount': '{n} 个',
  'kbView.list.noDescription': '暂无描述',
  'kbView.list.docCount': '{n} 个文档',
  'kbView.list.creator': '创建者：{name}',
  'kbView.list.mine': '挖掘',
  'kbView.list.copy': '拷贝',
  'kbView.list.exportDownload': '导出下载',
  'kbView.list.rename': '改名',
  'kbView.list.deleteKb': '删除知识库',
  // 删除中的知识库（后台清理任务的阶段与进度）
  'kbView.list.purgePhase.prepare': '准备',
  'kbView.list.purgePhase.documents': '删除文档',
  'kbView.list.purgePhase.snapshots': '回收知识快照',
  'kbView.list.purgePhase.objects': '回收存储对象',
  'kbView.list.purgePhase.finalize': '收尾',
  'kbView.list.purgeProgress.snapshots': '{done}/{total} 快照',
  'kbView.list.purgeProgress.objects': '{done}/{total} 对象',
  'kbView.list.purgeFinishing': '完成中',
  'kbView.list.purgeFailed': '「{name}」删除失败：{reason}——可重新发起删除',
  'kbView.list.unknownReason': '未知原因',
  // 已删除的知识库（软删态存量：恢复 / 彻底删除）
  'kbView.list.deletedTitle': '已删除的知识库（{n}）',
  'kbView.list.deletedAt': '删除于 {date}',
  'kbView.list.restore': '恢复',
  'kbView.list.purge': '彻底删除',
  'kbView.list.empty': '当前域还没有知识库，点击右上角「新建知识库」开始',
  // 通知与确认框
  'kbView.list.mineQueued': '挖掘已排队（run {id}）',
  'kbView.list.newName': '新名称',
  'kbView.list.renameTitle': '重命名知识库',
  'kbView.list.nameRequired': '名称不能为空',
  'kbView.list.renamed': '已改名',
  'kbView.list.deletePrompt': '此操作将永久删除知识库「{name}」及其全部文档、挖掘知识与历史记录，不可恢复。请输入库全名确认：',
  'kbView.list.deleteTitle': '永久删除知识库',
  'kbView.list.nameMismatch': '名称不一致',
  'kbView.list.deleteStarted': '已开始后台删除：库即刻停用（成员不可见），进度见列表下方',
  'kbView.list.purgePrompt': '将彻底清除「{name}」的全部残留数据（文档/知识/存储），不可恢复。请输入库全名确认：',
  'kbView.list.purgeStarted': '已开始后台彻底清除，进度见列表下方',
  'kbView.list.restorePrompt': '确认恢复知识库「{name}」？若当前域已有同名知识库，恢复会被拒绝。',
  'kbView.list.restoreTitle': '恢复知识库',
  'kbView.list.restored': '知识库已恢复',

  // views/kb/KbDetailView.vue：知识库详情
  // （「知识库」返回入口复用 title.kb；「加载中…」「重试」复用 common.*）
  'kbView.detail.paradigmBound': '范式已绑定',
  'kbView.detail.copiedAt': '拷贝于 {time}',
  'kbView.detail.copiedFrom': '拷贝自「{name}」',
  // 检索就绪度
  'kbView.detail.readiness': '检索就绪度',
  'kbView.detail.embeddingFallbackTip': '嵌入服务当时不可用，向量缺失已留痕；恢复后重新挖掘可补齐语义检索',
  'kbView.detail.embeddingFallback': '向量缺失已留痕',
  'kbView.detail.readinessDocs': '文档 {n}',
  'kbView.detail.readinessSegments': '切片 {n}',
  'kbView.detail.readinessUnits': '检索单元 {n}',
  'kbView.detail.readinessEmbeddings': '向量 {n}',
  'kbView.detail.readinessLevel.empty': '空库',
  'kbView.detail.readinessLevel.parsed': '已解析',
  'kbView.detail.readinessLevel.segmented': '已切片',
  'kbView.detail.readinessLevel.lexical_ready': '关键词可搜',
  'kbView.detail.readinessLevel.vector_ready': '语义可搜',
  // 拷贝引导条 / 缺失态
  'kbView.detail.copyBanner': '这是「{name}」的副本：文件与知识血缘已就绪。选择挖掘范式并开始挖掘后，知识才可被检索——选与源库相同的范式可复用已有解析与向量，几乎零成本。',
  'kbView.detail.chooseParadigm': '去选择挖掘范式',
  'kbView.detail.notFound': '知识库不存在或无权访问',
  'kbView.detail.backToList': '返回列表',
  // Tab（Run 详情的「返回列表」复用 backToList）
  'kbView.detail.tab.files': '文件',
  'kbView.detail.tab.search': '检索',
  'kbView.detail.tab.members': '成员',
  'kbView.detail.tab.mining': '挖掘',
  'kbView.detail.tab.quality': '质量',
  'kbView.detail.tab.onenet': '一张网',
  'kbView.detail.tab.settings': '设置',
  'kbView.detail.deleted': '知识库已删除',

  // views/kb/KbDocPreviewView.vue：文档预览
  // （「重试」复用 common.retry；「未命名文档」复用 kbStruct.untitledDoc；
  //   表格「{rows} 行 × {columns} 列」复用 kbStruct.tableDims；文档状态经 kbMeta.docStatusLabel 已译）
  'kbView.preview.back': '返回',
  'kbView.preview.outdated': '待更新（旧知识可用）',
  'kbView.preview.downloadLatest': '下载最新原文件',
  'kbView.preview.tab.preview': '最新原文件预览',
  // 源文件是两行 → 按行各成一 key（模板里两段之间的换行折叠成一个空格，渲染结果不变）
  'kbView.preview.sourceNotice1': '此处预览和下载的是最新原文件，可能与当前可搜索知识不同。替换文件后需重新挖掘；',
  'kbView.preview.sourceNotice2': '搜索证据请从搜索结果的来源入口核验对应版本。',
  'kbView.preview.tooLarge': '文件较大（{size} MB），未在线渲染。',
  'kbView.preview.shownChars': '已展示前 {n}k 字符',
  'kbView.preview.showMore': '展示更多',
  'kbView.preview.unsupported': '该类型暂不支持在线预览（.{ext}）。',
  'kbView.preview.unknownExt': '未知',
  'kbView.preview.supportedTypes': '支持：md / html / 纯文本代码（txt/json/yaml/csv/xml 等） / 图片（png/jpg/gif/webp/svg）/ PDF',
  // 结构化数据
  'kbView.preview.tab.structured': '结构化数据',
  'kbView.preview.searchOnlyNotice': '章节范围搜索和表格精确查询仅当前可搜索版本可用；此解析结果尚未进入搜索，请切回当前可搜索版本。',
  // 版本横幅（三支；每支的每个源码行一条 key）
  'kbView.preview.version.inSync': '当前可搜索版本与最新上传版本一致（rev.{rev}）',
  'kbView.preview.version.servingPrefix': '当前可搜索 rev.{rev}；',
  'kbView.preview.version.latestNotInSearch': '最新上传 rev.{rev} 尚未进入搜索',
  'kbView.preview.version.latestOnlyPrefix': '以下为最新上传版本（rev.{rev}）的解析结果，',
  'kbView.preview.version.notSearchable': '尚未进入搜索——搜索与 Agent 读取暂不可用',
  'kbView.preview.version.viewLatest': '查看最新解析',
  'kbView.preview.version.viewServing': '查看当前可搜索版本',
  // 知识快照
  'kbView.preview.card.snapshot': '知识快照',
  'kbView.preview.qualityResult': '质量结论',
  'kbView.preview.contentVersion': '内容版本',
  'kbView.preview.elementCounts': '元素 / 容器 / 关系',
  'kbView.preview.chunkCount': '切片数',
  'kbView.preview.parsePipeline': '解析管线',
  'kbView.preview.chunkStrategy': '切片策略',
  'kbView.preview.default': '默认',
  // 文档结构图 / 文档大纲
  'kbView.preview.card.structureGraph': '文档结构图',
  'kbView.preview.card.outline': '文档大纲',
  'kbView.preview.parentSection': '父章节',
  'kbView.preview.prevSection': '上一节',
  'kbView.preview.nextSection': '下一节',
  'kbView.preview.firstChildSection': '首个子节',
  'kbView.preview.searchSection': '本节搜索',
  'kbView.preview.searchDescendants': '本节及子节搜索',
  'kbView.preview.noScopeAnchor': '此版本章节无范围锚（旧版本快照），重新挖掘后可用',
  // 切片 / 结构元素 / 表格
  'kbView.preview.card.chunks': '切片（{n}）',
  'kbView.preview.chunksTruncated': '当前仅展示前 {n} 条切片。',
  'kbView.preview.card.elements': '结构元素（{n}）',
  'kbView.preview.emptyElement': '空元素',
  'kbView.preview.evidenceKept': '已保留来源定位',
  'kbView.preview.elementsTruncated': '当前仅展示前 {n} 个元素。',
  'kbView.preview.card.tables': '表格（{n}）',
  'kbView.preview.exactQuery': '精确查询',
  'kbView.preview.tablePreviewOnly': '仅预览前 {shown} 行（共 {total} 行数据行）——用「精确查询」筛选/排序/统计全表数据。',
  'kbView.preview.parseDiagnostics': '解析诊断',
  // 检索单元
  'kbView.preview.tab.units': '检索单元 ({n})',
  'kbView.preview.noUnits': '当前可搜索版本没有可返回的原始证据表示——可能挖掘未完成或未生成检索表示。',
  'kbView.preview.assistTitle': '搜索辅助表示（{n}）——帮助召回，不是原文',
  'kbView.preview.searchAssist': '搜索辅助',
  // 脚本内提示
  'kbView.preview.rowHit': '命中数据行 {row}（预览区高亮，超出预览范围请看查询结果）',
  'kbView.preview.noParseResult': '该文档还没有新链解析结果。触发「更新知识」后，这里会展示解析出的标题树、表格网格与切片。',
  'kbView.preview.structuredDisabled': '结构化数据视图未在本部署启用。',

  // views/kb/KbRunDetailView.vue：Run 详情
  // （「重试」「提交时间」「大小」「状态」「操作」「耗时」复用 common.*；「文档」复用 dashboard.tile.docs；
  //   Run 状态经 utils/runStatus.runStatusLabel 已译）
  'kbView.run.totalDuration': '总历时',
  // 部分成功横幅
  'kbView.run.partialTitle': '任务部分完成',
  'kbView.run.partial.committed': '成功入库：{n}',
  'kbView.run.partial.failed': '失败待重试：{n}',
  'kbView.run.partial.skipped': '跳过未变化：{n}',
  'kbView.run.partialHint': '失败的文档下次点击「挖掘」会自动重试，不影响已入库内容',
  // 人审暂停横幅
  'kbView.run.pausedTitle': '挖掘已暂停，等待人工处理',
  'kbView.run.pausedNode': '暂停节点：{node}',
  'kbView.run.resumeHint': '处理完成后可继续',
  'kbView.run.resume': '继续挖掘',
  // 进度卡
  'kbView.run.progress.completed': '{n} 完成',
  'kbView.run.progress.failed': '{n} 失败',
  'kbView.run.progress.skipped': '{n} 跳过',
  'kbView.run.progress.processing': '{n} 处理中',
  // Pipeline 阶段
  'kbView.run.pipelineStages': 'Pipeline 阶段',
  'kbView.run.legacyTask': '历史任务',
  'kbView.run.stagesUnavailable': '任务阶段暂不可用',
  // 文档处理结果表（筛选里「处理中」「失败」「已完成」复用 common.*）
  'kbView.run.docResults': '文档处理结果 ({n})',
  'kbView.run.column.fileName': '文件名',
  'kbView.run.column.stage': '阶段',
  'kbView.run.column.error': '错误',
  'kbView.run.filter.all': '全部',
  // 继续挖掘
  'kbView.run.reviewPending': '仍有待审项，请先处理完再继续',
  'kbView.run.runCompleted': '该 run 已完成',
  'kbView.run.resumeStarted': '已开始继续挖掘，正在后台运行…',
  'kbView.run.resumeFailed': '继续失败',
  // 仅 Run 详情有的阶段名（Run 文档详情把 enrich 叫「增强」，见 kbView.runDoc.stage.enrich）
  'kbView.run.stage.enrich': '段落理解',
  'kbView.run.stage.entity_extract': '实体抽取',
  'kbView.run.stage.resolve': '实体归一',
  'kbView.run.stage.entity_relations': '关系抽取',
  'kbView.run.stage.graph_write': '落图',

  // Run 内文档的状态 / 动作 / 阶段名：KbRunDetailView 的文档表与 KbRunDocDetailView 的时间线共用
  // （「处理中」复用 common.docStatus.mining；「失败」复用 common.docStatus.failed；
  //   「已完成」复用 common.runStatus.completed；「向量化」复用 common.miningOperator.embedding）
  'kbView.runDoc.docStatus.committed': '完成',
  'kbView.runDoc.docStatus.pending': '等待',
  'kbView.runDoc.docStatus.skipped': '跳过',
  'kbView.runDoc.action.new': '新增',
  'kbView.runDoc.action.updated': '更新',
  'kbView.runDoc.action.unchanged': '无变化',
  'kbView.runDoc.action.removed': '移除',
  'kbView.runDoc.stage.parse': '解析',
  'kbView.runDoc.stage.segment': '分段',
  'kbView.runDoc.stage.enrich': '增强',
  'kbView.runDoc.stage.discourse': '语篇分析',
  'kbView.runDoc.stage.retrieval_units': '检索单元构建',
  'kbView.runDoc.stage.db_write': '数据写入',
  'kbView.runDoc.stage.commit_segments': '写入分段',
  'kbView.runDoc.stage.build_relations': '写入关系',
  'kbView.runDoc.stage.build_retrieval_units': '写入检索单元',
  'kbView.runDoc.stage.select_snapshot': '快照选择',
  'kbView.runDoc.stage.assemble_build': '构建组装',
  'kbView.runDoc.stage.validate_build': '构建校验',
  'kbView.runDoc.stage.publish_release': '发布',

  // views/kb/KbRunDocDetailView.vue：Run 文档详情
  // （「重试」「耗时」「类型」「加载失败」复用 common.*；「检索单元」复用 dashboard.tile.units）
  'kbView.runDoc.back': '返回 Run',
  'kbView.runDoc.loadFailed': '文档详情加载失败：{error}',
  'kbView.runDoc.hasErrors': '有错误',
  // 跳过原因（<strong> 标签切开 → 片段 key；原因文案表见下）
  'kbView.runDoc.skipReason': '跳过原因：',
  'kbView.runDoc.stageTimeline': '阶段时间线',
  'kbView.runDoc.stageInProgress': '进行中',
  'kbView.runDoc.noStageData': '暂无阶段数据',
  // 产物
  'kbView.runDoc.artifactSummary': '产物摘要',
  'kbView.runDoc.rawSegments': '原始分段',
  'kbView.runDoc.knowledgeArtifacts': '知识产物',
  'kbView.runDoc.column.role': '角色',
  'kbView.runDoc.column.title': '标题',
  'kbView.runDoc.column.content': '内容',
  'kbView.runDoc.column.structuralContext': '结构上下文',
  'kbView.runDoc.noRawContent': '无可渲染的原始文件',
  'kbView.runDoc.rawText': '原始文本',
  'kbView.runDoc.rawUnpreviewable': '该文件类型不支持在线预览',
  // 跳过原因（后端机器码 → 文案；最后两条是没有机器码的老数据兜底）
  'kbView.runDoc.skip.unchanged': '内容与已发布快照一致（哈希未变），复用原快照，未重新挖掘',
  'kbView.runDoc.skip.restored': '内容与历史快照一致，直接恢复该快照，未重新挖掘',
  'kbView.runDoc.skip.preprocess_failed': '摄取时格式转换失败（PDF 抽取 / HTML 转换 / 压缩包解包 / doc 转 docx），未能取到正文',
  'kbView.runDoc.skip.parser_failed': '文件解析失败（可能已加密、损坏或格式异常），未生成快照',
  'kbView.runDoc.skip.unsupported_type': '该文件类型没有对应的解析器，未生成快照',
  'kbView.runDoc.skip.empty_file': '文件内容为空或未抽取到文本（如扫描件 PDF），未生成快照',
  'kbView.runDoc.skip.no_segments': '分段结果为空，未生成快照',
  'kbView.runDoc.skip.parse_no_tree': '解析未产出内容，未生成快照',
  'kbView.runDoc.skip.noContent': '该文档未产出可入库的内容（解析为空或分段为空）',
  'kbView.runDoc.skip.unchangedLegacy': '内容未发生变化，复用已发布快照',

  // components/mining/workflow/MiningWorkflowTrace.vue：Run 详情的挖掘轨迹
  // （「运行中」复用 common.runStatus.running；「完成」「跳过」复用 kbView.runDoc.docStatus.*；
  //   「失败」复用 common.docStatus.failed；算子名经 miningOperatorLabel 已译）
  'kbView.trace.legacyHint': '该 Run 未绑定 Workflow Manifest，继续展示兼容阶段事件。',
  'kbView.trace.pauseAt': '暂停位置：',
  'kbView.trace.emptyGraph': '冻结 Manifest 中没有可渲染的图。',
  'kbView.trace.nodeEvents': '节点事件 {n}',
  'kbView.trace.finalBuild': '最终 Build/Release：',
  'kbView.trace.nodeStatus.pending': '未运行',
  'kbView.trace.nodeStatus.paused': '已暂停',
  'kbView.trace.nodeStatus.fallback': '降级完成',
  'kbView.trace.nodeStatus.not_applicable': '不适用',
} as const
