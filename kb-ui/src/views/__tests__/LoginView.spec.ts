import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

const authApi = vi.hoisted(() => ({
  identify: vi.fn(),
  login: vi.fn(),
}))
const authStore = vi.hoisted(() => ({
  login: vi.fn(async () => { /* sets token in real store; mock no-op */ }),
  isAuthenticated: false,
  siteRole: 'member',
  user: null,
  token: null,
}))

vi.mock('@/api/auth', () => ({ useAuthApi: () => authApi }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => authStore }))
vi.mock('@/stores/brand', () => ({
  useBrandStore: () => ({ title: 'CoreMasterKB', adminContact: '张三 / 工号 12345' }),
}))
vi.mock('@/api/proxyClient', () => ({ apiErrorDetail: async () => '请求失败' }))

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

describe('LoginView two-step', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('empty username → error, no identify call', async () => {
    const w = mountIt()
    await w.vm.onSubmit()
    expect(w.vm.errorMsg).toBeTruthy()
    expect(authApi.identify).not.toHaveBeenCalled()
  })

  it('identify member → calls auth.login (直接进，SSO 口子)', async () => {
    authApi.identify.mockResolvedValue({ mode: 'member' })
    const w = mountIt()
    w.vm.username = 'alice'
    await w.vm.onSubmit()
    await flushPromises()
    expect(authApi.identify).toHaveBeenCalledWith('alice')
    expect(authStore.login).toHaveBeenCalledWith('alice')  // 工号无密码
  })

  it('identify password → 进第二步（显示密码框）', async () => {
    authApi.identify.mockResolvedValue({ mode: 'password' })
    const w = mountIt()
    w.vm.username = 'root'
    await w.vm.onSubmit()
    await flushPromises()
    expect(w.vm.step).toBe('password')
  })

  it('identify not_found → 报错 + 管理员联系方式', async () => {
    authApi.identify.mockResolvedValue({ mode: 'not_found' })
    const w = mountIt()
    w.vm.username = 'ghost'
    await w.vm.onSubmit()
    await flushPromises()
    expect(w.vm.errorMsg).toContain('用户未在系统')
    expect(w.html()).toContain('张三 / 工号 12345')
  })

  it('step password + login → auth.login(user, password)', async () => {
    authApi.identify.mockResolvedValue({ mode: 'password' })
    const w = mountIt()
    w.vm.username = 'root'
    await w.vm.onSubmit()  // 第一步 → password
    await flushPromises()
    w.vm.password = 'adminpw12'
    await w.vm.onSubmit()  // 第二步
    await flushPromises()
    expect(authStore.login).toHaveBeenCalledWith('root', 'adminpw12')
  })
})
