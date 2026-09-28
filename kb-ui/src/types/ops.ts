/** 首页「检索概况」兼容类型；完整分析与明细使用 retrievalRecords 类型。 */
export interface OpsUsageSummary {
  queries: number
  no_result: number
  /** 0–1 的小数，不是百分数。 */
  no_result_rate: number
  p95_duration_ms: number
  failed?: number
  failed_rate?: number
}

export interface OpsUsage {
  available: boolean
  days: number
  summary: OpsUsageSummary
}
