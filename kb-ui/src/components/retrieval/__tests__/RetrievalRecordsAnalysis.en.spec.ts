/**
 * RetrievalRecordsAnalysis 的 EN 渲染断言（四区英化 Task 6 / 规则 R11）。
 *
 * mock 与挂载代码复制自同目录 RetrievalRecordsAnalysis.spec.ts，区别只在语言：
 * 全局语言键必须在 pinia 创建**之前**写入——locale store 在实例化时读它。
 * 装进插件的 pinia 与活动 pinia 是同一个实例（否则 t() 读不到切换）。
 *
 * 夹具数据一律 ASCII：EN 态下整块界面的文本里出现任何 CJK 字符都算泄漏。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, shallowMount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import type { Pinia } from 'pinia'

const api = vi.hoisted(() => ({ getSummary: vi.fn() }))
const domain = vi.hoisted(() => ({ currentDomain: 'domain-a' }))
const kbApi = vi.hoisted(() => ({ listKbs: vi.fn() }))
const operatorApi = vi.hoisted(() => ({ listParadigms: vi.fn() }))
vi.mock('@/api/retrievalRecords', () => ({ useRetrievalRecordsApi: () => api }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => domain }))
vi.mock('@/api/kb', () => ({ useKbApi: () => kbApi }))
vi.mock('@/api/operator', () => ({ useOperatorApi: () => operatorApi }))

import RetrievalRecordsAnalysis from '@/components/retrieval/RetrievalRecordsAnalysis.vue'
import { useRetrievalNames } from '@/components/retrieval/useRetrievalNames'
import { useLocaleStore } from '@/stores/locale'

enableAutoUnmount(afterEach)

/** 任何 CJK 字符都算泄漏：EN 态下整块界面不该残留中文。 */
const CJK = /[一-龥]/

const summary = {
  available: true, days: 7,
  summary: { total_calls: 10, calls: 7, no_result: 2, failed: 1, no_result_rate: .2, failure_rate: .1, p95_duration_ms: 300, avg_duration_ms: 100, active_paradigms: 1 },
  trend: [{ date: '2026-09-28', total_calls: 10, calls: 7, no_result: 2, failed: 1 }],
  sources: { web: 4, mcp: 6 },
  tools: [{ tool_name: 'search_knowledge', calls: 5, no_result: 1 }],
  paradigms: [{ paradigm_id: 'p-1', calls: 7, no_result: 2 }],
  top_queries: [
    { query_text: 'what are the 5GC charging rules', count: 5, no_result: 0, last_at: '2026-09-28T00:00:00Z' },
    { query_text: 'charging', count: 3, no_result: 0, last_at: '2026-09-28T00:00:00Z' },
  ],
}

let pinia: Pinia

// 顺序铁律：全局键先于 pinia 写入——store 在创建时读它
beforeEach(() => {
  localStorage.setItem('kb-ui-lang', 'en')
  pinia = createPinia()
  setActivePinia(pinia)
  vi.clearAllMocks()
  useRetrievalNames().reset()
  kbApi.listKbs.mockResolvedValue([{ id: 'kb-1', name: 'cloud-core-manual' }])
  operatorApi.listParadigms.mockResolvedValue([{ id: 'p-1', name: 'alarm-paradigm' }])
  api.getSummary.mockResolvedValue(summary)
})
afterEach(() => localStorage.removeItem('kb-ui-lang'))

async function mountAnalysis() {
  const wrapper = shallowMount(RetrievalRecordsAnalysis, { global: { plugins: [pinia] } })
  await flushPromises()
  return wrapper
}

describe('retrieval records analysis (EN)', () => {
  it('renders every section heading in English, paradigm distribution first of the grid', async () => {
    const wrapper = await mountAnalysis()

    expect(wrapper.text()).toContain('Paradigm Distribution')
    expect(wrapper.findAll('h3').map(heading => heading.text())).toEqual([
      'Call Trend', 'Paradigm Distribution', 'MCP Tool Distribution', 'Source Distribution', 'Top Query Terms',
    ])
    expect(wrapper.text()).not.toMatch(CJK)
  })

  it('labels the KPI cards and the trend series in English', async () => {
    const wrapper = await mountAnalysis()

    const labels = wrapper.findAllComponents({ name: 'StatsCard' }).map(card => card.props('label'))
    expect(labels).toEqual(['Calls', 'Retrieval Calls', 'No-result Rate', 'Failure Rate', 'Average Latency', 'P95 Latency'])
    const series = wrapper.findComponent({ name: 'LineChart' }).props('series')
    // 「零结果」「失败」图例与状态筛选同词
    expect(series.map((item: { name: string }) => item.name)).toEqual(['All Calls', 'No Result', 'Failed'])
  })

  it('translates chart labels that come from lookup functions, never server data', async () => {
    const wrapper = await mountAnalysis()

    const bars = wrapper.findAllComponents({ name: 'BarChart' })
    // 范式/Tool 名称是服务端数据，原样透出
    expect(bars[0].props('data')[0].name).toBe('alarm-paradigm')
    expect(bars[1].props('data')).toEqual([{ name: 'search_knowledge', value: 5 }])
    // 来源 key → 文案走界面语言
    expect(bars[2].props('data')).toEqual([{ name: 'Web', value: 4 }, { name: 'MCP', value: 6 }])
  })

  it('renders the empty hint of every chart in English', async () => {
    api.getSummary.mockResolvedValue({ ...summary, paradigms: [], tools: [], sources: {}, top_queries: [] })
    const wrapper = await mountAnalysis()

    expect(wrapper.findAll('.records-analysis__card p').map(hint => hint.text())).toEqual([
      'No paradigm calls yet', 'No Tool calls yet', 'No source data', 'No queries in this time range',
    ])
    expect(wrapper.findAllComponents({ name: 'BarChart' })).toHaveLength(0)
    expect(wrapper.findComponent({ name: 'WordCloud' }).exists()).toBe(false)
    expect(wrapper.text()).not.toMatch(CJK)
  })

  it('labels the time-range toolbar in English', async () => {
    const wrapper = await mountAnalysis()

    expect(wrapper.find('.records-analysis__toolbar-label').text()).toBe('Time Range')
    expect(wrapper.find('[aria-label="时间范围"]').exists()).toBe(false)
    const select = wrapper.find('select[aria-label="Time Range"]')
    expect(select.findAll('option').map(option => option.text()))
      .toEqual(['Last 7 days', 'Last 30 days', 'Last 90 days', 'Custom'])

    await select.setValue('custom')
    await flushPromises()
    expect(wrapper.find('.records-analysis__sep').text()).toBe('to')
    expect(wrapper.find('input[aria-label="Start Date"]').exists()).toBe(true)
    expect(wrapper.find('input[aria-label="End Date"]').exists()).toBe(true)
    expect(wrapper.text()).not.toMatch(CJK)
  })

  it('validates a reversed custom range in English without hitting the API', async () => {
    const wrapper = await mountAnalysis()

    await wrapper.find('select[aria-label="Time Range"]').setValue('custom')
    await wrapper.find('input[aria-label="Start Date"]').setValue('2026-10-21')
    await wrapper.find('input[aria-label="End Date"]').setValue('2026-10-20')
    await flushPromises()

    expect(wrapper.find('.records-analysis__range-error').text())
      .toBe('The start date cannot be later than the end date')
  })

  it('renders the unavailable notice in English', async () => {
    api.getSummary.mockResolvedValue({ ...summary, available: false })
    const wrapper = await mountAnalysis()

    expect(wrapper.find('.records-analysis__notice').text())
      .toBe('Unified records are produced starting from this release; there are no calls to analyze yet.')
    expect(wrapper.findAllComponents({ name: 'StatsCard' })).toHaveLength(0)
    expect(wrapper.text()).not.toMatch(CJK)
  })

  it('renders the loading notice in English', async () => {
    api.getSummary.mockReturnValue(new Promise(() => {}))
    const wrapper = shallowMount(RetrievalRecordsAnalysis, { global: { plugins: [pinia] } })
    // onMounted 置 loading 后 DOM 要等一个 tick
    await nextTick()

    expect(wrapper.find('.records-analysis__notice').text()).toBe('Loading usage analysis…')
    expect(wrapper.text()).not.toMatch(CJK)
  })

  it('renders the error notice with an English retry button', async () => {
    api.getSummary.mockRejectedValue(new Error('summary downstream down'))
    const wrapper = await mountAnalysis()

    const notice = wrapper.find('.records-analysis__notice--error')
    expect(notice.text()).toContain('summary downstream down')
    expect(notice.find('button').text()).toBe('Retry')
    expect(wrapper.text()).not.toMatch(CJK)
  })

  it('follows a runtime language switch reactively', async () => {
    const wrapper = await mountAnalysis()
    expect(wrapper.text()).toContain('Paradigm Distribution')
    expect(wrapper.findAllComponents({ name: 'BarChart' })[2].props('data')[0].name).toBe('Web')

    useLocaleStore().setLang('zh')
    await nextTick()

    expect(wrapper.text()).toContain('范式分布')
    expect(wrapper.findAllComponents({ name: 'BarChart' })[2].props('data')[0].name).toBe('网页')
    expect(wrapper.findComponent({ name: 'LineChart' }).props('series').map((item: { name: string }) => item.name))
      .toEqual(['全部调用', '零结果', '失败'])
    expect(wrapper.find('select').attributes('aria-label')).toBe('时间范围')
  })
})
