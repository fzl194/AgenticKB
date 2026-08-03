import type { HealthStatus, SearchResult } from '@/types'
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
  }
}
