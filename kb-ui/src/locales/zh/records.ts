/** 检索记录区文案。 */
export const zhRecords = {
  // 自定义时间窗口校验（components/retrieval/timeRange.ts）
  'records.range.missingDates': '自定义时间需要选择开始与结束日期',
  'records.range.startAfterEnd': '开始日期不能晚于结束日期',
  'records.range.maxSpan': '自定义时间最长跨 {days} 天',
  // 词云 tooltip（components/charts/WordCloud.vue）
  'records.wordTooltip': '{name}：{value} 次',
} as const
