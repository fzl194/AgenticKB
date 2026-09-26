import { beforeEach, describe, expect, it, vi } from 'vitest'

const mining = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('axios', () => ({
  default: { create: () => ({ get: vi.fn(), post: vi.fn() }) },
}))
vi.mock('@/api/proxyClient', () => ({
  createProxyClient: () => mining,
  extractOne: (value: unknown) => value,
  installAuthInterceptors: vi.fn(),
}))

import { useAuthApi } from '@/api/auth'

describe('domain user-management API contract', () => {
  beforeEach(() => vi.clearAllMocks())

  it('reads and replaces domain grants for a user as site admin', async () => {
    const grants = [{ domain: 'domain_a', domain_role: 'admin' as const }]
    mining.get.mockResolvedValue({ data: { user_id: 'u1', domains: ['domain_a'], domain_grants: grants } })
    mining.put.mockResolvedValue({ data: { user_id: 'u1', domains: ['domain_a'], domain_grants: grants } })

    const api = useAuthApi()
    await expect(api.getUserDomainGrants('u1')).resolves.toEqual(grants)
    await expect(api.setUserDomainGrants('u1', grants)).resolves.toEqual(grants)

    expect(mining.get).toHaveBeenCalledWith('/api/kb/admin/users/u1/domain-grants')
    expect(mining.put).toHaveBeenCalledWith('/api/kb/admin/users/u1/domain-grants', { grants })
  })

  it('lists, adds, and removes ordinary members in one managed domain', async () => {
    const member = {
      id: 'u1', username: 'alice', display_name: 'Alice', domain_role: 'member' as const,
    }
    mining.get.mockResolvedValue({ data: { domain: 'domain_a', users: [member] } })
    mining.post.mockResolvedValue({ data: member })
    mining.delete.mockResolvedValue({ data: { ok: true, user_id: 'u1', domain: 'domain_a' } })

    const api = useAuthApi()
    await expect(api.listDomainUsers('domain_a')).resolves.toEqual([member])
    await expect(api.addDomainUser('domain_a', 'alice')).resolves.toEqual(member)
    await api.removeDomainUser('domain_a', 'u1')

    expect(mining.get).toHaveBeenCalledWith('/api/kb/domains/domain_a/users')
    expect(mining.post).toHaveBeenCalledWith('/api/kb/domains/domain_a/users', { username: 'alice' })
    expect(mining.delete).toHaveBeenCalledWith('/api/kb/domains/domain_a/users/u1')
  })
})
