import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

const authStore = vi.hoisted(() => ({
  login: vi.fn(),
  isAuthenticated: false,
  siteRole: 'member',
  user: null,
  token: null,
}))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => authStore,
}))
vi.mock('@/stores/brand', () => ({
  useBrandStore: () => ({ title: 'CoreMasterKB' }),
}))
vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: async () => '用户名或密码错误',
}))

import LoginView from '@/views/LoginView.vue'

function mountIt() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div/>' } },
      { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
    ],
  })
  return mount(LoginView, { global: { plugins: [router] } })
}

describe('LoginView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('renders username + password inputs', () => {
    const w = mountIt()
    expect(w.findAll('input').length).toBeGreaterThanOrEqual(2)
  })

  it('shows error when fields empty', async () => {
    const w = mountIt()
    await w.vm.submit('', '')
    expect(w.vm.errorMsg).toBeTruthy()
    expect(authStore.login).not.toHaveBeenCalled()
  })

  it('submits and surfaces error on failure', async () => {
    authStore.login.mockRejectedValue({ response: { status: 401, data: { detail: 'bad' } } })
    const w = mountIt()
    await w.vm.submit('u', 'wrong')
    await flushPromises()
    expect(authStore.login).toHaveBeenCalledWith('u', 'wrong')
    expect(w.vm.errorMsg).toBeTruthy()
  })
})
