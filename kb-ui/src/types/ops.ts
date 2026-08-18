/**
 * 运维使用分析类型（GET /api/ops/usage）。
 *
 * 回答的是**使用**问题而不是基础设施问题：有没有人在用、用户问了什么、系统答不上来
 * 哪些、哪个检索范式在真正承载流量。数据源是 serving 写的 serving_query_logs。
 */

/** 窗口内的关键数。分母为 0 时后端给 0 而不是 null——前端直接拿去渲染百分比。 */
export interface OpsUsageSummary {
  queries: number
  no_result: number
  /** 0–1 的小数，不是百分数。 */
  no_result_rate: number
  /** 用 P95 不是平均值：平均值会被一堆快查询稀释，掩掉真正卡住人的那条尾巴。 */
  p95_duration_ms: number
  avg_duration_ms: number
  active_paradigms: number
}

/** 答不上来的问题。**这是整份数据里最有行动价值的一段**——它直接指向该补的知识。 */
export interface OpsNoResultQuery {
  /** 用户输入原文（后端截断到 200 字）。仅 admin 可见。 */
  query_text: string
  count: number
  last_at: string | null
}

export interface OpsTopQuery {
  query_text: string
  count: number
  /** 「问得多又答不上」的优先级最高，所以热门榜也带零结果数。 */
  no_result: number
}

export interface OpsParadigmUsage {
  /** `__legacy__` = 走旧 SearchService、没经范式引擎的流量。 */
  paradigm_id: string
  calls: number
  no_result: number
  p95_duration_ms: number
}

/** 每日检索量。后端已补零，前端直接照数组画。 */
export interface OpsTrendPoint {
  date: string
  queries: number
  no_result: number
}

export interface OpsUsage {
  /**
   * false = serving_query_logs 不存在（serving 从没启动过）。此时其余字段是形状相同的
   * 空壳，不是「用量为 0」——页面要说"尚未产生检索日志"，不能画一屏 0。
   */
  available: boolean
  /** 摘要与各列表的窗口天数。 */
  days: number
  /** 趋势折线的窗口天数，与 days 独立。 */
  trend_days: number
  summary: OpsUsageSummary
  no_result_queries: OpsNoResultQuery[]
  top_queries: OpsTopQuery[]
  paradigms: OpsParadigmUsage[]
  trend: OpsTrendPoint[]
  intents: Record<string, number>
  channels: Record<string, number>
}
