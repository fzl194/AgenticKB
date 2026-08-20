/**
 * 设置页的 ?tab= 深链。
 *
 * 概览页运维区块的「详情 →」靠它落到「系统状态」。白名单是承重的：el-tabs 收到一个
 * 不存在的 name 会一个面板都不渲染，页面看起来像坏了——而 query 是用户可改的。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { enableAutoUnmount, mount } from '@vue/test-utils'

/** useRoute() 读的是注入的 router 上下文，global.mocks.$route 够不到它。 */
const routeQuery = vi.hoisted(() => ({ current: {} as Record<string, unknown> }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ get query() { return routeQuery.current } }),
}))

vi.mock('@/stores/controlPlane', () => ({
  useControlPlaneStore: () => ({}),
}))

enableAutoUnmount(afterEach)

import SettingsView from '@/views/SettingsView.vue'

/**
 * shallow 挂载：各 tab 内容各自要 store / API，全部自动 stub 掉。
 * 这里只关心 activeTab 落在哪个值上，不关心面板里画了什么。
 */
async function mountAt(query: Record<string, unknown>) {
  routeQuery.current = query
  return mount(SettingsView, { shallow: true })
}

describe('设置页 ?tab= 深链', () => {
  it('合法 tab 名被采纳', async () => {
    const w = await mountAt({ tab: 'status' })
    expect(w.vm.activeTab).toBe('status')
  })

  it('未知 tab 名回落到默认，而不是让 el-tabs 渲染空白', async () => {
    const w = await mountAt({ tab: 'no-such-tab' })
    expect(w.vm.activeTab).toBe('system')
  })

  it('没带 tab 时用默认', async () => {
    const w = await mountAt({})
    expect(w.vm.activeTab).toBe('system')
  })

  it('数组形态的 query（?tab=a&tab=b）不采信', async () => {
    const w = await mountAt({ tab: ['status', 'logs'] })
    expect(w.vm.activeTab).toBe('system')
  })
})
