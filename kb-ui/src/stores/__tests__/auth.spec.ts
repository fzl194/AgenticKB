import { describe, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  login: vi.fn(),
  getMe: vi.fn(),
}))
const storage = vi.hoisted(() => ({
  loadToken: vi.fn<() => string | null>(() => null),
  saveToken: vi.fn(),
  clearToken: vi.fn(),
}))

vi.mock('@/api/auth', () => ({
  useAuthApi: () => api,
  loadToken: storage.loadToken,
  saveToken: storage.saveToken,
  clearToken: storage.clearToken,
}))

import { useAuthStore } from '@/stores/auth'

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('login sets token + user and persists', async () => {
    api.login.mockResolvedValue({
      token: 'tok',
      user: { username: 'alice', display_name: 'Alice', site_role: 'admin' },
    })
    const s = useAuthStore()
    await s.login('alice', 'pw')
    expect(s.token).toBe('tok')
    expect(s.siteRole).toBe('admin')
    expect(s.isAuthenticated).toBe(true)
    expect(s.user?.username).toBe('alice')
    expect(storage.saveToken).toHaveBeenCalledWith('tok')
  })

  it('logout clears state + token', async () => {
    api.login.mockResolvedValue({
      token: 't',
      user: { username: 'a', display_name: 'A', site_role: 'member' },
    })
    const s = useAuthStore()
    await s.login('a', 'p')
    s.logout()
    expect(s.isAuthenticated).toBe(false)
    expect(s.token).toBe(null)
    expect(s.user).toBe(null)
    expect(storage.clearToken).toHaveBeenCalled()
  })

  it('fetchMe populates from token', async () => {
    api.getMe.mockResolvedValue({ username: 'bob', display_name: 'Bob', site_role: 'member' })
    const s = useAuthStore()
    s.token = 't'
    await s.fetchMe()
    expect(s.siteRole).toBe('member')
    expect(s.user?.username).toBe('bob')
  })

  it('fetchMe logs out only on 401 (token invalid)', async () => {
    api.getMe.mockRejectedValue({ response: { status: 401 } })
    const s = useAuthStore()
    s.token = 't'
    await s.fetchMe()
    expect(s.isAuthenticated).toBe(false)
    expect(storage.clearToken).toHaveBeenCalled()
  })

  it('fetchMe keeps token on non-401 error (network blip)', async () => {
    api.getMe.mockRejectedValue({ response: { status: 500 } })
    const s = useAuthStore()
    s.token = 't'
    await s.fetchMe()
    expect(s.token).toBe('t') // 保留 token
    expect(storage.clearToken).not.toHaveBeenCalled()
  })

  it('fetchMe no-op without token', async () => {
    const s = useAuthStore()
    await s.fetchMe()
    expect(api.getMe).not.toHaveBeenCalled()
  })

  it('bootstrap restores token and fetches profile when token present', async () => {
    storage.loadToken.mockReturnValue('persisted')
    api.getMe.mockResolvedValue({ username: 'admin', display_name: 'A', site_role: 'admin' })
    const s = useAuthStore()
    await s.bootstrap()
    expect(s.token).toBe('persisted')
    expect(s.user?.username).toBe('admin')
  })

  it('bootstrap without token does not call getMe', async () => {
    storage.loadToken.mockReturnValue(null)
    const s = useAuthStore()
    await s.bootstrap()
    expect(s.token).toBe(null)
    expect(api.getMe).not.toHaveBeenCalled()
  })

  it('siteRole defaults to member when no user', () => {
    const s = useAuthStore()
    expect(s.siteRole).toBe('member')
  })
})
