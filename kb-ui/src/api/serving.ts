import type { HealthStatus, SearchResult } from '@/types'
import type { EvidenceItem, EvidenceResponse } from '@/types/operator'
import { createProxyClient } from '@/api/proxyClient'

export type { EvidenceItem, EvidenceResponse }

export interface SearchOptions {
  domain?: string
  debug?: boolean
  /**
   * 把检索范围收窄到这些知识库。留空 = 检索该域当前生效的 release（原行为）。
   * 身份由 proxyClient 注入的 X-KB-User 头决定：其中任何一个不可见，后端整单返回
   * 404 kb_not_found，而不是静默少给结果。
   */
  kbIds?: string[]
}

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

    async search(query: string, options?: SearchOptions): Promise<SearchResult> {
      const payload: Record<string, unknown> = {
        query,
        domain: options?.domain,
        debug: options?.debug ?? true,
      }
      // 只在真的选了知识库时才带 kbIds：后端把空数组和缺省一视同仁，但省掉这个键能让
      // 「全域检索」的请求体与改动前逐字一致，便于比对回归。
      const kbIds = options?.kbIds?.map(id => id?.trim()).filter((id): id is string => !!id)
      if (kbIds && kbIds.length > 0) payload.kbIds = kbIds

      try {
        const { data } = await client.post('/api/v1/search', payload)
        return data.data ?? data
      } catch (err: unknown) {
        throw localizeSearchError(err)
      }
    },

    /**
     * 下载文档原件。
     *
     * 走 axios 而不是给 `<a href>` 拼一个 URL：身份是 proxyClient 在请求拦截器里注入的
     * X-KB-User 头，浏览器直接发起的导航根本不经过拦截器，会以匿名身份到达后端 —— 私有
     * 知识库的文档就会莫名其妙 404。
     */
    async downloadRawFile(
      documentId: string,
      options?: SearchOptions,
    ): Promise<{ blob: Blob; disposition: string | null }> {
      const params: Record<string, unknown> = { domain: options?.domain }
      const kbIds = options?.kbIds?.map(id => id?.trim()).filter((id): id is string => !!id)
      if (kbIds && kbIds.length > 0) params.kbIds = kbIds

      const response = await client.get(`/api/v1/documents/${documentId}/raw`, {
        params,
        // indexes:null → kbIds=a&kbIds=b。axios 默认发 kbIds[]=a，Spring 的
        // @RequestParam List<String> 不认这种形状，会当成没传，静默退回全域范围。
        paramsSerializer: { indexes: null },
        responseType: 'blob',
      })
      return {
        blob: response.data,
        disposition: response.headers['content-disposition'] ?? null,
      }
    },
  }
}
