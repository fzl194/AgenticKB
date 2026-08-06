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

  it('fetchMe logs out on failure', async () => {
    api.getMe.mockRejectedValue(new Error('401'))
    const s = useAuthStore()
    s.token = 't'
    await s.fetchMe()
    expect(s.isAuthenticated).toBe(false)
    expect(storage.clearToken).toHaveBeenCalled()
  })

  it('fetchMe no-op without token', async () => {
    const s = useAuthStore()
    await s.fetchMe()
    expect(api.getMe).not.toHaveBeenCalled()
  })

  it('restore loads token from storage', () => {
    storage.loadToken.mockReturnValue('persisted')
    const s = useAuthStore()
    s.restore()
    expect(s.token).toBe('persisted')
  })

  it('siteRole defaults to member when no user', () => {
    const s = useAuthStore()
    expect(s.siteRole).toBe('member')
  })
})
