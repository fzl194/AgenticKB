/**
 * segmentTerms——查询分词与词频聚合（词云原料）。
 *
 * 钉三件事：词频按问句次数加权聚合、功能词/纯数字/单字被过滤、
 * 无 Intl.Segmenter 的环境退化为 CJK 二元组仍可统计。
 * 断言用带空格的句子保证分词边界确定（真实句子由真实分词器处理）。
 */
import { describe, expect, it, vi } from 'vitest'
import { aggregateTerms } from '../segmentTerms'

describe('aggregateTerms', () => {
  it('同词跨问句按次数加权；同一句内重复只计一次', () => {
    const terms = aggregateTerms([
      { text: '5GC 计费 规则', count: 5 },
      { text: '计费 计费', count: 3 },
    ])
    const byTerm = new Map(terms.map(t => [t.term, t.count]))
    expect(byTerm.get('计费')).toBe(8)          // 5 + 3，句内重复不加倍
    expect(byTerm.get('规则')).toBe(5)
    expect(byTerm.get('5gc')).toBe(5)           // 英文小写归一
  })

  it('过滤功能词、纯数字与单字，只留有信息量的词', () => {
    const terms = aggregateTerms([{ text: '怎么 查询 2026 的 告警', count: 1 }])
    expect(terms.map(t => t.term)).toEqual(['告警'])
  })

  it('noResult 按词聚合透传（供词云外的分析备用）', () => {
    const terms = aggregateTerms([
      { text: '告警 处理', count: 4, noResult: 1 },
      { text: '告警', count: 2, noResult: 2 },
    ])
    const alarm = terms.find(t => t.term === '告警')
    expect(alarm).toMatchObject({ count: 6, noResult: 3 })
    expect(terms.find(t => t.term === '处理')).toMatchObject({ count: 4, noResult: 1 })
  })

  it('无 Intl.Segmenter 环境退化为 CJK 二元组切分', () => {
    // 保留 Intl 其余成员（sort 里的 localeCompare 还要用），只把 Segmenter 摘掉
    const intlStub = Object.create(Intl) as Intl.DateTimeFormatConstructor & Record<string, unknown>
    intlStub.Segmenter = undefined
    vi.stubGlobal('Intl', intlStub)
    try {
      const terms = aggregateTerms([{ text: '计费规则', count: 1 }])
      const names = terms.map(t => t.term)
      expect(names).toContain('计费')
      expect(names).toContain('规则')
      expect(names).toContain('费规')           // 二元组特征
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('空文本与非正次数跳过', () => {
    expect(aggregateTerms([{ text: '  ', count: 3 }, { text: '告警', count: 0 }])).toEqual([])
  })
})
