/**
 * 概览页统计图表的数据派生。
 *
 * 图表出错很少出在渲染，出在喂进去的数组：排序方向反了、不适用的口径被当成 0 画出来、
 * 横向柱上下颠倒。这些看图很难发现（图总是"画出来了"），所以在这里逐条钉死。
 */
import { describe, it, expect } from 'vitest'
import {
  TOP_KB_BAR_LIMIT, documentStatusSlices, kbDocumentBars,
  trendSeries, trendTotals, unitTypeBars,
} from '@/utils/dashboard'
import type { KbOverviewItem, KbStats } from '@/types/kb'

function stats(over: Partial<KbStats> = {}): KbStats {
  return {
    kb_count: 2,
    has_active_release: false,
    trend_days: 30,
    document_status: {
      uploaded: 5, mining: 1, mined: 10, published: 0, withdrawn: 0, failed: 2,
      update_failed: 0,
    },
    assets: {
      snapshots: 10, segments: 300, retrieval_units: 420,
      embeddings: 88,
    },
    retrieval_unit_types: { raw_text: 200, summary: 120, entity_card: 100 },
    mining_trend: [
      { date: '2026-08-16', runs: 2, completed: 2, documents: 7 },
      { date: '2026-08-17', runs: 0, completed: 0, documents: 0 },
      { date: '2026-08-18', runs: 1, completed: 1, documents: 3 },
    ],
    ...over,
  }
}

function kb(name: string, total: number): KbOverviewItem {
  return {
    id: name,
    name,
    my_role: 'owner',
    can_write: true,
    status_counts: { total, mining: 0, failed: 0 },
    last_mined_at: null,
    awaiting_review_run_id: null,
  }
}

describe('documentStatusSlices', () => {
  it('无 active release 时摘掉 published / withdrawn 两档', () => {
    const names = documentStatusSlices(stats()).map(s => s.name)

    expect(names).not.toContain('已发布')
    expect(names).not.toContain('已撤回')
    // 36号 §九：mined=已入库（Build membership）；update_failed 新档
    expect(names).toEqual(['已入库', '处理中', '待挖掘', '失败', '更新失败'])
  })

  it('有 active release 时七档齐全', () => {
    const names = documentStatusSlices(stats({ has_active_release: true })).map(s => s.name)

    expect(names).toHaveLength(7)
    expect(names).toContain('已发布')
    expect(names).toContain('已撤回')
  })

  it('计数为 0 的档保留——「失败 0」本身是有用的信息', () => {
    const slices = documentStatusSlices(stats({
      document_status: {
        uploaded: 0, mining: 0, mined: 4, published: 0, withdrawn: 0, failed: 0,
        update_failed: 0,
      },
    }))

    expect(slices.find(s => s.name === '失败')).toEqual(
      expect.objectContaining({ value: 0 }),
    )
  })

  it('每一档都带固定的状态色（颜色语义不随数据变）', () => {
    const byName = Object.fromEntries(
      documentStatusSlices(stats()).map(s => [s.name, s.color]),
    )

    expect(byName['已入库']).toBe('#10b981')
    expect(byName['失败']).toBe('#ef4444')
  })
})

describe('kbDocumentBars', () => {
  it('按文档数降序取前 N，并反转以配合 ECharts 自下而上的 category 轴', () => {
    const bars = kbDocumentBars([kb('a', 3), kb('b', 9), kb('c', 5)])

    // 数组末位 = 图表最上方 = 最多的那个
    expect(bars.map(b => b.name)).toEqual(['a', 'c', 'b'])
    expect(bars[bars.length - 1].value).toBe(9)
  })

  it('文档数为 0 的库不进图', () => {
    const bars = kbDocumentBars([kb('empty', 0), kb('has', 2)])

    expect(bars.map(b => b.name)).toEqual(['has'])
  })

  it('超过上限时截断', () => {
    const many = Array.from({ length: 20 }, (_, i) => kb(`kb-${i}`, i + 1))

    expect(kbDocumentBars(many)).toHaveLength(TOP_KB_BAR_LIMIT)
  })

  it('截断保留的是最多的那几个，不是最先的那几个', () => {
    const many = Array.from({ length: 20 }, (_, i) => kb(`kb-${i}`, i + 1))
    const values = kbDocumentBars(many).map(b => b.value)

    expect(Math.max(...values)).toBe(20)
    expect(Math.min(...values)).toBe(13)
  })

  it('空输入给空数组，不抛', () => {
    expect(kbDocumentBars([])).toEqual([])
  })
})

describe('unitTypeBars', () => {
  it('翻译类型名、降序、反转', () => {
    const bars = unitTypeBars({ summary: 120, raw_text: 200 })

    expect(bars.map(b => b.name)).toEqual(['摘要', '原始文本'])
  })

  it('未收录的类型键原样显示，不显示成 undefined', () => {
    expect(unitTypeBars({ some_new_type: 5 })[0].name).toBe('some_new_type')
  })

  it('undefined / 空对象给空数组', () => {
    expect(unitTypeBars(undefined)).toEqual([])
    expect(unitTypeBars({})).toEqual([])
  })
})

describe('trendSeries / trendTotals', () => {
  it('标签只留 MM-DD（30 个 YYYY-MM-DD 必然互相压盖）', () => {
    expect(trendSeries(stats()).labels).toEqual(['08-16', '08-17', '08-18'])
  })

  it('保留后端补的零值天——跳过空天会把「停了三周」画成持续产出', () => {
    expect(trendSeries(stats()).data).toEqual([7, 0, 3])
  })

  it('stats 为 null 时给空数组，不抛', () => {
    expect(trendSeries(null)).toEqual({ labels: [], data: [] })
    expect(trendTotals(null)).toEqual({ runs: 0, documents: 0 })
  })

  it('合计窗口内的 run 数与文档数', () => {
    expect(trendTotals(stats())).toEqual({ runs: 3, documents: 10 })
  })
})
