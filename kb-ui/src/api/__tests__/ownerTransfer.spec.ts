import { beforeEach, describe, expect, it, vi } from 'vitest'

const mining = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

vi.mock('@/api/proxyClient', () => ({
  createProxyClient: () => mining,
  extractItems: (value: unknown, keys: string[]) => (value as Record<string, unknown>)[keys[0]!],
  extractOne: (value: unknown) => value,
}))

import { useKbApi } from '@/api/kb'

describe('KB owner transfer API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('loads eligible candidates and submits an explicit transfer policy', async () => {
    const users = [{ id: 'u2', username: 'bob', display_name: 'Bob' }]
    mining.get.mockResolvedValue({ data: { users } })
    mining.post.mockResolvedValue({ data: { id: 'kb1', owner_id: 'u2' } })
    const api = useKbApi()

    await expect(api.listOwnerCandidates('kb1', 'bo')).resolves.toEqual(users)
    await api.transferOwner('kb1', { new_owner_id: 'u2', keep_old_as_editor: true })

    expect(mining.get).toHaveBeenCalledWith('/api/kb/kb1/owner-candidates', {
      params: { q: 'bo', limit: 50 },
    })
    expect(mining.post).toHaveBeenCalledWith('/api/kb/kb1/transfer-owner', {
      new_owner_id: 'u2', keep_old_as_editor: true,
    })
  })
})
