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

  it('sets the first password in the same request as a domain-admin grant', async () => {
    const grants = [{ domain: 'domain_a', domain_role: 'admin' as const }]
    mining.put.mockResolvedValue({ data: { domain_grants: grants } })

    await useAuthApi().setUserDomainGrants('u1', grants, 'domainpw123')

    expect(mining.put).toHaveBeenCalledWith('/api/kb/admin/users/u1/domain-grants', {
      grants,
      initial_password: 'domainpw123',
    })
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

  it('searches the minimal domain-member candidate directory', async () => {
    const candidates = [{
      id: 'u1', username: 'alice', display_name: 'Alice', already_in_domain: false,
    }]
    mining.get.mockResolvedValue({ data: { domain: 'domain_a', users: candidates } })

    await expect(useAuthApi().listDomainUserCandidates('domain_a', 'ali')).resolves.toEqual(candidates)

    expect(mining.get).toHaveBeenCalledWith('/api/kb/domains/domain_a/user-candidates', {
      params: { q: 'ali', limit: 20 },
    })
  })

  it('supports deletion preview, confirmed deletion, deleted listing, and restore', async () => {
    const preview = {
      user: { id: 'u1', username: 'alice' }, owned_knowledge_bases: [],
      domain_count: 2, kb_member_count: 3, mcp_key_count: 4,
    }
    mining.get
      .mockResolvedValueOnce({ data: [] })
      .mockResolvedValueOnce({ data: preview })
    mining.delete.mockResolvedValue({ data: { id: 'u1', username: 'alice', deleted_at: 'now' } })
    mining.post.mockResolvedValue({ data: { id: 'u1', username: 'alice', deleted_at: null } })
    const api = useAuthApi()

    await api.listUsers(true)
    await expect(api.previewUserDeletion('u1')).resolves.toEqual(preview)
    await api.deleteUser('u1', 'alice')
    await api.restoreUser('u1')

    expect(mining.get).toHaveBeenNthCalledWith(1, '/api/kb/users', {
      params: { include_deleted: true },
    })
    expect(mining.get).toHaveBeenNthCalledWith(2, '/api/kb/admin/users/u1/deletion-preview')
    expect(mining.delete).toHaveBeenCalledWith('/api/kb/admin/users/u1', {
      data: { confirm_username: 'alice' },
    })
    expect(mining.post).toHaveBeenCalledWith('/api/kb/admin/users/u1/restore')
  })

  it('uploads the same selected file for preview and apply', async () => {
    const file = new File(['username\nalice\n'], 'users.csv', { type: 'text/csv' })
    const plan = { new_users: [], bindings: [], errors: [], total_rows: 1, applied: false }
    mining.post.mockResolvedValue({ data: plan })

    await expect(useAuthApi().importUsers(file, true)).resolves.toEqual(plan)
    await expect(useAuthApi().importDomainUsers('domain_a', file, false)).resolves.toEqual(plan)

    expect(mining.post.mock.calls[0]?.[0]).toBe('/api/kb/admin/users/import')
    expect(mining.post.mock.calls[0]?.[2]).toEqual({ params: { dry_run: true } })
    expect(mining.post.mock.calls[1]?.[0]).toBe('/api/kb/domains/domain_a/users/import')
    expect(mining.post.mock.calls[1]?.[2]).toEqual({ params: { dry_run: false } })
  })
})
