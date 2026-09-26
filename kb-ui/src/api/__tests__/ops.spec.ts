import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.hoisted(() => vi.fn())

vi.mock('@/api/proxyClient', () => ({
  createProxyClient: () => ({ get }),
  extractOne: <T>(value: T) => value,
}))

import { useOpsApi } from '@/api/ops'

describe('ops api', () => {
  beforeEach(() => {
    get.mockReset()
    get.mockResolvedValue({ data: { available: false } })
  })

  it('dashboard view is sent explicitly', async () => {
    await useOpsApi().getUsage('cloud_core_network', { view: 'dashboard' })
    expect(get).toHaveBeenCalledWith('/api/ops/usage', {
      params: { domain: 'cloud_core_network', view: 'dashboard' },
    })
  })

  it('default call keeps the full response contract', async () => {
    await useOpsApi().getUsage('cloud_core_network')
    expect(get).toHaveBeenCalledWith('/api/ops/usage', {
      params: { domain: 'cloud_core_network' },
    })
  })
})
