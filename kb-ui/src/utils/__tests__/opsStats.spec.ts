/**
 * 运维使用分析的派生逻辑。
 *
 * 与 dashboardStats.spec.ts 同理：图表"总是画得出来"，错的是喂进去的数组——
 * 排序反了、legacy 桶被当成一个真范式、样本太少时误报警。这些看图发现不了。
 */
import { describe, it, expect } from 'vitest'
import {
  LEGACY_PARADIGM_ID, LEGACY_PARADIGM_LABEL, breakdownBars, formatMs, formatRate,
  paradigmBars, paradigmLabel, shouldAlertNoResult, usageTrendSeries,
} from '@/utils/opsStats'
import type { OpsUsage } from '@/types/ops'

function usage(over: Partial<OpsUsage> = {}): OpsUsage {
  return {
    available: true,
    days: 7,
    trend_days: 30,
    summary: {
      queries: 100, no_result: 7, no_result_rate: 0.07,
      p95_duration_ms: 412, avg_duration_ms: 180, active_paradigms: 2,
    },
    no_result_queries: [],
    top_queries: [],
    paradigms: [],
    trend: [
      { date: '2026-08-16', queries: 7, no_result: 1 },
      { date: '2026-08-17', queries: 0, no_result: 0 },
      { date: '2026-08-18', queries: 3, no_result: 0 },
    ],
    intents: {},
    channels: {},
    ...over,
  }
}

describe('paradigmLabel', () => {
  it('legacy 桶换成人话——它不是一个范式，是"还没走范式的流量"', () => {
    expect(paradigmLabel(LEGACY_PARADIGM_ID)).toBe(LEGACY_PARADIGM_LABEL)
  })

  it('真实范式 id 原样显示', () => {
    expect(paradigmLabel('p-retrieval-v2')).toBe('p-retrieval-v2')
  })
})

describe('paradigmBars', () => {
  it('按调用量降序并反转，配合 ECharts 自下而上的 category 轴', () => {
    const bars = paradigmBars([
      { paradigm_id: 'a', calls: 3, no_result: 0, p95_duration_ms: 1 },
      { paradigm_id: 'b', calls: 9, no_result: 0, p95_duration_ms: 1 },
      { paradigm_id: 'c', calls: 5, no_result: 0, p95_duration_ms: 1 },
    ])

    // 数组末位 = 图表最上方 = 调用最多的
    expect(bars.map(b => b.name)).toEqual(['a', 'c', 'b'])
  })

  it('legacy 桶在图里也显示成人话', () => {
    const bars = paradigmBars([
      { paradigm_id: LEGACY_PARADIGM_ID, calls: 5, no_result: 0, p95_duration_ms: 1 },
    ])

    expect(bars[0].name).toBe(LEGACY_PARADIGM_LABEL)
  })

  it('零调用的不进图', () => {
    const bars = paradigmBars([
      { paradigm_id: 'zero', calls: 0, no_result: 0, p95_duration_ms: 0 },
      { paradigm_id: 'used', calls: 2, no_result: 0, p95_duration_ms: 1 },
    ])

    expect(bars.map(b => b.name)).toEqual(['used'])
  })

  it('不修改传入的数组（组件里它来自 props）', () => {
    const input = [
      { paradigm_id: 'a', calls: 1, no_result: 0, p95_duration_ms: 1 },
      { paradigm_id: 'b', calls: 9, no_result: 0, p95_duration_ms: 1 },
    ]
    paradigmBars(input)

    expect(input.map(p => p.paradigm_id)).toEqual(['a', 'b'])
  })

  it('undefined 给空数组，不抛', () => {
    expect(paradigmBars(undefined)).toEqual([])
  })
})

describe('usageTrendSeries', () => {
  it('标签只留 MM-DD', () => {
    expect(usageTrendSeries(usage()).labels).toEqual(['08-16', '08-17', '08-18'])
  })

  it('保留后端补的零值天——跳过空天会把"停了一周"画成连续使用', () => {
    expect(usageTrendSeries(usage()).data).toEqual([7, 0, 3])
  })

  it('null 给空数组', () => {
    expect(usageTrendSeries(null)).toEqual({ labels: [], data: [] })
  })
})

describe('breakdownBars', () => {
  it('降序 + 反转', () => {
    expect(breakdownBars({ a: 1, b: 9, c: 5 }).map(d => d.name)).toEqual(['a', 'c', 'b'])
  })

  it('undefined / 空对象给空数组', () => {
    expect(breakdownBars(undefined)).toEqual([])
    expect(breakdownBars({})).toEqual([])
  })
})

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

describe('shouldAlertNoResult', () => {
  it('样本足够且超阈值 → 报警', () => {
    expect(shouldAlertNoResult(usage({
      summary: {
        queries: 200, no_result: 60, no_result_rate: 0.3,
        p95_duration_ms: 1, avg_duration_ms: 1, active_paradigms: 1,
      },
    }))).toBe(true)
  })

  it('样本太少不报警——3 次里 1 次就是 33%，据此弹红只会训练人忽略它', () => {
    expect(shouldAlertNoResult(usage({
      summary: {
        queries: 3, no_result: 1, no_result_rate: 0.333,
        p95_duration_ms: 1, avg_duration_ms: 1, active_paradigms: 1,
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
        p95_duration_ms: 1, avg_duration_ms: 1, active_paradigms: 1,
      },
    }))).toBe(false)
  })

  it('null 不报警', () => {
    expect(shouldAlertNoResult(null)).toBe(false)
  })
})
