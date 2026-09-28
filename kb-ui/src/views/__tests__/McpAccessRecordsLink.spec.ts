import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, shallowMount } from '@vue/test-utils'

const kbApi = vi.hoisted(() => ({ listMcpKeys: vi.fn() }))
const push = vi.hoisted(() => vi.fn())
const domainStore = vi.hoisted(() => ({ currentDomain: 'domain-a', enabledDomains: [], fetchDomains: vi.fn(), switchDomain: vi.fn() }))

vi.mock('@/api/kb', () => ({ useKbApi: () => kbApi }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => domainStore }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ user: { username: 'alice' } }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

import McpAccessView from '@/views/McpAccessView.vue'

enableAutoUnmount(afterEach)

describe('MCP key retrieval records deep link', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    kbApi.listMcpKeys.mockResolvedValue({ keys: [{
      id: 'key-1', name: 'Agent A', domain: 'domain-a', domain_bound: true,
      key_prefix: 'abc', status: 'active', created_at: '2026-09-28T00:00:00Z', last_used_at: null,
    }] })
  })

  it('opens the unified records page with the selected key filter', async () => {
    const wrapper = shallowMount(McpAccessView)
    await flushPromises()
    expect(wrapper.text()).toContain('调用记录')
    const setup = (wrapper.vm.$ as unknown as {
      setupState: { viewRecords: (row: { id: string }) => void }
    }).setupState
    setup.viewRecords({ id: 'key-1' })

    expect(domainStore.switchDomain).not.toHaveBeenCalled()
    expect(push).toHaveBeenCalledWith({ name: 'retrieval-records', query: { mcpKeyId: 'key-1' } })
  })
})
