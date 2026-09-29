import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, shallowMount } from '@vue/test-utils'

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

enableAutoUnmount(afterEach)

describe('RetrievalRecordsAnalysis', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useRetrievalNames().reset()
    kbApi.listKbs.mockResolvedValue([{ id: 'kb-1', name: '云核心网手册' }])
    operatorApi.listParadigms.mockResolvedValue([{ id: 'p-1', name: '告警范式' }])
    api.getSummary.mockResolvedValue({
      available: true, days: 7,
      summary: { total_calls: 10, calls: 7, no_result: 2, failed: 1, no_result_rate: .2, failure_rate: .1, p95_duration_ms: 300, avg_duration_ms: 100, active_paradigms: 1 },
      trend: [{ date: '2026-09-28', total_calls: 10, calls: 7, no_result: 2, failed: 1 }],
      sources: { web: 4, mcp: 6 },
      tools: [{ tool_name: 'search_knowledge', calls: 5, no_result: 1 }],
      paradigms: [{ paradigm_id: 'p-1', calls: 7, no_result: 2 }],
      no_result_queries: [{ query_text: '未命中的问题', count: 2, no_result: 2, last_at: '2026-09-28T00:00:00Z' }],
      top_queries: [{ query_text: '热门问题', count: 5, no_result: 0, last_at: '2026-09-28T00:00:00Z' }],
    })
  })

  it('renders trend and every migrated analysis section', async () => {
    const wrapper = shallowMount(RetrievalRecordsAnalysis)
    await flushPromises()

    expect(wrapper.findComponent({ name: 'LineChart' }).exists()).toBe(true)
    expect(wrapper.findAllComponents({ name: 'BarChart' })).toHaveLength(3)
    expect(wrapper.text()).toContain('范式分布')
    expect(wrapper.text()).toContain('MCP Tool 分布')
    expect(wrapper.text()).toContain('来源分布')
    // 范式分布按名称展示，不露原始 ID
    const paradigmBars = wrapper.findAllComponents({ name: 'BarChart' })[0]
    expect(paradigmBars.props('data')[0].name).toBe('告警范式')
    const queryLists = wrapper.findAllComponents({ name: 'QueryList' })
    expect(queryLists[0].props('items')[0].text).toBe('未命中的问题')
    expect(queryLists[1].props('items')[0].text).toBe('热门问题')
  })
})
