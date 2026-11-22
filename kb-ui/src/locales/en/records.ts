import type { zhRecords } from '../zh/records'

export const enRecords: Record<keyof typeof zhRecords, string> = {
  'records.range.missingDates': 'Select both a start date and an end date for the custom range',
  'records.range.startAfterEnd': 'The start date cannot be later than the end date',
  'records.range.maxSpan': 'The custom range cannot exceed {days} days',
  // 词云 tooltip（components/charts/WordCloud.vue）
  'records.wordTooltip': '{name}: {value} times',
}
