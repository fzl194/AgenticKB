/**
 * 首页检索摘要的兼容门面。
 *
 * 完整记录页与首页都读取统一 `/api/retrieval-records/summary`。这里保留旧的
 * OpsUsage 形状，避免首页展示迁移牵动知识资产仪表盘；系统状态页已不再使用它。
 */
import { createProxyClient, extractOne } from '@/api/proxyClient'
import type { OpsUsage } from '@/types/ops'
import type { RetrievalRecordsSummary } from '@/types/retrievalRecords'

export function useOpsApi() {
  const client = createProxyClient('mining')

  return {
    async getUsage(
      domain: string,
      options: { days?: number; view?: 'full' | 'dashboard' } = {},
    ): Promise<OpsUsage> {
      const params: Record<string, string | number> = { domain }
      if (options.days !== undefined) params.days = options.days
      const { data } = await client.get('/api/retrieval-records/summary', { params })
      const value = extractOne<RetrievalRecordsSummary>(data)
      return {
        available: value.available,
        days: value.days,
        summary: {
          queries: value.summary.total_calls,
          no_result: value.summary.no_result,
          no_result_rate: value.summary.no_result_rate,
          failed: value.summary.failed,
          failed_rate: value.summary.failure_rate,
          p95_duration_ms: value.summary.p95_duration_ms,
        },
      }
    },
  }
}
