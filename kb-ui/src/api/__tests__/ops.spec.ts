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
    get.mockResolvedValue({ data: {
      available: false, days: 7,
      summary: { total_calls: 0, calls: 0, no_result: 0, failed: 0, no_result_rate: 0, failure_rate: 0, p95_duration_ms: 0, avg_duration_ms: 0, active_paradigms: 0 },
      trend: [], paradigms: [], tools: [], sources: {},
    } })
  })

  it('dashboard uses the unified summary endpoint without an unsupported view parameter', async () => {
    await useOpsApi().getUsage('cloud_core_network', { view: 'dashboard' })
    expect(get).toHaveBeenCalledWith('/api/retrieval-records/summary', {
      params: { domain: 'cloud_core_network' },
    })
  })

  it('maps only the compact dashboard summary contract', async () => {
    const result = await useOpsApi().getUsage('cloud_core_network')
    expect(get).toHaveBeenCalledWith('/api/retrieval-records/summary', {
      params: { domain: 'cloud_core_network' },
    })
    expect(result).toEqual({
      available: false,
      days: 7,
      summary: {
        queries: 0, no_result: 0, no_result_rate: 0,
        failed: 0, failed_rate: 0, p95_duration_ms: 0,
      },
    })
    expect(result).not.toHaveProperty('paradigms')
    expect(result).not.toHaveProperty('trend')
  })
})
