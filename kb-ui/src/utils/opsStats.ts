/**
 * 首页检索概况仍在使用的纯格式化与告警规则。
 */
import type { OpsUsage } from '@/types/ops'

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
export const FAILURE_ALERT_THRESHOLD = 0.1
export const NO_RESULT_ALERT_THRESHOLD = 0.2
export const NO_RESULT_MIN_SAMPLE = 20

export function shouldAlertNoResult(usage: OpsUsage | null): boolean {
  const s = usage?.summary
  if (!s || !usage?.available) return false
  return s.queries >= NO_RESULT_MIN_SAMPLE && s.no_result_rate >= NO_RESULT_ALERT_THRESHOLD
}
