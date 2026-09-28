import { beforeEach, describe, expect, it, vi } from 'vitest'

const mining = vi.hoisted(() => ({ delete: vi.fn() }))

vi.mock('@/api/proxyClient', () => ({
  createProxyClient: () => mining,
  extractItems: (value: unknown) => value,
  extractOne: (value: unknown) => value,
}))

import { useKbApi } from '@/api/kb'

describe('MCP key deletion API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('deletes one revoked key record through the owner-scoped endpoint', async () => {
    mining.delete.mockResolvedValue({ data: undefined })

    await useKbApi().deleteMcpKey('key-1')

    expect(mining.delete).toHaveBeenCalledWith('/api/kb/users/me/mcp-keys/key-1')
  })
})
