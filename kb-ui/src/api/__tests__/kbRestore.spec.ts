import { describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ post: vi.fn() }))
vi.mock('@/api/proxyClient', () => ({
  createProxyClient: () => client,
  extractItems: vi.fn(),
  extractOne: (value: { data?: unknown } | unknown) =>
    value && typeof value === 'object' && 'data' in value ? value.data : value,
}))

import { useKbApi } from '@/api/kb'

describe('KB restore API', () => {
  it('restores a lifecycle-manageable KB', async () => {
    client.post.mockResolvedValue({ data: { id: 'kb-1', status: 'active' } })

    await useKbApi().restoreKb('kb-1')

    expect(client.post).toHaveBeenCalledWith('/api/kb/kb-1/restore')
  })
})
