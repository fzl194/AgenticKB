import { beforeEach, describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  domain: { currentDomain: 'cloud_core_network' },
  requestInterceptor: undefined as ((config: Record<string, unknown>) => Record<string, unknown>) | undefined,
  requests: [] as Array<{ method: string; url: string; config: Record<string, unknown>; body?: unknown }>,
  responses: {} as Record<string, unknown>,
}))

vi.mock('@/stores/domain', () => ({ useDomainStore: () => state.domain }))
vi.mock('axios', () => ({
  default: {
    create: () => {
      const dispatch = async (method: string, url: string, body?: unknown, initial: Record<string, unknown> = {}) => {
        const config = state.requestInterceptor?.({ ...initial, url, method }) ?? { ...initial, url, method }
        state.requests.push({ method, url, config, body })
        const key = `${method} ${url}`
        if (key in state.responses) return { data: state.responses[key] }
        return { data: {} }
      }
      return {
        interceptors: { request: { use: (fn: typeof state.requestInterceptor) => { state.requestInterceptor = fn } } },
        get: (url: string, config?: Record<string, unknown>) => dispatch('get', url, undefined, config),
        post: (url: string, body?: unknown, config?: Record<string, unknown>) => dispatch('post', url, body, config),
        put: (url: string, body?: unknown, config?: Record<string, unknown>) => dispatch('put', url, body, config),
        patch: (url: string, body?: unknown, config?: Record<string, unknown>) => dispatch('patch', url, body, config),
        delete: (url: string, config?: Record<string, unknown>) => dispatch('delete', url, undefined, config),
      }
    },
  },
}))

import { useOnenetApi } from '@/api/onenet'

beforeEach(() => {
  state.requests = []
  state.responses = {}
})

describe('onenet api client', () => {
  it('search posts triples with pagination', async () => {
    state.responses['post /api/onenet/search'] = {
      documents: [{ source_id: 'DOC1', slice_hits: 3, sample_titles: [] }],
      total_documents: 1, page: 2, page_size: 20,
      slice_total_reported: 30, capped: true, slices_pulled: 30,
    }
    const api = useOnenetApi()
    const out = await api.search(
      [{ field: 'source_site', fuzzy: false, content: 'support' },
       { field: 'doc_name', fuzzy: false, content: 'UDG' }], 2)
    expect(out.documents[0].source_id).toBe('DOC1')
    expect(out.capped).toBe(true)
    expect(state.requests[0].body).toEqual({
      conditions: [
        { field: 'source_site', fuzzy: false, content: 'support' },
        { field: 'doc_name', fuzzy: false, content: 'UDG' },
      ],
      page: 2, page_size: 20,
    })
  })

  it('toc posts domain + source_id', async () => {
    state.responses['post /api/onenet/toc'] = { cached: false, source_id: 'DOC1', tree: [] }
    const api = useOnenetApi()
    await api.toc('cloud_core_network', 'DOC1')
    expect(state.requests[0].body).toEqual({ domain: 'cloud_core_network', source_id: 'DOC1' })
  })

  it('startImport sends empty selection object when no subtrees', async () => {
    state.responses['post /api/onenet/imports'] = { id: 'i1', status: 'queued' }
    const api = useOnenetApi()
    await api.startImport({ domain: 'd', source_id: 'DOC1', selection: {} })
    expect(state.requests[0].body).toEqual({ domain: 'd', source_id: 'DOC1', selection: {} })
  })

  it('listImports extracts imports array', async () => {
    state.responses['get /api/onenet/imports'] = { imports: [{ id: 'i1' }] }
    const api = useOnenetApi()
    const rows = await api.listImports('d')
    expect(rows).toHaveLength(1)
  })

  it('refs endpoints hit kb-scoped path', async () => {
    state.responses['get /api/kb/kb1/onenet/refs'] = { refs: [{ document_id: 'd1' }] }
    state.responses['post /api/kb/kb1/onenet/refs'] = { added: ['d1'], skipped: [] }
    const api = useOnenetApi()
    const refs = await api.listRefs('kb1')
    expect(refs[0].document_id).toBe('d1')
    const out = await api.addRefs('kb1', ['d1'])
    expect(out.added).toEqual(['d1'])
  })

  it('removeRefs sends body via delete', async () => {
    state.responses['delete /api/kb/kb1/onenet/refs'] = { removed: ['d1'] }
    const api = useOnenetApi()
    const out = await api.removeRefs('kb1', ['d1'])
    expect(out.removed).toEqual(['d1'])
    const req = state.requests.find((r) => r.method === 'delete')
    expect(req?.config.data).toEqual({ document_ids: ['d1'] })
  })

  it('deleteImport removes source-level import', async () => {
    state.responses['delete /api/onenet/imports/imp1'] = { deleted_documents: ['d1', 'd2'], removed_refs: 3 }
    const api = useOnenetApi()
    const out = await api.deleteImport('imp1')
    expect(out.deleted_documents).toEqual(['d1', 'd2'])
    expect(out.removed_refs).toBe(3)
    const req = state.requests.find((r) => r.url === '/api/onenet/imports/imp1')
    expect(req?.method).toBe('delete')
  })

  it('markdown url + text fetch', async () => {
    state.responses['get /api/kb/kb1/onenet/documents/d1/markdown'] = '# 标题'
    const api = useOnenetApi()
    expect(api.documentMarkdownUrl('kb1', 'd1')).toBe('/api/kb/kb1/onenet/documents/d1/markdown')
    const text = await api.fetchDocumentMarkdown('kb1', 'd1')
    expect(text).toBe('# 标题')
    const req = state.requests.find((r) => r.url.includes('markdown'))
    expect(req?.config.responseType).toBe('text')
  })
})
