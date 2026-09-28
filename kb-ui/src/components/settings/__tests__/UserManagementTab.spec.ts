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
  listDomainUserCandidates: vi.fn(),
  addDomainUser: vi.fn(),
  removeDomainUser: vi.fn(),
  previewUserDeletion: vi.fn(),
  deleteUser: vi.fn(),
  restoreUser: vi.fn(),
}))
const ui = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(),
  prompt: vi.fn(), confirm: vi.fn(),
}))

vi.mock('@/api/auth', () => ({ useAuthApi: () => api }))
vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: async () => '失败',
  installAuthInterceptors: vi.fn(),
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: ui.success, error: ui.error, warning: ui.warning },
  ElMessageBox: { prompt: ui.prompt, confirm: ui.confirm },
}))

import UserManagementTab from '../UserManagementTab.vue'

describe('UserManagementTab', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    ui.prompt.mockRejectedValue('cancel')
    ui.confirm.mockResolvedValue(undefined)
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

  it('ignores a stale domain-user response after switching domains', async () => {
    let resolveA!: (users: Array<{ id: string; username: string; display_name: string; domain_role: 'member' }>) => void
    let resolveB!: (users: Array<{ id: string; username: string; display_name: string; domain_role: 'member' }>) => void
    api.listDomainUsers
      .mockReturnValueOnce(new Promise(resolve => { resolveA = resolve }))
      .mockReturnValueOnce(new Promise(resolve => { resolveB = resolve }))
    const w = mount(UserManagementTab, {
      props: { domainId: 'domain_a' },
      global: { plugins: [createPinia()] },
    })
    await w.setProps({ domainId: 'domain_b' })

    resolveB([{ id: 'b', username: 'bob', display_name: 'Bob', domain_role: 'member' }])
    await flushPromises()
    resolveA([{ id: 'a', username: 'alice', display_name: 'Alice', domain_role: 'member' }])
    await flushPromises()

    expect(w.vm.users.map(user => user.username)).toEqual(['bob'])
    expect(w.vm.users[0].domains).toEqual(['domain_b'])
  })

  it('searches candidates and adds the selected existing user to the current domain', async () => {
    api.listDomainUsers.mockResolvedValue([])
    api.listDomainUserCandidates.mockResolvedValue([
      { id: '2', username: 'alice', display_name: 'Alice', already_in_domain: false },
    ])
    api.addDomainUser.mockResolvedValue({
      id: '2', username: 'alice', display_name: 'Alice', domain_role: 'member',
    })
    const w = mount(UserManagementTab, {
      props: { domainId: 'domain_a' },
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    await w.vm.searchDomainCandidates('ali')
    expect(api.listDomainUserCandidates).toHaveBeenCalledWith('domain_a', 'ali')
    expect(w.vm.domainCandidates[0].username).toBe('alice')

    w.vm.addUsername = 'alice'
    await w.vm.confirmAddDomainUser()
    expect(api.addDomainUser).toHaveBeenCalledWith('domain_a', 'alice')
  })

  it('submits an initial password atomically when promoting a passwordless domain admin', async () => {
    api.listUsers.mockResolvedValue([{
      id: '2', username: 'alice', site_role: 'member', status: 'active',
      has_password: false, display_name: 'Alice', domains: [], domain_grants: [],
    }])
    api.getUserDomainGrants.mockResolvedValue([])
    api.setUserDomainGrants.mockResolvedValue([{ domain: 'domain_a', domain_role: 'admin' }])
    const w = mount(UserManagementTab, { global: { plugins: [createPinia()] } })
    await flushPromises()
    await w.vm.openDomains(w.vm.users[0])
    w.vm.domainsForm.grants = [{ domain: 'domain_a', domain_role: 'admin' }]

    await w.vm.confirmDomains()
    expect(ui.warning).toHaveBeenCalledWith('域管理员初始密码至少 8 位')
    expect(api.setUserDomainGrants).not.toHaveBeenCalled()

    w.vm.initialDomainPassword = 'domainpw123'
    await w.vm.confirmDomains()
    expect(api.setUserDomainGrants).toHaveBeenCalledWith(
      '2', [{ domain: 'domain_a', domain_role: 'admin' }], 'domainpw123',
    )
    expect(w.vm.users[0].has_password).toBe(true)
    await w.vm.openDomains(w.vm.users[0])
    w.vm.domainsForm.grants = [{ domain: 'domain_a', domain_role: 'admin' }]
    expect(w.vm.domainAdminNeedsInitialPassword).toBe(false)
    expect(w.vm.initialDomainPassword).toBe('')
  })

  it('applies a slow grant save to its original user without closing a newer dialog', async () => {
    api.listUsers.mockResolvedValue([
      {
        id: 'a', username: 'alice', site_role: 'member', status: 'active',
        has_password: false, display_name: 'Alice', domains: [], domain_grants: [],
      },
      {
        id: 'b', username: 'bob', site_role: 'member', status: 'active',
        has_password: false, display_name: 'Bob', domains: [], domain_grants: [],
      },
    ])
    api.getUserDomainGrants.mockResolvedValue([])
    let resolveSave!: (grants: Array<{ domain: string; domain_role: 'admin' }>) => void
    api.setUserDomainGrants.mockReturnValue(new Promise(resolve => { resolveSave = resolve }))
    const w = mount(UserManagementTab, { global: { plugins: [createPinia()] } })
    await flushPromises()
    await w.vm.openDomains(w.vm.users[0])
    w.vm.domainsForm.grants = [{ domain: 'domain_a', domain_role: 'admin' }]
    w.vm.initialDomainPassword = 'domainpw123'
    const savingAlice = w.vm.confirmDomains()

    await w.vm.openDomains(w.vm.users[1])
    resolveSave([{ domain: 'domain_a', domain_role: 'admin' }])
    await savingAlice

    expect(w.vm.domainsForm.id).toBe('b')
    expect(w.vm.users.find(user => user.id === 'a')?.has_password).toBe(true)
    expect(w.vm.users.find(user => user.id === 'b')?.has_password).toBe(false)
  })

  it('previews and blocks deletion while the user still owns a knowledge base', async () => {
    api.listUsers.mockResolvedValue([{
      id: '2', username: 'alice', site_role: 'member', status: 'active',
      has_password: false, display_name: 'Alice', domains: ['domain_a'], domain_grants: [],
    }])
    api.previewUserDeletion.mockResolvedValue({
      user: { id: '2', username: 'alice' },
      owned_knowledge_bases: [{ id: 'kb1', domain: 'domain_a', name: '手册', status: 'active' }],
      domain_count: 1,
      kb_member_count: 2,
      mcp_key_count: 3,
    })
    const w = mount(UserManagementTab, { global: { plugins: [createPinia()] } })
    await flushPromises()

    await w.vm.removeUser(w.vm.users[0])

    expect(api.deleteUser).not.toHaveBeenCalled()
    expect(ui.error).toHaveBeenCalledWith(expect.stringContaining('手册'))
  })

  it('deletes after exact-name confirmation and restores the same deleted identity', async () => {
    const deleted = {
      id: '2', username: 'alice', site_role: 'member' as const, status: 'disabled' as const,
      deleted_at: '2026-09-28T00:00:00Z', display_name: 'Alice', domains: [], domain_grants: [],
    }
    api.listUsers.mockResolvedValue([deleted])
    api.previewUserDeletion.mockResolvedValue({
      user: deleted,
      owned_knowledge_bases: [],
      domain_count: 1,
      kb_member_count: 2,
      mcp_key_count: 3,
    })
    api.deleteUser.mockResolvedValue(deleted)
    api.restoreUser.mockResolvedValue({ ...deleted, deleted_at: null })
    ui.prompt.mockResolvedValue({ value: 'alice' })
    const w = mount(UserManagementTab, { global: { plugins: [createPinia()] } })
    w.vm.showDeleted = true
    await w.vm.load()
    await flushPromises()

    await w.vm.removeUser({ ...deleted, deleted_at: null })
    expect(api.deleteUser).toHaveBeenCalledWith('2', 'alice')
    expect(ui.prompt).toHaveBeenCalledWith(
      expect.stringContaining('1 个域绑定、2 个知识库成员关系、3 把 MCP 钥匙'),
      '删除用户',
      expect.any(Object),
    )

    await w.vm.restoreDeletedUser(deleted)
    expect(api.restoreUser).toHaveBeenCalledWith('2')
    expect(api.listUsers).toHaveBeenCalledWith(true)
  })
})
