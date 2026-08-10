import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  listUsers: vi.fn(),
  createUser: vi.fn(),
  resetPassword: vi.fn(),
  updateUser: vi.fn(),
}))
const ui = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }))

vi.mock('@/api/auth', () => ({ useAuthApi: () => api }))
vi.mock('@/api/proxyClient', () => ({ apiErrorDetail: async () => '失败' }))
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
})
