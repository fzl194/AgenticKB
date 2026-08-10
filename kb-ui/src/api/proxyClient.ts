import axios, { type AxiosInstance } from 'axios'
import { useDomainStore } from '@/stores/domain'
import { loadToken } from './tokenStorage'

/**
 * Phase 2：真实登录。前端不再写死 X-KB-User——改由网关 main_control_service/proxy.py
 * 从 JWT 派生，对所有代理请求（mining 与 serving）统一注入。
 *
 * 请求拦截：从 tokenStorage 读 token，加 Authorization: Bearer。
 *
 * 两个对 X-KB-User 的消费方（均由网关注入，前端无需关心）：
 * - mining 的 /api/kb*：mining/kb/auth.current_user 解析为 kb_users.id（会 upsert）。
 * - serving：KbAccessService 用它裁剪请求里的 kbIds 可见性（只读，不 upsert）。
 *
 * 注意：**不在响应拦截里自动登出**。代理请求的 401 可能是下游 mining 的 infra/业务
 * 问题（如 X-Internal-Auth 失配），不该把整个会话核掉。会话有效性由 stores/auth.fetchMe
 * （启动期，/me 返回 401 才 logout）+ 路由守卫把关。
 */
export function installAuthInterceptors(client: AxiosInstance): void {
  if (!client?.interceptors?.request?.use) {
    return
  }
  client.interceptors.request.use((config) => {
    const token = loadToken()
    if (token) {
      config.headers.set('Authorization', `Bearer ${token}`)
    }
    return config
  })
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
