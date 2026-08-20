/**
 * 运维使用分析的派生逻辑。
 *
 * 与 utils/dashboard.ts 同一理由抽成纯函数：图表出错多半不在渲染，在喂进去的数组
 * ——排序方向反了、legacy 桶被当成一个真范式排到第一、零结果率的分母取错。
 */
import type { OpsParadigmUsage, OpsUsage } from '@/types/ops'
import type { ChartDatum } from '@/utils/dashboard'

/** 后端给 legacy（非范式引擎）流量用的桶名。 */
export const LEGACY_PARADIGM_ID = '__legacy__'

/** 它不是一个范式，是"还没走范式的那部分流量"，展示时必须换成人话。 */
export const LEGACY_PARADIGM_LABEL = '未走范式（旧检索）'

export function paradigmLabel(id: string): string {
  return id === LEGACY_PARADIGM_ID ? LEGACY_PARADIGM_LABEL : id
}

/**
 * 范式调用排行（横向柱）。
 *
 * ⚠️ 末尾 reverse 同 kbDocumentBars：ECharts 的 category 轴自下而上排，不反转的话
 * 调用最多的范式会落在图表最底下，与人从上往下读相反。
 */
export function paradigmBars(
  paradigms: OpsParadigmUsage[] | undefined,
  limit = 8,
): ChartDatum[] {
  return (paradigms ?? [])
    .filter(p => p.calls > 0)
    .slice()
    .sort((a, b) => b.calls - a.calls)
    .slice(0, limit)
    .map(p => ({ name: paradigmLabel(p.paradigm_id), value: p.calls }))
    .reverse()
}

/**
 * 检索量趋势。**只画总量一条线**，零结果不并进来做第二条：
 * 两者量级差一个数量级以上（几百次检索 vs 个位数零结果），共轴会把零结果压成贴地直线，
 * 而给它单开一根右轴就是双轴图——同一张图两套刻度，读者无从判断交叉有没有意义。
 * 零结果单独由数字卡与「答不上来的问题」列表承载，那里它是主角。
 */
export function usageTrendSeries(usage: OpsUsage | null): {
  labels: string[]
  data: number[]
} {
  const points = usage?.trend ?? []
  return {
    // 只留 MM-DD：30 个刻度写全 YYYY-MM-DD 必然互相压盖
    labels: points.map(p => p.date.slice(5)),
    data: points.map(p => p.queries),
  }
}

/** 分布字典 → 横向柱。reverse 理由同上。 */
export function breakdownBars(
  breakdown: Record<string, number> | undefined,
  limit = 8,
): ChartDatum[] {
  return Object.entries(breakdown ?? {})
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([name, value]) => ({ name, value }))
    .reverse()
}

/** 百分比文案。后端给的是 0–1 小数。 */
export function formatRate(rate: number): string {
  if (!Number.isFinite(rate)) return '-'
  return `${(rate * 100).toFixed(1)}%`
}

/** 毫秒 → 人读的时长。超过 1s 用秒，否则用毫秒——「1200ms」不如「1.2s」好判断。 */
export function formatMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

/**
 * 零结果率是否该报警。
 *
 * 20% 是个拍出来的阈值，但**低流量时不报**是有依据的：3 次检索里 1 次没答上来就是
 * 33%，据此弹红只会训练管理员忽略它。样本太少时任何比率都不稳定。
 */
export const NO_RESULT_ALERT_THRESHOLD = 0.2
export const NO_RESULT_MIN_SAMPLE = 20

export function shouldAlertNoResult(usage: OpsUsage | null): boolean {
  const s = usage?.summary
  if (!s || !usage?.available) return false
  return s.queries >= NO_RESULT_MIN_SAMPLE && s.no_result_rate >= NO_RESULT_ALERT_THRESHOLD
}
