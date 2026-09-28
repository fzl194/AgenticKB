import { createProxyClient, extractOne } from '@/api/proxyClient'
import type {
  RetrievalRecord, RetrievalRecordFilters, RetrievalRecordsPage, RetrievalRecordsSummary,
  RetrievalSummaryFilters,
} from '@/types/retrievalRecords'

function compactParams(values: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(values).filter(([, value]) => value !== undefined && value !== ''),
  )
}

function serializeFilters(domain: string, filters: RetrievalRecordFilters): Record<string, unknown> {
  return compactParams({
    domain,
    cursor: filters.cursor,
    page_size: filters.pageSize,
    days: filters.days,
    source: filters.source,
    tool_name: filters.toolName,
    operation: filters.operation,
    kb_id: filters.kbId,
    actor_user_id: filters.actorUserId,
    mcp_key_id: filters.mcpKeyId,
    status: filters.status,
    paradigm_id: filters.paradigmId,
  })
}

export function useRetrievalRecordsApi() {
  const client = createProxyClient('mining')

  return {
    async getSummary(domain: string, filters: RetrievalSummaryFilters = {}): Promise<RetrievalRecordsSummary> {
      const { data } = await client.get('/api/retrieval-records/summary', {
        params: serializeFilters(domain, filters),
      })
      return extractOne<RetrievalRecordsSummary>(data)
    },

    async list(domain: string, filters: RetrievalRecordFilters = {}): Promise<RetrievalRecordsPage> {
      const { data } = await client.get('/api/retrieval-records', {
        params: serializeFilters(domain, filters),
      })
      return extractOne<RetrievalRecordsPage>(data)
    },

    async getOne(domain: string, id: string): Promise<RetrievalRecord> {
      const { data } = await client.get(`/api/retrieval-records/${encodeURIComponent(id)}`, {
        params: { domain },
      })
      return extractOne<RetrievalRecord>(data)
    },
  }
}
