import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.hoisted(() => vi.fn())

vi.mock('@/api/proxyClient', () => ({
  createProxyClient: () => ({ get }),
  extractOne: <T>(value: { data?: T } | T) =>
    (value && typeof value === 'object' && 'data' in value ? value.data : value) as T,
}))

import { useRetrievalRecordsApi } from '@/api/retrievalRecords'

describe('retrieval records API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('loads the unified summary for the selected domain', async () => {
    get.mockResolvedValue({ data: { data: { days: 7, summary: { total: 0 } } } })

    await useRetrievalRecordsApi().getSummary('domain-a', {
      days: 7, kbId: 'kb-1', source: 'mcp', status: 'failed', operation: 'read',
      toolName: 'get_knowledge', actorUserId: 'u-1', paradigmId: 'p-1', mcpKeyId: 'key-1',
    })

    expect(get).toHaveBeenCalledWith('/api/retrieval-records/summary', {
      params: {
        domain: 'domain-a', days: 7, kb_id: 'kb-1', source: 'mcp', status: 'failed',
        operation: 'read', tool_name: 'get_knowledge', actor_user_id: 'u-1',
        paradigm_id: 'p-1', mcp_key_id: 'key-1',
      },
    })
  })

  it('serializes list filters without leaking undefined values', async () => {
    get.mockResolvedValue({ data: { data: { items: [], next_cursor: null, has_more: false, page_size: 25 } } })

    await useRetrievalRecordsApi().list('domain-a', {
      cursor: 'cursor-1', pageSize: 25, days: 30, source: 'mcp', status: 'failed',
      kbId: 'kb-1', mcpKeyId: 'key-1', toolName: 'search_knowledge',
      operation: 'search', actorUserId: 'u-1', paradigmId: 'p-1',
    })

    expect(get).toHaveBeenCalledWith('/api/retrieval-records', {
      params: {
        domain: 'domain-a', cursor: 'cursor-1', page_size: 25, days: 30,
        source: 'mcp', status: 'failed', kb_id: 'kb-1', mcp_key_id: 'key-1',
        tool_name: 'search_knowledge',
        operation: 'search', actor_user_id: 'u-1', paradigm_id: 'p-1',
      },
    })
  })

  it('loads one record detail by encoded id', async () => {
    get.mockResolvedValue({ data: { data: { id: 'call/1' } } })

    await useRetrievalRecordsApi().getOne('domain-a', 'call/1')

    expect(get).toHaveBeenCalledWith('/api/retrieval-records/call%2F1', {
      params: { domain: 'domain-a' },
    })
  })
})
