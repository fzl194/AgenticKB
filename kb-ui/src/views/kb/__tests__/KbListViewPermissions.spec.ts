import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  auth: { siteRole: 'member' as 'admin' | 'member' },
  domain: {
    currentDomain: 'domain_a',
    currentDomainInfo: {
      domain_id: 'domain_a', display_name: 'Domain A', enabled: true,
      default_channel: 'prod', scenario_pack_ref: 'domain_a',
      domain_role: 'admin' as 'admin' | 'member',
      capabilities: ['domain.users.manage', 'domain.kbs.manage'] as string[],
    },
  },
  api: {
    listKbs: vi.fn(),
    listDeletedKbs: vi.fn(),
    purgeTasks: vi.fn(),
    restoreKb: vi.fn(),
  },
}))

vi.mock('@/stores/auth', () => ({ useAuthStore: () => state.auth }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => state.domain }))
vi.mock('@/api/kb', () => ({ useKbApi: () => state.api }))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn().mockResolvedValue(undefined), prompt: vi.fn() },
}))

import KbListView from '@/views/kb/KbListView.vue'

describe('KbListView domain-admin lifecycle access', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.auth.siteRole = 'member'
    state.domain.currentDomainInfo.domain_role = 'admin'
    state.domain.currentDomainInfo.capabilities = ['domain.users.manage', 'domain.kbs.manage']
    state.api.listKbs.mockResolvedValue([])
    state.api.listDeletedKbs.mockResolvedValue([])
    state.api.purgeTasks.mockResolvedValue([])
    state.api.restoreKb.mockResolvedValue({})
  })

  it('loads deleted KBs for an administrator of the selected domain', async () => {
    shallowMount(KbListView)
    await flushPromises()

    expect(state.api.listDeletedKbs).toHaveBeenCalledWith('domain_a')
  })

  it('does not expose deleted KBs to an ordinary domain member', async () => {
    state.domain.currentDomainInfo.domain_role = 'member'
    state.domain.currentDomainInfo.capabilities = []

    shallowMount(KbListView)
    await flushPromises()

    expect(state.api.listDeletedKbs).not.toHaveBeenCalled()
  })
})
