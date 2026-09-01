import axios from 'axios'
import { installAuthInterceptors } from './proxyClient'

const baseURL = import.meta.env.VITE_CONTROL_PLANE_API_BASE || '/api/control-plane'
const client = axios.create({ baseURL })
installAuthInterceptors(client)

export function useControlPlaneApi() {
  return {
    async getReleaseInfo(): Promise<ReleaseInfo> {
      const { data } = await client.get('/api/v1/version')
      return data as ReleaseInfo
    },

    // ── System config ──
    async listSystemConfigs(): Promise<string[]> {
      const { data } = await client.get('/api/v1/system')
      return data.items ?? []
    },

    async getSystemConfig(name: string): Promise<Record<string, unknown>> {
      const { data } = await client.get(`/api/v1/system/${name}`)
      return data
    },

    async getSystemConfigRaw(name: string): Promise<string> {
      const { data } = await client.get(`/api/v1/system/${name}/raw`, { responseType: 'text' })
      return typeof data === 'string' ? data : JSON.stringify(data)
    },

    async updateSystemConfigRaw(name: string, yamlText: string): Promise<void> {
      await client.put(`/api/v1/system/${name}/raw`, yamlText, {
        headers: { 'Content-Type': 'text/yaml' },
      })
    },

    // ── Domains ──
    async getDomains(): Promise<ControlPlaneDomainSummary[]> {
      const { data } = await client.get('/api/v1/domains')
      return data.items ?? []
    },

    async getDomain(domainId: string): Promise<ControlPlaneDomainSummary> {
      const { data } = await client.get(`/api/v1/domains/${domainId}`)
      return data
    },

    async getDomainRaw(domainId: string): Promise<string> {
      const { data } = await client.get(`/api/v1/domains/${domainId}/raw`, { responseType: 'text' })
      return typeof data === 'string' ? data : JSON.stringify(data)
    },

    async createDomain(domainId: string, extra?: Record<string, unknown>): Promise<Record<string, unknown>> {
      const { data } = await client.post('/api/v1/domains', { domain_id: domainId, ...extra })
      return data
    },

    async updateDomainRaw(domainId: string, yamlText: string): Promise<void> {
      await client.put(`/api/v1/domains/${domainId}/raw`, yamlText, {
        headers: { 'Content-Type': 'text/yaml' },
      })
    },

    async deleteDomain(domainId: string): Promise<void> {
      await client.delete(`/api/v1/domains/${domainId}`)
    },

    // ── Scenario packs ──
    async getScenarioRaw(domainId: string): Promise<string> {
      const { data } = await client.get(`/api/v1/domains/${domainId}/scenario/raw`, { responseType: 'text' })
      return typeof data === 'string' ? data : JSON.stringify(data)
    },

    async updateScenarioRaw(domainId: string, yamlText: string): Promise<void> {
      await client.put(`/api/v1/domains/${domainId}/scenario/raw`, yamlText, {
        headers: { 'Content-Type': 'text/yaml' },
      })
    },

    // ── Service reload (via existing proxy) ──
    async reloadServiceConfig(domainId: string, serviceName: string): Promise<ServiceReloadResult> {
      const { data } = await client.post(`/api/v1/proxy/${domainId}/${serviceName}/api/v1/admin/reload-config`)
      return data
    },

    // ── Code sync ──
    async codeSync(): Promise<CodeSyncResult> {
      const { data } = await client.post('/api/v1/code-sync')
      return data
    },

    // ── Service logs ──
    async listLogs(): Promise<LogListResult> {
      const { data } = await client.get('/api/v1/logs')
      return data
    },

    async readLog(name: string, params: LogQuery = {}): Promise<LogContent> {
      const { data } = await client.get(`/api/v1/logs/${name}`, { params })
      return data
    },
  }
}

// ── Minimal types (co-located) ──

export interface ControlPlaneDomainSummary {
  domain_id: string
  display_name: string
  enabled: boolean
  default_channel: string
  scenario_pack_ref: string
}

export interface ReleaseInfo {
  version: string
  released_at: string
  title: string
  changes: string[]
}

export interface ServiceReloadResult {
  ok: boolean
  error?: string
  config?: Record<string, unknown>
}

export interface CodeSyncResult {
  ok: boolean
  updated_dirs?: string[]
  file_count?: number
  error?: string
}

export interface LogFileInfo {
  name: string
  size_bytes: number
  modified_at: string | null
  /** 轮转备份份数（mining.log.1 …），当前文件不计入 */
  rotated_count: number
}

export interface LogListResult {
  log_dir: string
  files: LogFileInfo[]
}

export interface LogQuery {
  lines?: number
  /** 关键字，大小写不敏感 */
  q?: string
  /** 级别下限：选 WARNING 会同时包含 ERROR/CRITICAL */
  level?: string
}

export interface LogContent {
  name: string
  lines: string[]
  returned_lines: number
  size_bytes: number
  /** true = 只扫描了文件尾部若干 MB，更早的内容不在结果里 */
  truncated: boolean
  filtered: boolean
}
