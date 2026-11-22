/** 概览区文案（views/DashboardView + components/dashboard + 本区专属词条）。 */
export const zhDashboard = {
  // utils/dashboard.ts：待处理清单
  'dashboard.pending.reviewText': '挖掘已暂停，等待人工审核',
  'dashboard.pending.reviewAction': '去处理',
  'dashboard.pending.failedDocs': '{n} 篇文档解析失败',
  // views/DashboardView.vue：域未就绪提示（「重试」复用 common.retry）
  'dashboard.notice.domainNotReady': '知识域尚未就绪',
  'dashboard.notice.domainNotReadyHint': '当前没有可用的知识域，无法加载统计。请检查域配置或稍后重试。',
  // 区块标题
  'dashboard.block.overview': '知识库概况',
  'dashboard.block.pending': '⚠ 待处理',
  'dashboard.block.docStatus': '文档状态分布',
  'dashboard.block.trend': '近 {n} 天入库文档数',
  'dashboard.block.kbDocs': '各知识库文档数（前 {n}）',
  'dashboard.block.unitTypes': '检索单元类型分布',
  'dashboard.block.myKbs': '我的知识库',
  'dashboard.block.recentRuns': '最近挖掘',
  // 口径副文案
  'dashboard.scope.overview': '口径：本域我可见的 {n} 个知识库',
  'dashboard.scope.trendTotals': '共 {docs} 篇 · {runs} 次挖掘',
  // 趋势折线图系列名
  'dashboard.series.indexedDocs': '入库文档数',
  // 顶部数字条
  'dashboard.tile.kb': '知识库',
  'dashboard.tile.docs': '文档',
  'dashboard.tile.mined': '已挖掘',
  'dashboard.tile.units': '检索单元',
  // 空状态（区块级「加载失败」复用 common.loadFailed）
  'dashboard.empty.noKbForCharts': '还没有知识库，建一个就能看到统计',
  'dashboard.empty.noKb': '还没有知识库',
  'dashboard.empty.noDocs': '还没有文档',
  'dashboard.empty.noUnits': '还没有挖掘出检索单元',
  'dashboard.empty.noRuns': '还没有挖掘记录',
  // 操作按钮
  'dashboard.action.refresh': '刷新',
  'dashboard.action.newKb': '新建知识库',
  'dashboard.action.new': '+ 新建',
  'dashboard.action.viewAll': '查看全部 →',
  // 同域刷新失败：保留旧数据时的提示条
  'dashboard.warning.refreshFailed': '刷新失败，显示上次数据',
  // components/dashboard/OpsPanel.vue：检索概况（「重试」复用 common.retry）
  'dashboard.ops.title': '检索概况',
  'dashboard.ops.scope': '近 {n} 天 · 当前知识域',
  'dashboard.ops.viewRecords': '查看检索记录 →',
  'dashboard.ops.loadFailed': '检索概况加载失败',
  'dashboard.ops.noRecords': '尚无新检索记录',
  'dashboard.ops.noRecordsHint': '统一记录从本版本上线后开始产生，旧记录不会迁入。',
  'dashboard.ops.queries': '调用量',
  'dashboard.ops.noResultRate': '零结果率',
  'dashboard.ops.failedRate': '失败率',
  'dashboard.ops.p95Latency': 'P95 延迟',
  'dashboard.ops.failedAlert': '失败率 {rate}，建议打开检索记录定位失败调用。',
  'dashboard.ops.noResultAlert': '零结果率 {rate}，建议打开检索记录查看需要补充的知识。',
  // components/dashboard/KbCard.vue
  'dashboard.kbCard.docCount': '{n} 篇',
  'dashboard.kbCard.badgeFailed': '有文档处理失败',
  'dashboard.kbCard.badgeMining': '正在挖掘',
  'dashboard.kbCard.badgeReady': '就绪',
  'dashboard.kbCard.failedPending': '{n} 篇待处理',
  'dashboard.kbCard.miningProgress': '挖掘中 {done}/{total}',
  'dashboard.kbCard.lastMined': '最近挖掘 {time}',
  'dashboard.kbCard.notMined': '尚未挖掘',
  // components/dashboard/QueryList.vue
  'dashboard.queryList.count': '{n} 次',
} as const
