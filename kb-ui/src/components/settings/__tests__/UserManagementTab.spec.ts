import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  listUsers: vi.fn(),
  createUser: vi.fn(),
  resetPassword: vi.fn(),
  updateUser: vi.fn(),
  getUserDomains: vi.fn(),
  setUserDomains: vi.fn(),
  getUserDomainGrants: vi.fn(),
  setUserDomainGrants: vi.fn(),
  listDomainUsers: vi.fn(),
  addDomainUser: vi.fn(),
  removeDomainUser: vi.fn(),
}))
const ui = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }))

vi.mock('@/api/auth', () => ({ useAuthApi: () => api }))
vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: async () => '失败',
  installAuthInterceptors: vi.fn(),
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: ui.success, error: ui.error, warning: ui.warning },
  ElMessageBox: { prompt: vi.fn().mockRejectedValue('cancel') },
}))

import UserManagementTab from '../UserManagementTab.vue'

describe('UserManagementTab', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('lists users on load', async () => {
    api.listUsers.mockResolvedValue([
      { id: '1', username: 'admin', site_role: 'admin', status: 'active', has_password: true, display_name: 'Admin' },
    ])
    const w = mount(UserManagementTab, { global: { plugins: [createPinia()] } })
    await w.vm.load()
    await flushPromises()
    expect(api.listUsers).toHaveBeenCalled()
    expect(w.vm.users.length).toBe(1)
    expect(w.vm.users[0].username).toBe('admin')
  })

  it('hydrates bound domains from the initial user list without opening the dialog', async () => {
    api.listUsers.mockResolvedValue([
      {
        id: '2', username: 'alice', site_role: 'member', status: 'active',
        display_name: 'Alice', domains: ['domain_a', 'domain_b'],
        domain_grants: [
          { domain: 'domain_a', domain_role: 'admin' },
          { domain: 'domain_b', domain_role: 'member' },
        ],
      },
    ])

    const w = mount(UserManagementTab, { global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(w.vm.users[0].domains).toEqual(['domain_a', 'domain_b'])
    expect(w.vm.userDomains).toEqual({
      2: [
        { domain: 'domain_a', domain_role: 'admin' },
        { domain: 'domain_b', domain_role: 'member' },
      ],
    })
    expect(api.getUserDomainGrants).not.toHaveBeenCalled()
  })

  it('keeps the newer domain dialog loading until its own request finishes', async () => {
    api.listUsers.mockResolvedValue([
      { id: '1', username: 'alice', site_role: 'member', status: 'active', domains: ['domain_a'] },
      { id: '2', username: 'bob', site_role: 'member', status: 'active', domains: ['domain_b'] },
    ])
    let resolveAlice!: (grants: Array<{ domain: string; domain_role: 'member' | 'admin' }>) => void
    let resolveBob!: (grants: Array<{ domain: string; domain_role: 'member' | 'admin' }>) => void
    api.getUserDomainGrants
      .mockReturnValueOnce(new Promise(resolve => { resolveAlice = resolve }))
      .mockReturnValueOnce(new Promise(resolve => { resolveBob = resolve }))

    const w = mount(UserManagementTab, { global: { plugins: [createPinia()] } })
    await flushPromises()
    const aliceLoad = w.vm.openDomains(w.vm.users[0])
    const bobLoad = w.vm.openDomains(w.vm.users[1])

    resolveAlice([{ domain: 'domain_a', domain_role: 'member' }])
    await aliceLoad
    expect(w.vm.domainsLoading).toBe(true)

    resolveBob([{ domain: 'domain_b', domain_role: 'member' }])
    await bobLoad
    expect(w.vm.domainsLoading).toBe(false)
  })

  it('createUser calls api with form values', async () => {
    api.listUsers.mockResolvedValue([])
    api.createUser.mockResolvedValue({ id: '2', username: 'alice', site_role: 'member', status: 'active' })
    const w = mount(UserManagementTab, { global: { plugins: [createPinia()] } })
    await flushPromises()
    await w.vm.createUser({
      username: 'alice', password: 'alicepw12', site_role: 'member', display_name: 'Alice',
    })
    expect(api.createUser).toHaveBeenCalledWith({
      username: 'alice', password: 'alicepw12', site_role: 'member', display_name: 'Alice',
    })
  })

  it('shows error when load fails', async () => {
    api.listUsers.mockRejectedValue({ response: { status: 500, data: { detail: 'boom' } } })
    const w = mount(UserManagementTab, { global: { plugins: [createPinia()] } })
    await w.vm.load()
    await flushPromises()
    expect(ui.error).toHaveBeenCalled()
  })

  it('loads only the selected domain users in domain-admin mode', async () => {
    api.listDomainUsers.mockResolvedValue([
      { id: '2', username: 'alice', display_name: 'Alice', domain_role: 'member' },
    ])

    const w = mount(UserManagementTab, {
      props: { domainId: 'domain_a' },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    expect(api.listDomainUsers).toHaveBeenCalledWith('domain_a')
    expect(api.listUsers).not.toHaveBeenCalled()
    expect(w.vm.users[0].domain_role).toBe('member')
  })
})
