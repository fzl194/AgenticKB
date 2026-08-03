import { createProxyClient } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import type {
  OperatorDef, ParadigmGraph, ParadigmView, ParadigmVersionView, RunResult,
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
     * `binding` optionally binds in the same call. It is applied after the publish commits and is
     * not part of its transaction, so a rejected binding leaves the paradigm published-but-unbound
     * and comes back as `bindingError` rather than as a thrown error.
     */
    async publish(
      id: string,
      createdBy?: string,
      binding?: { domain: string; setDefault?: boolean },
    ): Promise<ParadigmVersionView> {
      const { data } = await client.post(`/api/v1/paradigm/${id}/publish`, { createdBy, ...binding })
      return data
    },

    // ---- domain binding (MCP auto-matching) ----
    /**
     * Bind a published paradigm to a domain. With `isDefault`, it becomes the paradigm MCP
     * auto-matches for that domain — replacing whichever one held the slot before.
     */
    async bindParadigm(id: string, domain: string, isDefault: boolean): Promise<ParadigmView> {
      const { data } = await client.put(`/api/v1/paradigm/${id}/binding`, { domain, isDefault })
      return data
    },

    async unbindParadigm(id: string): Promise<ParadigmView> {
      const { data } = await client.delete(`/api/v1/paradigm/${id}/binding`)
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
