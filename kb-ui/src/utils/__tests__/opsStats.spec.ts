/**
 * 首页检索概况的格式化与告警边界测试。
 */
import { describe, it, expect } from 'vitest'
import {
  FAILURE_ALERT_THRESHOLD, formatMs, formatRate, shouldAlertNoResult,
} from '@/utils/opsStats'
import type { OpsUsage } from '@/types/ops'

function usage(over: Partial<OpsUsage> = {}): OpsUsage {
  return {
    available: true,
    days: 7,
    summary: {
      queries: 100, no_result: 7, no_result_rate: 0.07,
      p95_duration_ms: 412,
    },
    ...over,
  }
}

describe('formatRate / formatMs', () => {
  it('比率是 0–1 小数，渲染成百分数', () => {
    expect(formatRate(0.07)).toBe('7.0%')
    expect(formatRate(0)).toBe('0.0%')
  })

  it('超过 1s 用秒——「1200ms」不如「1.2s」好判断', () => {
    expect(formatMs(1200)).toBe('1.2s')
    expect(formatMs(412)).toBe('412ms')
  })

  it('非法输入给 "-" 而不是 NaN', () => {
    expect(formatRate(Number.NaN)).toBe('-')
    expect(formatMs(Number.NaN)).toBe('-')
    expect(formatMs(-1)).toBe('-')
  })
})

describe('alert thresholds', () => {
  it('names the dashboard failure-rate boundary', () => {
    expect(FAILURE_ALERT_THRESHOLD).toBe(0.1)
  })

  it('样本足够且超阈值 → 报警', () => {
    expect(shouldAlertNoResult(usage({
      summary: {
        queries: 200, no_result: 60, no_result_rate: 0.3,
        p95_duration_ms: 1,
      },
    }))).toBe(true)
  })

  it('样本太少不报警——3 次里 1 次就是 33%，据此弹红只会训练人忽略它', () => {
    expect(shouldAlertNoResult(usage({
      summary: {
        queries: 3, no_result: 1, no_result_rate: 0.333,
        p95_duration_ms: 1,
      },
    }))).toBe(false)
  })

  it('样本足够但比率低 → 不报警', () => {
    expect(shouldAlertNoResult(usage())).toBe(false)
  })

  it('表不存在时不报警——那不是"零结果率为 0"，是没有口径', () => {
    expect(shouldAlertNoResult(usage({
      available: false,
      summary: {
        queries: 200, no_result: 60, no_result_rate: 0.3,
        p95_duration_ms: 1,
      },
    }))).toBe(false)
  })

  it('null 不报警', () => {
    expect(shouldAlertNoResult(null)).toBe(false)
  })
})
