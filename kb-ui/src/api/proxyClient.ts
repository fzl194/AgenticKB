import axios from 'axios'
import { useDomainStore } from '@/stores/domain'

/**
 * Phase 1：无登录态，前端写死一个默认 KB 用户。经 main_control_service 代理透传为
 * X-KB-User 头。接真登录时把这里换成「从登录态 store 读」即可，其余代码零改。
 *
 * 两个消费方：
 * - mining 的 /api/kb*：mining/kb/auth.current_user 解析为 kb_users.id（会 upsert）。
 * - serving：KbAccessService 用它裁剪请求里的 kbIds 可见性（只读，不 upsert）。
 *   不带头 = 匿名 = 只能检索 public 知识库，所以 serving 请求必须注入，否则前端选了
 *   private/shared 知识库会拿到 404 kb_not_found。
 */
const DEFAULT_KB_USER = import.meta.env.VITE_KB_DEFAULT_USER || 'admin'

/**
 * 该请求是否需要带上调用者身份。mining 只有 KB 路由读这个头；serving 全部路由注入，
 * 因为它只在请求真的收窄到知识库时才消费，其余情况完全忽略，副作用为零。
 */
function needsKbIdentity(service: string, url: unknown): boolean {
  if (service === 'serving') return true
  return service === 'mining' && typeof url === 'string' && url.startsWith('/api/kb')
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
    if (needsKbIdentity(service, config.url)) {
      // axios 1.x 的 config.headers 是 AxiosHeaders 实例；用 .set() 才会进发送通道
      config.headers.set('X-KB-User', DEFAULT_KB_USER)
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
