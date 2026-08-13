/**
 * SearchView 的默认检索范围接线（缺陷 D8）。
 *
 * 走 `?q=` 自动检索这条路径来观察实际发出的请求：Element Plus 在 test/setup.ts 里被
 * 全局 stub 掉了（el-select 不带 v-model），操作选择器等于在测 stub；而 onMounted 里
 * 「带 q 进来就直接搜」是真实代码路径，能一路验到 servingApi.search 的入参。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const kbApi = vi.hoisted(() => ({ getOverview: vi.fn() }))
const servingApi = vi.hoisted(() => ({ search: vi.fn(), fetchFullText: vi.fn() }))

vi.mock('@/api/kb', () => ({ useKbApi: () => kbApi }))
vi.mock('@/api/serving', () => ({ useServingApi: () => servingApi }))
vi.mock('@/stores/domain', () => ({
  useDomainStore: () => ({ currentDomain: 'cloud_core_network' }),
}))
vi.mock('@/api/proxyClient', () => ({ apiErrorDetail: async () => '请求失败' }))

import SearchView from '@/views/SearchView.vue'

function overview(kbs: string[], hasActiveRelease = false) {
  return {
    has_active_release: hasActiveRelease,
    kbs: kbs.map(id => ({
      id, name: id.toUpperCase(), my_role: 'owner', can_write: true,
      status_counts: { total: 3, mining: 0, failed: 0 },
      last_mined_at: null, awaiting_review_run_id: null,
    })),
    recent_runs: [],
  }
}

async function mountAt(query: Record<string, string>) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/search', name: 'search', component: SearchView }],
  })
  await router.push({ path: '/search', query })
  await router.isReady()
  const wrapper = mount(SearchView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

describe('SearchView 默认范围', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    servingApi.search.mockResolvedValue({ items: [] })
  })

  it('默认显式带上全部可见知识库，不再留空', async () => {
    kbApi.getOverview.mockResolvedValue(overview(['kb-a', 'kb-b']))

    await mountAt({ q: 'SMF 怎么配' })

    expect(servingApi.search).toHaveBeenCalledTimes(1)
    const [query, options] = servingApi.search.mock.calls[0]
    expect(query).toBe('SMF 怎么配')
    // 留空会被 serving.ts 省掉 kbIds 键 → 后端走域级 active release → no_active_release
    expect(options.kbIds).toEqual(['kb-a', 'kb-b'])
  })

  it('KB-only 部署（无 active release）下默认检索不会落到域级 release 分支', async () => {
    kbApi.getOverview.mockResolvedValue(overview(['kb-a'], false))

    await mountAt({ q: '测试' })

    const [, options] = servingApi.search.mock.calls[0]
    expect(options.kbIds.length).toBeGreaterThan(0)
  })

  it('一个可见知识库都没有时不发请求', async () => {
    kbApi.getOverview.mockResolvedValue(overview([]))

    await mountAt({ q: '测试' })

    expect(servingApi.search).not.toHaveBeenCalled()
  })

  it('范围加载失败时不发请求，也不静默退化成全域检索', async () => {
    kbApi.getOverview.mockRejectedValue(new Error('boom'))

    const wrapper = await mountAt({ q: '测试' })

    expect(servingApi.search).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('检索范围加载失败')
  })

  it('URL 带 kbIds 时按它收窄，并丢弃已不可见的 id', async () => {
    kbApi.getOverview.mockResolvedValue(overview(['kb-a', 'kb-b']))

    await mountAt({ q: '测试', kbIds: 'kb-b,kb-gone' })

    const [, options] = servingApi.search.mock.calls[0]
    expect(options.kbIds).toEqual(['kb-b'])
  })

  it('没带 q 时只准备范围，不自动检索', async () => {
    kbApi.getOverview.mockResolvedValue(overview(['kb-a']))

    await mountAt({})

    expect(kbApi.getOverview).toHaveBeenCalledWith('cloud_core_network')
    expect(servingApi.search).not.toHaveBeenCalled()
  })
})
