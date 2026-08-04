import type { FullTextRef, FullTextResult, HealthStatus, SearchResult } from '@/types'
import { createProxyClient } from '@/api/proxyClient'

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

export function useServingApi() {
  const client = createProxyClient('serving')

  return {
    async getHealth(): Promise<HealthStatus> {
      const { data } = await client.get('/actuator/health')
      return data
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

      const { data } = await client.post('/api/v1/search', payload)
      return data.data ?? data
    },

    /**
     * 取回若干条证据的未压缩原文。
     *
     * kbIds 必须与产生这些 id 的那次检索一致——范围不同，后端会把它们全部报成
     * out_of_scope。所以调用方应把当次检索用的 kbIds 原样传回来，而不是传当前
     * 选择器里的值（用户可能已经改过了）。
     */
    async fetchFullText(refs: FullTextRef[], options?: SearchOptions): Promise<FullTextResult> {
      const payload: Record<string, unknown> = {
        refs,
        domain: options?.domain,
      }
      const kbIds = options?.kbIds?.map(id => id?.trim()).filter((id): id is string => !!id)
      if (kbIds && kbIds.length > 0) payload.kbIds = kbIds

      const { data } = await client.post('/api/v1/segments/fulltext', payload)
      return data.data ?? data
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
