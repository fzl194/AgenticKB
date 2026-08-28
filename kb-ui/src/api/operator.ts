import { createProxyClient } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import type {
  McpCatalog, OperatorDef, ParadigmGraph, ParadigmView, ParadigmVersionView, RunResult,
} from '@/types/operator'

/**
 * Client for the agent_serving_java operator subsystem (catalog + paradigm
 * management + execution). Routes through the main_control reverse proxy via
 * createProxyClient('serving'), same as the existing serving search.
 */
export function useOperatorApi() {
  const client = createProxyClient('serving')

  function runBody(query: string, opts?: { channel?: string; debug?: boolean }) {
    const domain = useDomainStore().currentDomain
    return { query, domain, channel: opts?.channel, debug: opts?.debug ?? false }
  }

  return {
    // ---- catalog ----
    async getCatalog(): Promise<OperatorDef[]> {
      const { data } = await client.get('/api/v1/operator/catalog')
      return data.operators ?? []
    },

    /**
     * Which published paradigms agents can actually use, and why the rest cannot.
     *
     * The `hidden` half only comes back because `createProxyClient('serving')` injects
     * `X-KB-User` on every serving request — serving withholds it from anonymous callers since it
     * names knowledge bases.
     */
    async getMcpCatalog(domain?: string): Promise<McpCatalog> {
      const { data } = await client.get('/api/v1/paradigm/mcp-catalog', {
        params: domain ? { domain } : undefined,
      })
      return { paradigms: data.paradigms ?? [], hidden: data.hidden ?? [] }
    },

    // ---- paradigm CRUD ----
    async listParadigms(): Promise<ParadigmView[]> {
      const { data } = await client.get('/api/v1/paradigm')
      return data.paradigms ?? []
    },

    async getParadigm(id: string): Promise<ParadigmView> {
      const { data } = await client.get(`/api/v1/paradigm/${id}`)
      return data
    },

    async createParadigm(payload: { name: string; description?: string; graph?: ParadigmGraph }): Promise<ParadigmView> {
      const { data } = await client.post('/api/v1/paradigm', payload)
      return data
    },

    async updateDraft(id: string, graph: ParadigmGraph): Promise<ParadigmView> {
      const { data } = await client.put(`/api/v1/paradigm/${id}`, { graph })
      return data
    },

    // ---- versions / publish / rollback ----
    async listVersions(id: string): Promise<ParadigmVersionView[]> {
      const { data } = await client.get(`/api/v1/paradigm/${id}/versions`)
      return data.versions ?? []
    },

    async getVersion(id: string, version: number): Promise<ParadigmVersionView> {
      const { data } = await client.get(`/api/v1/paradigm/${id}/versions/${version}`)
      return data
    },

    /**
     * Publish the draft as a new immutable version.
     *
     * 批次6：域绑定退役——发布即全域可用；库级绑定在知识库侧管理。
     */
    async publish(id: string, createdBy?: string): Promise<ParadigmVersionView> {
      const { data } = await client.post(`/api/v1/paradigm/${id}/publish`, { createdBy })
      return data
    },

    async rollback(id: string, version: number): Promise<ParadigmView> {
      const { data } = await client.post(`/api/v1/paradigm/${id}/rollback?version=${version}`, {})
      return data
    },

    async archive(id: string): Promise<ParadigmView> {
      const { data } = await client.post(`/api/v1/paradigm/${id}/archive`, {})
      return data
    },

    async deleteParadigm(id: string): Promise<void> {
      await client.delete(`/api/v1/paradigm/${id}`)
    },

    // ---- validation / execution ----
    async validateDraft(id: string): Promise<RunResult> {
      const { data } = await client.post(`/api/v1/paradigm/${id}/validate`, {})
      return data
    },

    async dryRun(id: string, query: string, opts?: { debug?: boolean }): Promise<RunResult> {
      const { data } = await client.post(`/api/v1/paradigm/${id}/dryrun`, runBody(query, opts))
      return data
    },

    async search(id: string, query: string, opts?: { version?: number; debug?: boolean }): Promise<RunResult> {
      const v = opts?.version != null ? `?version=${opts.version}` : ''
      const { data } = await client.post(`/api/v1/paradigm/${id}/search${v}`, runBody(query, opts))
      return data
    },

    // ---- inline (non-persisted) ----
    async runInline(paradigm: ParadigmGraph, query: string, opts?: { debug?: boolean }): Promise<RunResult> {
      const { data } = await client.post('/api/v1/paradigm/run', { paradigm, ...runBody(query, opts) })
      return data
    },

    async validateInline(paradigm: ParadigmGraph): Promise<RunResult> {
      const { data } = await client.post('/api/v1/paradigm/validate', { paradigm })
      return data
    },
  }
}
