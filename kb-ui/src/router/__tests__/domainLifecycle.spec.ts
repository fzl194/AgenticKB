import { beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({
  ready: Promise.resolve(),
  isAuthenticated: true,
  siteRole: 'member',
  user: { username: 'alice' } as { username: string } | null,
}))
const domains = vi.hoisted(() => ({
  fetchDomains: vi.fn(),
  currentDomainInfo: undefined as undefined | {
    enabled: boolean
    domain_role: 'member' | 'admin'
    capabilities: string[]
  },
}))

vi.mock('@/stores/auth', () => ({ useAuthStore: () => auth }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => domains }))

import { authAndDomainGuard } from '@/router'

describe('router domain lifecycle', () => {
  beforeEach(() => {
    auth.isAuthenticated = true
    auth.siteRole = 'member'
    auth.user = { username: 'alice' }
    domains.fetchDomains.mockReset()
    domains.currentDomainInfo = undefined
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

  it('loads domain access before allowing a domain administrator into user management', async () => {
    domains.fetchDomains.mockImplementation(async () => {
      domains.currentDomainInfo = {
        enabled: true,
        domain_role: 'admin',
        capabilities: ['domain.users.manage', 'domain.kbs.manage'],
      }
    })

    const result = await authAndDomainGuard({
      meta: {}, name: 'users', fullPath: '/users',
    } as never)

    expect(domains.fetchDomains).toHaveBeenCalledWith('alice')
    expect(result).toBeUndefined()
  })

  it('blocks a regular member from the standalone user-management deep link', async () => {
    domains.currentDomainInfo = {
      enabled: true,
      domain_role: 'member',
      capabilities: [],
    }

    const result = await authAndDomainGuard({
      meta: {}, name: 'users', fullPath: '/users',
    } as never)

    expect(result).toEqual({ name: 'dashboard' })
  })
})
