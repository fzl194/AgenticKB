import { beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({
  ready: Promise.resolve(),
  isAuthenticated: true,
  siteRole: 'member',
  user: { username: 'alice' } as { username: string } | null,
}))
const domains = vi.hoisted(() => ({ fetchDomains: vi.fn() }))

vi.mock('@/stores/auth', () => ({ useAuthStore: () => auth }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => domains }))

import { authAndDomainGuard } from '@/router'

describe('router domain lifecycle', () => {
  beforeEach(() => {
    auth.isAuthenticated = true
    auth.siteRole = 'member'
    auth.user = { username: 'alice' }
    domains.fetchDomains.mockReset()
  })

  it('passes the current username on every authenticated navigation', async () => {
    const kbRoute = { meta: {}, name: 'kb', fullPath: '/kb' } as never
    const mcpRoute = { meta: {}, name: 'mcp-access', fullPath: '/mcp' } as never

    await authAndDomainGuard(kbRoute)
    auth.user = { username: 'bob' }
    await authAndDomainGuard(mcpRoute)

    expect(domains.fetchDomains).toHaveBeenNthCalledWith(1, 'alice')
    expect(domains.fetchDomains).toHaveBeenNthCalledWith(2, 'bob')
  })
})
