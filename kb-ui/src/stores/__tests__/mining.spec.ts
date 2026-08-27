import { describe, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  getRuns: vi.fn(),
  getRun: vi.fn(),
  getRunStages: vi.fn(),
  getRunDocuments: vi.fn(),
}))

vi.mock('@/api/mining', () => ({ useMiningApi: () => api }))

import { useDomainStore } from '@/stores/domain'
import { useMiningStore } from '@/stores/mining'

describe('mining store run submission', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('silent refresh preserves rows and exposes an error', async () => {
    const domain = useDomainStore()
    domain.currentDomain = 'odn'
    const store = useMiningStore()
    store.runs = [{ id: 'old', status: 'running' } as never]
    api.getRuns.mockRejectedValue(new Error('refresh failed'))

    await store.fetchRuns({ silent: true })

    expect(store.runs[0].id).toBe('old')
    expect(store.loading).toBe(false)
    expect(store.error).toBe('refresh failed')
  })
})
