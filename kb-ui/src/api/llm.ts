import type { HealthStatus, LlmTaskStats, LlmTask, LlmTaskDetail, LlmCleanupResult } from '@/types'
import { createProxyClient, extractItems } from '@/api/proxyClient'

export function useLlmApi() {
  const client = createProxyClient('llm')

  return {
    async getHealth(): Promise<HealthStatus> {
      const { data } = await client.get('/health')
      return data
    },

    async getStats(params?: { domain?: string; service?: string }): Promise<LlmTaskStats> {
      const { data } = await client.get('/api/v1/stats', { params })
      const resp = data as Record<string, unknown>
      return (resp.data ?? resp) as LlmTaskStats
    },

    /**
     * GET /api/v1/tasks — returns { success, data: { total, page, page_size, items } }
     */
    async getTasks(params?: {
      status?: string
      task_type?: string
      domain?: string
      service?: string
      stage?: string
      page?: number
      page_size?: number
    }): Promise<{ total: number; items: LlmTask[] }> {
      const { data } = await client.get('/api/v1/tasks', { params })
      const resp = data as Record<string, unknown>
      const d = (resp.data ?? resp) as Record<string, unknown>
      return {
        total: (d.total as number) ?? 0,
        items: extractItems<LlmTask>(d),
      }
    },

    async getTask(taskId: string): Promise<LlmTaskDetail> {
      const { data } = await client.get(`/api/v1/tasks/${taskId}`)
      const obj = data as Record<string, unknown>
      return (obj.data ?? obj) as LlmTaskDetail
    },

    async cancelTask(taskId: string): Promise<void> {
      await client.post(`/api/v1/tasks/${taskId}/cancel`)
    },

    async getTemplates(params?: { domain?: string }): Promise<Record<string, unknown>[]> {
      try {
        const { data } = await client.get('/api/v1/templates', { params })
        return Array.isArray(data) ? data : []
      } catch { return [] }
    },

    /**
     * POST /api/v1/admin/cleanup — retention cleanup (dry_run=estimate only).
     * knowledge_domain omitted = all domains.
     * Long-running real deletes: explicit timeout below the proxy's 300s read limit.
     */
    async cleanupTasks(params: {
      retention_days: number
      dry_run: boolean
      knowledge_domain?: string
    }): Promise<LlmCleanupResult> {
      const { data } = await client.post('/api/v1/admin/cleanup', params, { timeout: 290_000 })
      const resp = data as Record<string, unknown>
      return (resp.data ?? resp) as LlmCleanupResult
    },
  }
}
