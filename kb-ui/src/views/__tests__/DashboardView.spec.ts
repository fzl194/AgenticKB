/**
 * 概览页（统计仪表盘版）的接线。
 *
 * 重点在几条改版最容易丢的行为：两个数据源各自的错误隔离、切域竞态守卫、
 * 「一个库都没有」不画一屏零、以及保留下来的待处理/卡片/最近挖掘三块。
 * 图表本身的数据派生在 utils/__tests__/dashboardStats.spec.ts 里钉，这里只验接线。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { enableAutoUnmount, mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const kbApi = vi.hoisted(() => ({ getOverview: vi.fn(), getStats: vi.fn() }))
const opsApi = vi.hoisted(() => ({ getUsage: vi.fn() }))
/** 角色也必须是响应式的，理由同域：组件用 computed 读它。 */
const roleRef = vi.hoisted(() => ({ current: null as { value: string } | null }))
/**
 * 域必须是**响应式**的，否则 `watch(() => domainStore.currentDomain)` 根本不会触发，
 * 切域竞态那条用例就会在"第二次请求从未发出"的情况下假绿。
 */
const domainRef = vi.hoisted(() => ({ current: null as { value: string } | null }))

vi.mock('@/api/kb', () => ({ useKbApi: () => kbApi }))
vi.mock('@/api/ops', () => ({ useOpsApi: () => opsApi }))
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

// 三个图表都依赖 echarts 的真实布局（jsdom 下拿不到尺寸），这里只关心渲不渲染。
vi.mock('@/components/charts/PieChart.vue', () => ({
  default: { name: 'PieChart', template: '<div class="pie-stub" />' },
}))
vi.mock('@/components/charts/BarChart.vue', () => ({
  default: { name: 'BarChart', template: '<div class="bar-stub" />' },
}))
vi.mock('@/components/charts/LineChart.vue', () => ({
  default: { name: 'LineChart', template: '<div class="line-stub" />' },
}))

function setDomain(name: string) {
  domainRef.current!.value = name
}

function setRole(role: 'admin' | 'member') {
  roleRef.current!.value = role
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
    no_result_queries: [{ query_text: 'SMF 会话建立超时', count: 12, last_at: null }],
    top_queries: [{ query_text: '5GC 计费接口', count: 30, no_result: 2 }],
    paradigms: [{ paradigm_id: 'p-1', calls: 80, no_result: 3, p95_duration_ms: 300 }],
    trend: [{ date: '2026-08-18', queries: 10, no_result: 1 }],
    intents: { lookup: 60 },
    channels: { mcp: 100 },
    ...over,
  }
}

// 用例之间必须卸载：域 ref 是模块级共享的，残留的组件仍挂着 watch，
// beforeEach 里改域会让它们也发请求，把下一条用例排好的 mock 队列吃掉。
enableAutoUnmount(afterEach)

import DashboardView from '@/views/DashboardView.vue'

function kb(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    name: id.toUpperCase(),
    my_role: 'owner',
    can_write: true,
    status_counts: { total: 4, mining: 0, failed: 0 },
    last_mined_at: null,
    awaiting_review_run_id: null,
    ...over,
  }
}

function overview(kbs: unknown[] = [], recent_runs: unknown[] = []) {
  return { has_active_release: false, kbs, recent_runs }
}

function stats(over: Record<string, unknown> = {}) {
  return {
    kb_count: 1,
    has_active_release: false,
    trend_days: 30,
    document_status: {
      uploaded: 1, mining: 0, mined: 3, published: 0, withdrawn: 0, failed: 0,
    },
    assets: {
      snapshots: 3, segments: 90, retrieval_units: 120,
      entity_mentions: 40, relations: 65,
    },
    retrieval_unit_types: { raw_text: 80, summary: 40 },
    mining_trend: [
      { date: '2026-08-17', runs: 1, completed: 1, documents: 3 },
      { date: '2026-08-18', runs: 0, completed: 0, documents: 0 },
    ],
    ...over,
  }
}

async function mountDash() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'dashboard', component: DashboardView },
      { path: '/kb', name: 'kb', component: { template: '<div/>' } },
      { path: '/kb/:kbId', name: 'kb-detail', component: { template: '<div/>' } },
      { path: '/kb/:kbId/run/:runId', name: 'run', component: { template: '<div/>' } },
      { path: '/search', name: 'search', component: { template: '<div/>' } },
    ],
  })
  await router.push('/')
  await router.isReady()
  const wrapper = mount(DashboardView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

describe('概览页（统计仪表盘）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setDomain('cloud_core_network')
    setRole('member')          // 默认非 admin，运维区块的用例各自提权
    kbApi.getOverview.mockResolvedValue(overview([kb('kb-a')]))
    kbApi.getStats.mockResolvedValue(stats())
    opsApi.getUsage.mockResolvedValue(usage())
  })

  it('两个聚合接口各打一次，都带当前域', async () => {
    await mountDash()

    expect(kbApi.getOverview).toHaveBeenCalledTimes(1)
    expect(kbApi.getStats).toHaveBeenCalledTimes(1)
    expect(kbApi.getOverview).toHaveBeenCalledWith('cloud_core_network')
    expect(kbApi.getStats).toHaveBeenCalledWith('cloud_core_network')
  })

  it('不再有搜索框', async () => {
    const { wrapper } = await mountDash()

    expect(wrapper.find('input').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('搜索')
  })

  it('渲染汇总数字：文档数取六态之和，不与各库 total 相加的那个数并存', async () => {
    const { wrapper } = await mountDash()
    const text = wrapper.text()

    expect(text).toContain('知识库')
    expect(text).toContain('检索单元')
    expect(text).toContain('120')     // retrieval_units
    expect(text).toContain('4')       // uploaded 1 + mined 3
  })

  it('四张图表都渲染，环形图旁必须有数值表（颜色不能是唯一区分手段）', async () => {
    const { wrapper } = await mountDash()

    expect(wrapper.find('.pie-stub').exists()).toBe(true)
    expect(wrapper.find('.line-stub').exists()).toBe(true)
    expect(wrapper.findAll('.bar-stub').length).toBe(2)
    // 图例行带名称 + 数字
    expect(wrapper.findAll('.legend__row').length).toBeGreaterThan(0)
    expect(wrapper.text()).toContain('已挖掘')
  })

  it('域内无 active release 时，状态图不出现「已发布」「已撤回」两档', async () => {
    const { wrapper } = await mountDash()

    expect(wrapper.text()).not.toContain('已发布')
    expect(wrapper.text()).not.toContain('已撤回')
  })

  it('有 active release 时才把发布两档纳入统计', async () => {
    kbApi.getStats.mockResolvedValue(stats({
      has_active_release: true,
      document_status: {
        uploaded: 0, mining: 0, mined: 1, published: 2, withdrawn: 1, failed: 0,
      },
    }))

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('已发布')
    expect(wrapper.text()).toContain('已撤回')
  })

  it('一个知识库都没有时给建库入口，而不是画一屏 0', async () => {
    kbApi.getOverview.mockResolvedValue(overview([]))
    kbApi.getStats.mockResolvedValue(stats({ kb_count: 0 }))

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('建一个就能看到统计')
    expect(wrapper.find('.pie-stub').exists()).toBe(false)
  })

  it('统计接口挂掉不牵连知识库卡片与最近挖掘', async () => {
    kbApi.getStats.mockRejectedValue(new Error('boom'))
    kbApi.getOverview.mockResolvedValue(overview([kb('kb-a')], [{
      id: 'run-1', kb_id: 'kb-a', kb_name: '核心网文档', status: 'completed',
      total_documents: 12, new_count: 3, updated_count: 1,
      started_at: '2026-08-11T09:12:00+00:00',
      finished_at: '2026-08-11T09:14:14+00:00',
    }]))

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('加载失败')     // 统计块自己报错
    expect(wrapper.text()).toContain('KB-A')         // 卡片还在
    expect(wrapper.text()).toContain('核心网文档')   // 最近挖掘还在
  })

  it('overview 挂掉时统计数字仍然显示', async () => {
    kbApi.getOverview.mockRejectedValue(new Error('boom'))

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('加载失败')
    expect(wrapper.text()).toContain('120')          // 统计块的检索单元数还在
  })

  it('无待处理时整块不渲染', async () => {
    const { wrapper } = await mountDash()

    expect(wrapper.text()).not.toContain('待处理')
  })

  it('有待人审时列出任务条', async () => {
    kbApi.getOverview.mockResolvedValue(
      overview([kb('kb-a', { awaiting_review_run_id: 'run-9' })]),
    )

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('待处理')
    expect(wrapper.text()).toContain('挖掘已暂停，等待人工审核')
  })

  it('最近挖掘用 started_at 且行内不出现 Run ID 死链', async () => {
    kbApi.getOverview.mockResolvedValue(overview([kb('kb-a')], [{
      id: 'run-1', kb_id: 'kb-a', kb_name: '核心网文档', status: 'completed',
      total_documents: 12, new_count: 3, updated_count: 1,
      started_at: '2026-08-11T09:12:00+00:00',
      finished_at: '2026-08-11T09:14:14+00:00',
    }]))

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('核心网文档')
    expect(wrapper.text()).toContain('已完成')             // 不再显示英文 status
    expect(wrapper.html()).not.toContain('/mining/run-1')  // 已删除的旧路由形状
  })

  // ── 运维区块（admin-only）────────────────────────────────────────────

  it('member 看不到运维区块，也不发那个请求', async () => {
    const { wrapper } = await mountDash()

    expect(wrapper.text()).not.toContain('运维概览')
    // 后端会 403：白打一次往返还在控制台留一条红
    expect(opsApi.getUsage).not.toHaveBeenCalled()
  })

  it('admin 才渲染运维区块并取数', async () => {
    setRole('admin')

    const { wrapper } = await mountDash()

    expect(opsApi.getUsage).toHaveBeenCalledWith('cloud_core_network')
    expect(wrapper.text()).toContain('运维概览')
    expect(wrapper.text()).toContain('零结果率')
  })

  it('admin 能看到答不上来的问题原文——这是整块里最有行动价值的一段', async () => {
    setRole('admin')

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('SMF 会话建立超时')
    expect(wrapper.text()).toContain('12 次')
  })

  it('运维接口挂掉不牵连知识库统计', async () => {
    setRole('admin')
    opsApi.getUsage.mockRejectedValue(new Error('boom'))

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('运维数据加载失败')
    expect(wrapper.text()).toContain('120')      // 检索单元数还在
    expect(wrapper.text()).toContain('KB-A')     // 卡片还在
  })

  it('serving 没产出过日志时说明原因，而不是画一屏 0', async () => {
    setRole('admin')
    opsApi.getUsage.mockResolvedValue(usage({
      available: false,
      summary: {
        queries: 0, no_result: 0, no_result_rate: 0,
        p95_duration_ms: 0, avg_duration_ms: 0, active_paradigms: 0,
      },
    }))

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('尚未产生检索日志')
    expect(wrapper.text()).not.toContain('零结果率')
  })

  it('零结果率高但样本太少时不报警——3 次里 1 次就是 33%，据此弹红没有意义', async () => {
    setRole('admin')
    opsApi.getUsage.mockResolvedValue(usage({
      summary: {
        queries: 3, no_result: 1, no_result_rate: 0.333,
        p95_duration_ms: 100, avg_duration_ms: 80, active_paradigms: 1,
      },
    }))

    const { wrapper } = await mountDash()

    expect(wrapper.text()).not.toContain('建议从下面的清单补充知识')
  })

  it('样本足够且零结果率超阈值时才报警', async () => {
    setRole('admin')
    opsApi.getUsage.mockResolvedValue(usage({
      summary: {
        queries: 200, no_result: 60, no_result_rate: 0.3,
        p95_duration_ms: 100, avg_duration_ms: 80, active_paradigms: 1,
      },
    }))

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('建议从下面的清单补充知识')
  })

  it('切域时丢弃旧域的迟到响应（竞态守卫）', async () => {
    // 第一次请求慢，第二次快；旧响应后到，不得覆盖新域的数据
    let resolveSlow: (v: unknown) => void = () => {}
    kbApi.getOverview
      .mockImplementationOnce(() => new Promise(res => { resolveSlow = res }))
      .mockResolvedValueOnce(overview([kb('new-domain-kb')]))

    const { wrapper } = await mountDash()

    setDomain('generic')
    await wrapper.vm.$nextTick()
    await flushPromises()

    resolveSlow(overview([kb('stale-kb')]))
    await flushPromises()

    expect(wrapper.text()).toContain('NEW-DOMAIN-KB')
    expect(wrapper.text()).not.toContain('STALE-KB')
  })
})
