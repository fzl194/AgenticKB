import axios, { type AxiosInstance } from 'axios'
import { useDomainStore } from '@/stores/domain'
import { loadToken, clearToken } from './tokenStorage'

/**
 * Phase 2：真实登录。前端不再写死 X-KB-User（改由网关从 JWT 注入）。
 * 每个 axios 客户端装两个拦截器：
 *   - 请求拦截：从 tokenStorage 读 token，加 Authorization: Bearer。
 *   - 响应拦截：401 → 清 token + 跳 /login（token 过期/失效统一兜底）。
 * 用 tokenStorage 叶模块而非 auth store，是为了打断 store ↔ api ↔ proxyClient 的循环依赖。
 */
export function installAuthInterceptors(client: AxiosInstance): void {
  // 防御：测试里部分 axios.create() mock 不带 interceptors（只测 API 形状），
  // 生产环境真实 axios 总有 interceptors，照常安装。
  if (!client?.interceptors?.request?.use || !client?.interceptors?.response?.use) {
    return
  }
  client.interceptors.request.use((config) => {
    const token = loadToken()
    if (token) {
      config.headers.set('Authorization', `Bearer ${token}`)
    }
    return config
  })
  client.interceptors.response.use(
    (r) => r,
    (error) => {
      if (error?.response?.status === 401 && typeof window !== 'undefined') {
        clearToken()
        const path = window.location.pathname
        if (!path.startsWith('/login')) {
          window.location.href = `/login?redirect=${encodeURIComponent(path)}`
        }
      }
      return Promise.reject(error)
    },
  )
}

/**
 * Create an axios client that routes requests through the main_control_service
 * reverse proxy. The baseURL is resolved on every request via an interceptor,
 * so domain switching is reflected immediately.
 */
export interface ProxyClientOptions {
  includeDomainQuery?: boolean
}

export function createProxyClient(service: string, options: ProxyClientOptions = {}) {
  const includeDomainQuery = options.includeDomainQuery ?? true
  const client = axios.create()
  installAuthInterceptors(client)
  client.interceptors.request.use((config) => {
    const domainStore = useDomainStore()
    const params = config.params && typeof config.params === 'object'
      ? config.params as Record<string, unknown>
      : {}
    // 显式传入的 domain（如下架/下载等被「钉住」的操作）优先于当前活动域，且同时
    // 决定代理路径与 mining 查询参数——两者必须一致，否则后端按代理路径路由、按
    // 查询参数过滤会指向不同的域。
    const explicitDomain = typeof params.domain === 'string' ? params.domain.trim() : ''
    const requestedDomain = explicitDomain || domainStore.currentDomain
    config.baseURL = `/api/control-plane/api/v1/proxy/${encodeURIComponent(requestedDomain)}/${service}`
    if (service === 'mining' && includeDomainQuery) {
      config.params = { ...params, domain: requestedDomain }
    }
    return config
  })
  return client
}

/**
 * Normalize API response items — handles {items}, {data}, and bare arrays.
 */
export function extractItems<T>(data: unknown, extraKeys: string[] = []): T[] {
  if (Array.isArray(data)) return data
  const obj = data as Record<string, unknown>
  for (const key of ['items', 'data', ...extraKeys]) {
    const val = obj[key]
    if (Array.isArray(val)) return val
  }
  return []
}

/**
 * Unwrap {data: ...} envelope from API response.
 */
export function extractOne<T>(data: unknown): T {
  const obj = data as Record<string, unknown>
  return (obj.data ?? obj) as T
}

function errorDetailFromValue(value: unknown): string | null {
  if (typeof value === 'string') {
    const text = value.trim()
    if (!text) return null
    try {
      return errorDetailFromValue(JSON.parse(text)) ?? text
    } catch {
      return text
    }
  }

  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    if (typeof record.detail === 'string' && record.detail.trim()) return record.detail
    if (typeof record.message === 'string' && record.message.trim()) return record.message
  }

  return null
}

async function readBlob(blob: Blob): Promise<string> {
  if (typeof blob.text === 'function') return blob.text()

  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.addEventListener('load', () => resolve(String(reader.result ?? '')))
    reader.addEventListener('error', () => reject(reader.error))
    reader.readAsText(blob)
  })
}

/**
 * Pull a human-readable error message out of an axios error. Handles JSON
 * envelopes, string bodies, and blob responses (e.g. failed file downloads).
 */
export async function apiErrorDetail(error: unknown): Promise<string> {
  const responseData = error && typeof error === 'object'
    ? (error as { response?: { data?: unknown } }).response?.data
    : undefined

  if (typeof Blob !== 'undefined' && responseData instanceof Blob) {
    try {
      const detail = errorDetailFromValue(await readBlob(responseData))
      if (detail) return detail
    } catch {
      // Fall through to the ordinary error message or stable fallback.
    }
  } else {
    const detail = errorDetailFromValue(responseData)
    if (detail) return detail
  }

  if (error instanceof Error && error.message.trim()) return error.message
  return '请求失败'
}
