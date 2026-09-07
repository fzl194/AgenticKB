import type { HealthStatus } from '@/types'
import type { EvidenceItem, EvidenceLocator, EvidenceResponse } from '@/types/operator'
import { createProxyClient } from '@/api/proxyClient'

export type { EvidenceItem, EvidenceResponse }

export interface ParadigmResolveResult {
  domain: string
  bound: boolean
  paradigmId?: string
  name?: string
  description?: string
  version?: number
  url?: string
  source?: 'library' | 'official' | string
  degraded?: boolean
  degradedFrom?: string
}

export interface ParadigmSearchResult {
  evidenceResponse?: EvidenceResponse
  [k: string]: unknown
}

/**
 * 36号 §九：检索错误中文化。axios 错误按响应体/状态映射为用户可行动的
 * 中文文案；未识别的错误原样上抛（不吞错）。404 只在响应体确实是
 * kb_not_found 语义时才映射——paradigm 路由自身的 404（范式不存在）
 * 不得误报成「未完成挖掘」。
 */
export function localizeSearchError(err: unknown): unknown {
  const e = err as { response?: { status?: number; data?: { message?: string; error?: string } }; message?: string }
  const status = e?.response?.status
  const bodyMsg = e?.response?.data?.message || e?.response?.data?.error || ''
  const isKbNotFound = status === 404
    && /knowledge bases were not found|kb_not_found|no_active_kb_build|no mined content/i.test(String(bodyMsg))
  if (isKbNotFound) {
    return new Error('所选知识库暂不可检索：可能尚未完成挖掘，或全部文档挖掘失败（未生成可检索版本）。请先完成一次成功的挖掘。')
  }
  if (status === 401 || status === 403) {
    return new Error('没有访问所选知识库的权限，请确认知识库可见性或联系管理员。')
  }
  return err
}

/** A1（37/38 号）：ev_ ref 的服务端导航解析结果（/api/v1/evidence/{ref}/source）。 */
export interface EvidenceSourceNavigation {
  document_id: string
  kb_id?: string | null
  file_name?: string | null
  relative_path?: string | null
  section_element_id?: string | null
  section_path?: string | null
  table_ref?: string | null
  row_index?: number | null
  locator?: EvidenceLocator | null
}

export function useServingApi() {
  const client = createProxyClient('serving')

  return {
    async getHealth(): Promise<HealthStatus> {
      const { data } = await client.get('/actuator/health')
      return data
    },

    /**
     * ev_ ref -> 完整原文（2026-09-01）：检索面板把截断证据展开为完整内容。
     * 与 MCP get_content(ev_) 同源（ref 反查带授权），经平台前端通道。
     */
    async getEvidenceFull(
      ref: string, domain: string, kbId?: string, mode?: string,
    ): Promise<EvidenceItem> {
      const params: Record<string, unknown> = { domain }
      if (kbId) params.kbId = kbId
      if (mode) params.mode = mode
      const { data } = await client.get(`/api/v1/evidence/${ref}`, { params })
      return data
    },

    /**
     * A1 来源导航解析（37 号 P0-6）：ev_ ref -> 服务端在当前权限下解析的跳转锚
     * （document_id / 大纲锚 / 表格锚 / locator）。前端据此路由到文档页对应位置，
     * 不持有、不拼接任何内部编号。
     */
    async getEvidenceSource(
      ref: string, domain: string, kbId?: string,
    ): Promise<EvidenceSourceNavigation> {
      const params: Record<string, unknown> = { domain }
      if (kbId) params.kbId = kbId
      const { data } = await client.get(`/api/v1/evidence/${ref}/source`, { params })
      return data
    },

    /**
     * 三层解析：这个库组合该走哪条检索范式（库级 > 官方默认）。
     * 批次6「知识库检索 tab」与 MCP 路由共用同一判定。
     */
    async resolveParadigm(domain: string, kbIds?: string[]): Promise<ParadigmResolveResult> {
      const params: Record<string, unknown> = { domain }
      if (kbIds?.length) params.kbIds = kbIds.join(',')
      const { data } = await client.get('/api/v1/paradigm/resolve', { params })
      return data
    },

    /**
     * 按范式执行检索（批次6：检索唯一入口）。kbIds 只对图内 scope 留空的范式生效
     * ——写死范围的专属范式优先按图执行。身份由 proxyClient 注入的 X-KB-User 决定。
     *
     * 36号 §九：检索失败的用户可见文案在此中文化——「one or more knowledge bases
     * were not found」只在全库尚无任何可检索 Build（或不可见）时出现，用户需要
     * 的是可行动的中文原因，不是英文内部话术。
     */
    async runParadigmSearch(
      paradigmId: string,
      query: string,
      options?: { domain?: string; kbIds?: string[]; debug?: boolean },
    ): Promise<ParadigmSearchResult> {
      const payload: Record<string, unknown> = {
        query,
        domain: options?.domain,
        debug: options?.debug ?? false,
      }
      if (options?.kbIds?.length) payload.kbIds = options.kbIds
      try {
        const { data } = await client.post(`/api/v1/paradigm/${paradigmId}/search`, payload)
        return data
      } catch (err: unknown) {
        throw localizeSearchError(err)
      }
    },
  }
}
