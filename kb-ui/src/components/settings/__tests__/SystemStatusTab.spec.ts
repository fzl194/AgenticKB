/**
 * 设置 → 系统状态。
 *
 * 从概览页搬过来的运维内容。这里要钉的核心是：**把"没数据"和
 * "口径不适用"区分开**——纯 KB 部署下这些计数恒为 0，摆一排 0 会让人以为知识丢了。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { enableAutoUnmount, mount, flushPromises } from '@vue/test-utils'

const miningApi = vi.hoisted(() => ({ getStats: vi.fn(), getHealth: vi.fn() }))
const servingApi = vi.hoisted(() => ({ getHealth: vi.fn() }))
const llmApi = vi.hoisted(() => ({ getHealth: vi.fn() }))
const domainRef = vi.hoisted(() => ({ current: null as { value: string } | null }))

vi.mock('@/api/mining', () => ({ useMiningApi: () => miningApi }))
vi.mock('@/api/serving', () => ({ useServingApi: () => servingApi }))
vi.mock('@/api/llm', () => ({ useLlmApi: () => llmApi }))
vi.mock('@/stores/domain', async () => {
  const { ref } = await import('vue')
  domainRef.current = ref('cloud_core_network')
  return {
    useDomainStore: () => ({
      get currentDomain() { return domainRef.current!.value },
    }),
  }
})
// PieChart 依赖 echarts 的真实布局，这里只关心它渲不渲染
vi.mock('@/components/charts/PieChart.vue', () => ({
  default: { name: 'PieChart', template: '<div class="pie-stub" />' },
}))

import SystemStatusTab from '@/components/settings/SystemStatusTab.vue'

enableAutoUnmount(afterEach)

const RELEASE = { id: 'rel-1', domain: 'cloud_core_network', channel: 'prod' }

function stats(over: Record<string, unknown> = {}) {
  return {
    documents: 42, snapshots: 42, segments: 900, relations: 120,
    retrieval_units: 300, embeddings: 300, builds: 1, releases: 1,
    retrieval_units_by_type: { raw_text: 200, summary: 100 },
    active_releases: [RELEASE],
    ...over,
  }
}

async function mountTab() {
  const wrapper = mount(SystemStatusTab)
  await flushPromises()
  return wrapper
}

describe('系统状态 tab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    domainRef.current!.value = 'cloud_core_network'
    miningApi.getHealth.mockResolvedValue({ status: 'healthy', version: '3.0.0' })
    servingApi.getHealth.mockResolvedValue({ status: 'UP' })
    llmApi.getHealth.mockResolvedValue({ status: 'ok' })
    miningApi.getStats.mockResolvedValue(stats())
  })

  it('标注口径，不让人把域级数字当成全部知识', async () => {
    const wrapper = await mountTab()
    expect(wrapper.text()).toContain('域级 active release')
  })

  it('无 active release 时说明口径不适用，而不是渲染一排 0', async () => {
    miningApi.getStats.mockResolvedValue(stats({
      documents: 0, snapshots: 0, segments: 0, relations: 0,
      retrieval_units: 0, embeddings: 0, builds: 0, releases: 0,
      retrieval_units_by_type: {},
      active_releases: [],
    }))

    const wrapper = await mountTab()

    expect(wrapper.text()).toContain('该域无发布语料')
    // 统计卡整组不渲染——0 在这里没有意义
    expect(wrapper.findAllComponents({ name: 'StatsCard' })).toHaveLength(0)
  })

  it('有 release 但内容为空时，0 是真的 0，照常渲染', async () => {
    // 撤回最后一个文档会发布一个空的 active build，后端刻意保留这个区分
    miningApi.getStats.mockResolvedValue(stats({
      documents: 0, snapshots: 0, segments: 0, relations: 0,
      retrieval_units: 0, embeddings: 0,
      retrieval_units_by_type: {},
      active_releases: [RELEASE],
    }))

    const wrapper = await mountTab()

    expect(wrapper.text()).not.toContain('该域无发布语料')
    expect(wrapper.findAllComponents({ name: 'StatsCard' }).length).toBeGreaterThan(0)
  })

  it('三个服务的健康都探测并展示', async () => {
    const wrapper = await mountTab()

    expect(miningApi.getHealth).toHaveBeenCalled()
    expect(servingApi.getHealth).toHaveBeenCalled()
    expect(llmApi.getHealth).toHaveBeenCalled()
    expect(wrapper.text()).toContain('挖掘服务')
    expect(wrapper.text()).toContain('检索服务')
    expect(wrapper.text()).toContain('LLM服务')
  })

  it('健康检查与资产统计各用一个竞态计数器——否则健康结果会被统计作废', async () => {
    const wrapper = await mountTab()

    // 两块都成功落地：共用一个 generation 时，后发起的 loadStats 会把 loadHealth 判为过期
    const cards = wrapper.findAllComponents({ name: 'ServiceHealthCard' })
    expect(cards).toHaveLength(3)
    expect(cards.every(c => c.props('status') !== 'unknown')).toBe(true)
  })

  it('某个服务探测失败只影响它自己', async () => {
    servingApi.getHealth.mockRejectedValue(new Error('down'))

    const wrapper = await mountTab()

    const cards = wrapper.findAllComponents({ name: 'ServiceHealthCard' })
    expect(cards[0].props('status')).toBe('healthy')
    expect(cards[1].props('status')).toBe('unhealthy')
    expect(cards[2].props('status')).toBe('healthy')
  })

  it('统计加载失败要看得见', async () => {
    miningApi.getStats.mockRejectedValue(new Error('boom'))

    const wrapper = await mountTab()

    expect(wrapper.text()).toContain('加载失败')
  })
})
