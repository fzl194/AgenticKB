import { beforeEach, describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  domain: { currentDomain: 'plant/a' },
  requestInterceptor: undefined as ((config: Record<string, unknown>) => Record<string, unknown>) | undefined,
  requests: [] as Array<{ method: string; url: string; config: Record<string, unknown>; body?: unknown }>,
}))

vi.mock('@/stores/domain', () => ({ useDomainStore: () => state.domain }))
vi.mock('axios', () => ({
  default: {
    create: () => {
      const dispatch = async (method: string, url: string, body?: unknown, initial: Record<string, unknown> = {}) => {
        const config = state.requestInterceptor?.({ ...initial, url, method }) ?? { ...initial, url, method }
        state.requests.push({ method, url, config, body })
        if (url.endsWith('/catalog')) return { data: { catalog_version: '1', items: [{ type: 'input_ingest' }] } }
        if (url.endsWith('/options')) return { data: { items: [{ id: 'full', current_version: 4 }] } }
        if (method === 'get' && url === '/api/mining-workflows') return { data: { items: [{ id: 'wf' }] } }
        if (url.endsWith('/versions')) return { data: { items: [{ workflow_id: 'wf', version: 1 }] } }
        return { data: { id: 'wf', version: 1 } }
      }
      return {
        interceptors: { request: { use: (fn: typeof state.requestInterceptor) => { state.requestInterceptor = fn } } },
        get: (url: string, config?: Record<string, unknown>) => dispatch('get', url, undefined, config),
        post: (url: string, body?: unknown, config?: Record<string, unknown>) => dispatch('post', url, body, config),
        put: (url: string, body?: unknown, config?: Record<string, unknown>) => dispatch('put', url, body, config),
      }
    },
  },
}))

import { useMiningWorkflowApi } from '@/api/miningWorkflow'

describe('mining workflow API', () => {
  beforeEach(() => {
    state.requests.length = 0
    state.requestInterceptor = undefined
    state.domain.currentDomain = 'plant/a'
  })

  it('uses the active-domain proxy path without attaching a domain query', async () => {
    const api = useMiningWorkflowApi()

    await api.list({ include_archived: true })

    expect(state.requests[0]).toMatchObject({ method: 'get', url: '/api/mining-workflows' })
    expect(state.requests[0].config.baseURL).toBe('/api/control-plane/api/v1/proxy/plant%2Fa/mining')
    expect(state.requests[0].config.params).toEqual({ include_archived: true })
    expect(state.requests[0].config.params).not.toHaveProperty('domain')
  })

  it('exposes catalog, immutable versions, and revision-aware mutations', async () => {
    const api = useMiningWorkflowApi()

    await expect(api.getCatalog()).resolves.toMatchObject({ catalog_version: '1' })
    await expect(api.options()).resolves.toHaveLength(1)
    await api.create({ name: 'custom', template_key: 'hybrid_assets' })
    await api.saveDraft('wf', { graph: { nodes: [], edges: [], output: { nodeId: '', slot: '' } }, expected_revision: 3 })
    await api.validate('wf')
    await api.publish('wf', { expected_revision: 3, release_notes: 'ready' })
    await api.listVersions('wf')
    await api.getVersion('wf', 1)
    await api.restoreDraft('wf', 1, { expected_revision: 3 })
    await api.clone('wf', { name: 'copy', source_version: 1 })
    await api.archive('wf', { updated_by: 'tester' })

    expect(state.requests.map(({ method, url }) => `${method} ${url}`)).toEqual([
      'get /api/mining-operators/catalog',
      'get /api/mining-workflows/options',
      'post /api/mining-workflows',
      'put /api/mining-workflows/wf/draft',
      'post /api/mining-workflows/wf/validate',
      'post /api/mining-workflows/wf/publish',
      'get /api/mining-workflows/wf/versions',
      'get /api/mining-workflows/wf/versions/1',
      'post /api/mining-workflows/wf/versions/1/restore-draft',
      'post /api/mining-workflows/wf/clone',
      'post /api/mining-workflows/wf/archive',
    ])
  })
})
