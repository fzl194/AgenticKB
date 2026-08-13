/**
 * 概览页（搜索前置版）的接线（设计文档 §4.1 / §4.4）。
 *
 * 重点在几条改版最容易丢的行为：单一数据源、切域竞态守卫（D7）、失败可见（不再被
 * Promise.allSettled 吞成空白）、以及区块 2 的"有内容才渲染"。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { enableAutoUnmount, mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const kbApi = vi.hoisted(() => ({ getOverview: vi.fn() }))
/**
 * 域必须是**响应式**的，否则 `watch(() => domainStore.currentDomain)` 根本不会触发，
 * 切域竞态那条用例就会在"第二次请求从未发出"的情况下假绿。
 */
const domainRef = vi.hoisted(() => ({ current: null as { value: string } | null }))

vi.mock('@/api/kb', () => ({ useKbApi: () => kbApi }))
vi.mock('@/stores/domain', async () => {
  const { ref } = await import('vue')
  domainRef.current = ref('cloud_core_network')
  return {
    useDomainStore: () => ({
      get currentDomain() { return domainRef.current!.value },
    }),
  }
})

function setDomain(name: string) {
  domainRef.current!.value = name
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

describe('概览页', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setDomain('cloud_core_network')
  })

  it('只打一次聚合接口——不再是 5 个数据源各查各的', async () => {
    kbApi.getOverview.mockResolvedValue(overview([kb('kb-a')]))

    await mountDash()

    expect(kbApi.getOverview).toHaveBeenCalledTimes(1)
    expect(kbApi.getOverview).toHaveBeenCalledWith('cloud_core_network')
  })

  it('标题显示可搜范围', async () => {
    kbApi.getOverview.mockResolvedValue(overview([kb('kb-a'), kb('kb-b')]))

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('在 2 个知识库中搜索')
  })

  it('无待处理时整块不渲染', async () => {
    kbApi.getOverview.mockResolvedValue(overview([kb('kb-a')]))

    const { wrapper } = await mountDash()

    expect(wrapper.text()).not.toContain('待处理')
  })

  it('有待人审时列出任务条并给出可点的深链', async () => {
    kbApi.getOverview.mockResolvedValue(
      overview([kb('kb-a', { awaiting_review_run_id: 'run-9' })]),
    )

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('待处理')
    expect(wrapper.text()).toContain('挖掘已暂停，等待人工审核')
  })

  it('加载失败要看得见，而不是渲染成空白', async () => {
    kbApi.getOverview.mockRejectedValue(new Error('boom'))

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('加载失败')
  })

  it('没有可见知识库时禁用搜索并给建库入口', async () => {
    kbApi.getOverview.mockResolvedValue(overview([]))

    const { wrapper } = await mountDash()

    expect(wrapper.text()).toContain('你还没有可检索的知识库')
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
    expect(wrapper.text()).toContain('已完成')          // D5：不再显示英文 status
    expect(wrapper.html()).not.toContain('/mining/run-1')  // D1：旧死链形状
  })

  it('切域时丢弃旧域的迟到响应（D7 竞态守卫）', async () => {
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
