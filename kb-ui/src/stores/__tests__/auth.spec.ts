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
  subscribeTokenChanges: vi.fn(),
}))

vi.mock('@/api/auth', () => ({
  useAuthApi: () => api,
  loadToken: storage.loadToken,
  saveToken: storage.saveToken,
  clearToken: storage.clearToken,
  subscribeTokenChanges: storage.subscribeTokenChanges,
}))

import { useAuthStore } from '@/stores/auth'
import { useDomainStore } from '@/stores/domain'

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    storage.loadToken.mockReturnValue(null)
    storage.subscribeTokenChanges.mockReturnValue(() => undefined)
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
    const domains = useDomainStore()
    await s.login('a', 'p')
    domains.domains = [{
      domain_id: 'domain_a', display_name: 'Domain A', enabled: true,
      default_channel: 'prod', scenario_pack_ref: 'domain_a',
    }]
    domains.currentDomain = 'domain_a'
    domains.loaded = true
    s.logout()
    expect(s.isAuthenticated).toBe(false)
    expect(s.token).toBe(null)
    expect(s.user).toBe(null)
    expect(storage.clearToken).toHaveBeenCalled()
    expect(s.loggingOut).toBe(true)
    expect(domains.domains).toEqual([])
    expect(domains.currentDomain).toBe('')
    expect(domains.loaded).toBe(false)
  })

  it('does not let an in-flight profile response revive a logged-out session', async () => {
    let resolveProfile!: (user: { username: string; display_name: string; site_role: 'member' }) => void
    api.getMe.mockReturnValue(new Promise(resolve => { resolveProfile = resolve }))
    const s = useAuthStore()
    s.token = 'old-token'

    const pending = s.fetchMe()
    s.logout()
    resolveProfile({ username: 'alice', display_name: 'Alice', site_role: 'member' })
    await pending

    expect(s.token).toBe(null)
    expect(s.user).toBe(null)
  })

  it('logs out when another browser tab removes the shared token', async () => {
    let onTokenChange!: (token: string | null) => void
    storage.loadToken.mockReturnValue('persisted')
    storage.subscribeTokenChanges.mockImplementation((listener) => {
      onTokenChange = listener
      return () => undefined
    })
    api.getMe.mockResolvedValue({ username: 'alice', display_name: 'Alice', site_role: 'member' })
    const s = useAuthStore()
    await s.bootstrap()

    onTokenChange(null)

    expect(s.token).toBe(null)
    expect(s.user).toBe(null)
    expect(s.loggingOut).toBe(true)
  })

  it('loads the replacement identity when another tab changes to a new token', async () => {
    let onTokenChange!: (token: string | null) => void
    storage.loadToken.mockReturnValue('alice-token')
    storage.subscribeTokenChanges.mockImplementation((listener) => {
      onTokenChange = listener
      return () => undefined
    })
    api.getMe
      .mockResolvedValueOnce({ username: 'alice', display_name: 'Alice', site_role: 'member' })
      .mockResolvedValueOnce({ username: 'bob', display_name: 'Bob', site_role: 'admin' })
    const s = useAuthStore()
    await s.bootstrap()

    onTokenChange('bob-token')
    await s.ready

    expect(s.token).toBe('bob-token')
    expect(s.user?.username).toBe('bob')
    expect(s.externalSessionGeneration).toBe(1)
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
