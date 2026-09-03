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
const opsApi = vi.hoisted(() => ({ getUsage: vi.fn() }))
const controlPlaneApi = vi.hoisted(() => ({ getRestartStatus: vi.fn() }))
const domainRef = vi.hoisted(() => ({ current: null as { value: string } | null }))
const roleRef = vi.hoisted(() => ({ current: null as { value: string } | null }))

vi.mock('@/api/mining', () => ({ useMiningApi: () => miningApi }))
vi.mock('@/api/serving', () => ({ useServingApi: () => servingApi }))
vi.mock('@/api/llm', () => ({ useLlmApi: () => llmApi }))
vi.mock('@/api/ops', () => ({ useOpsApi: () => opsApi }))
vi.mock('@/api/controlPlane', () => ({ useControlPlaneApi: () => controlPlaneApi }))
vi.mock('@/stores/auth', async () => {
  const { ref } = await import('vue')
  roleRef.current = ref('member')
  return {
    useAuthStore: () => ({
      get siteRole() { return roleRef.current!.value },
    }),
  }
})
vi.mock('@/stores/domain', async () => {
  const { ref } = await import('vue')
  domainRef.current = ref('cloud_core_network')
  return {
    useDomainStore: () => ({
      get currentDomain() { return domainRef.current!.value },
    }),
  }
})
// 图表依赖 echarts 的真实布局，这里只关心渲不渲染
vi.mock('@/components/charts/PieChart.vue', () => ({
  default: { name: 'PieChart', template: '<div class="pie-stub" />' },
}))
vi.mock('@/components/charts/BarChart.vue', () => ({
  default: { name: 'BarChart', template: '<div class="bar-stub" />' },
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

function usage(over: Record<string, unknown> = {}) {
  return {
    available: true,
    days: 7,
    trend_days: 30,
    summary: {
      queries: 100, no_result: 7, no_result_rate: 0.07,
      p95_duration_ms: 412, avg_duration_ms: 180, active_paradigms: 2,
    },
    no_result_queries: [
      { query_text: 'SMF 会话建立超时', count: 12, last_at: '2026-08-18T01:00:00Z' },
    ],
    top_queries: [{ query_text: '5GC 计费接口', count: 30, no_result: 2 }],
    paradigms: [{ paradigm_id: 'p-1', calls: 80, no_result: 3, p95_duration_ms: 300 }],
    trend: [{ date: '2026-08-18', queries: 10, no_result: 1 }],
    intents: { lookup: 60 },
    channels: { mcp: 100 },
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
    opsApi.getUsage.mockResolvedValue(usage())
    miningApi.getStats.mockResolvedValue(stats())
    controlPlaneApi.getRestartStatus.mockResolvedValue({ state: 'idle', active: false })
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
    // 知识资产那一组统计卡不渲染——0 在这个口径下没有意义。
    // 只能按 label 判，不能数全页 StatsCard：检索使用分析那一段也有 5 张，
    // 它是另一个口径（全域检索流量），不受有没有 release 影响。
    const labels = wrapper.findAllComponents({ name: 'StatsCard' })
      .map(c => c.props('label'))
    expect(labels).not.toContain('快照')
    expect(labels).not.toContain('段落')
    expect(labels).toContain('检索次数')      // 使用分析那组照常在
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

  // ── 检索使用分析 ────────────────────────────────────────────────────

  it('给出比概览页更全的明细：零结果清单 / 范式表 / 热门查询 / 双分布图', async () => {
    const wrapper = await mountTab()
    const text = wrapper.text()

    expect(text).toContain('答不上来的问题')
    expect(text).toContain('SMF 会话建立超时')     // 全量清单（概览页只给前 5）
    expect(text).toContain('各检索范式')
    expect(text).toContain('热门查询')
    expect(text).toContain('5GC 计费接口')
    expect(text).toContain('查询意图分布')
    expect(text).toContain('接入渠道分布')
    expect(wrapper.findAll('.bar-stub').length).toBe(2)   // 意图 + 渠道
  })

  it('平均延迟与 P95 并排给出——两者差距就是长尾的严重程度', async () => {
    const wrapper = await mountTab()

    expect(wrapper.text()).toContain('P95 延迟')
    expect(wrapper.text()).toContain('平均延迟')
  })

  it('serving 没产出过日志时说明原因，而不是画一排 0', async () => {
    opsApi.getUsage.mockResolvedValue(usage({ available: false }))

    const wrapper = await mountTab()

    expect(wrapper.text()).toContain('尚未产生检索日志')
    expect(wrapper.text()).not.toContain('各检索范式')
  })

  it('使用分析挂掉不牵连服务状态与知识资产', async () => {
    opsApi.getUsage.mockRejectedValue(new Error('boom'))

    const wrapper = await mountTab()

    // 服务状态那三张卡还在
    expect(wrapper.findAllComponents({ name: 'ServiceHealthCard' })).toHaveLength(3)
    expect(wrapper.text()).toContain('知识资产')
  })

  it('member 看不到一键重启入口', async () => {
    const wrapper = await mountTab()

    expect(wrapper.find('[data-testid="service-restart"]').exists()).toBe(false)
  })
})
