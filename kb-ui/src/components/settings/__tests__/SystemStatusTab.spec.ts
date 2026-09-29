/**
 * 设置 → 系统状态。
 *
 * 知识资产区块已随域级 release 口径退役下线（后端 /api/knowledge/stats 在
 * 瘦身批次4 改成 KB-scoped 必填 kb_id 后，前端就再也没跟上了）。本 tab 现在只
 * 钉服务健康面：三个服务的探测、单点失败隔离、member 权限与切域竞态。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { enableAutoUnmount, mount, flushPromises } from '@vue/test-utils'

const miningApi = vi.hoisted(() => ({ getHealth: vi.fn() }))
const servingApi = vi.hoisted(() => ({ getHealth: vi.fn() }))
const llmApi = vi.hoisted(() => ({ getHealth: vi.fn() }))
const controlPlaneApi = vi.hoisted(() => ({ getRestartStatus: vi.fn() }))
const domainRef = vi.hoisted(() => ({ current: null as { value: string } | null }))
const roleRef = vi.hoisted(() => ({ current: null as { value: string } | null }))

vi.mock('@/api/mining', () => ({ useMiningApi: () => miningApi }))
vi.mock('@/api/serving', () => ({ useServingApi: () => servingApi }))
vi.mock('@/api/llm', () => ({ useLlmApi: () => llmApi }))
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

import SystemStatusTab from '@/components/settings/SystemStatusTab.vue'

enableAutoUnmount(afterEach)

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
    controlPlaneApi.getRestartStatus.mockResolvedValue({ state: 'idle', active: false })
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

  it('某个服务探测失败只影响它自己', async () => {
    servingApi.getHealth.mockRejectedValue(new Error('down'))

    const wrapper = await mountTab()

    const cards = wrapper.findAllComponents({ name: 'ServiceHealthCard' })
    expect(cards[0].props('status')).toBe('healthy')
    expect(cards[1].props('status')).toBe('unhealthy')
    expect(cards[2].props('status')).toBe('healthy')
  })

  it('member 看不到一键重启入口', async () => {
    const wrapper = await mountTab()

    expect(wrapper.find('[data-testid="service-restart"]').exists()).toBe(false)
  })

  it('域在退出时被清空后不再发起健康请求', async () => {
    domainRef.current!.value = ''

    await mountTab()

    expect(miningApi.getHealth).not.toHaveBeenCalled()
    expect(servingApi.getHealth).not.toHaveBeenCalled()
    expect(llmApi.getHealth).not.toHaveBeenCalled()
  })

  it('知识资产区块已下线，页面不再渲染该口径', async () => {
    const wrapper = await mountTab()

    expect(wrapper.text()).not.toContain('知识资产')
    expect(wrapper.text()).not.toContain('口径')
  })
})
