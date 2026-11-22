import type { RetrievalRecordFilters } from '@/types/retrievalRecords'
import { t } from '@/locales/t'

/** 时间范围档位：近 N 天快捷档或自定义日期窗口。 */
export type RangePreset = number | 'custom'

// 与后端 _CUSTOM_RANGE_MAX_DAYS 对齐：自定义窗口最长一年
export const MAX_CUSTOM_RANGE_DAYS = 365

export interface TimeRangeState {
  days: RangePreset
  startDate: string
  endDate: string
}

export function toDateString(value: Date): string {
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
}

/** 切到"自定义"时预填近 7 天窗口，让筛选开箱即用；已手选过日期则不覆盖。 */
export function prefillCustomRange(state: TimeRangeState): void {
  if (state.days !== 'custom' || (state.startDate && state.endDate)) return
  const today = new Date()
  state.startDate = toDateString(new Date(today.getTime() - 6 * 86400000))
  state.endDate = toDateString(today)
}

/** 校验自定义窗口；返回错误文案，空串=通过。 */
export function validateCustomRange(state: TimeRangeState): string {
  if (state.days !== 'custom') return ''
  if (!state.startDate || !state.endDate) return t('records.range.missingDates')
  // ISO 日期字符串的字典序即时间序
  if (state.startDate > state.endDate) return t('records.range.startAfterEnd')
  const spanDays = (new Date(state.endDate).getTime() - new Date(state.startDate).getTime()) / 86400000
  if (spanDays > MAX_CUSTOM_RANGE_DAYS) return t('records.range.maxSpan', { days: MAX_CUSTOM_RANGE_DAYS })
  return ''
}

/** 把时间范围状态映射为 API 筛选参数：自定义窗口与 days 快捷档互斥，只送其一。 */
export function rangeFilters(state: TimeRangeState): Pick<RetrievalRecordFilters, 'days' | 'startDate' | 'endDate'> {
  const custom = state.days === 'custom'
  return {
    days: state.days === 'custom' ? undefined : state.days,
    startDate: custom ? state.startDate || undefined : undefined,
    endDate: custom ? state.endDate || undefined : undefined,
  }
}
