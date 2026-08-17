import axios from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useServingApi } from '@/api/serving'
import { createProxyClient } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'

type Captured = { url: string; body: Record<string, unknown> }

/** Stub axios.create so we can read what the client would actually POST. */
function stubPost(): { calls: Captured[] } {
  const calls: Captured[] = []
  vi.spyOn(axios, 'create').mockReturnValue({
    interceptors: { request: { use: vi.fn() } },
    get: vi.fn(),
    post: vi.fn(async (url: string, body: Record<string, unknown>) => {
      calls.push({ url, body })
      return { data: { items: [] } }
    }),
  } as never)
  return { calls }
}

describe('serving search payload', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('omits kbIds entirely when nothing is selected', async () => {
    const { calls } = stubPost()

    await useServingApi().search('SMF 配置', { domain: 'cloud_core_network', debug: false })

    expect(calls).toHaveLength(1)
    // Domain-wide requests must stay byte-identical to the pre-kbIds behaviour.
    expect(calls[0].body).toEqual({
      query: 'SMF 配置', domain: 'cloud_core_network', debug: false,
    })
    expect(calls[0].body).not.toHaveProperty('kbIds')
  })

  it('omits kbIds when the selection is empty or blank-only', async () => {
    const { calls } = stubPost()
    const api = useServingApi()

    await api.search('q', { kbIds: [] })
    await api.search('q', { kbIds: ['', '   '] })

    expect(calls[0].body).not.toHaveProperty('kbIds')
    expect(calls[1].body).not.toHaveProperty('kbIds')
  })

  it('sends trimmed kbIds when knowledge bases are selected', async () => {
    const { calls } = stubPost()

    await useServingApi().search('q', { kbIds: [' kb1 ', 'kb2'] })

    expect(calls[0].body.kbIds).toEqual(['kb1', 'kb2'])
  })

  it('defaults debug to true, as before', async () => {
    const { calls } = stubPost()

    await useServingApi().search('q')

    expect(calls[0].body.debug).toBe(true)
  })
})

describe('proxy request interceptors', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  /** Run every request interceptor the client registered, in registration order. */
  function runInterceptors(service: string, url: string) {
    const interceptors: Array<(config: Record<string, unknown>) => Record<string, unknown>> = []
    vi.spyOn(axios, 'create').mockReturnValue({
      interceptors: { request: { use: (fn: never) => { interceptors.push(fn) } } },
    } as never)

    createProxyClient(service)
    const domainStore = useDomainStore()
    domainStore.currentDomain = 'cloud_core_network'

    const headers: Record<string, string> = {}
    let config: Record<string, unknown> = {
      url,
      params: {},
      headers: { set: (key: string, value: string) => { headers[key] = value } },
    }
    for (const fn of interceptors) config = fn(config as never)
    return { headers, config }
  }

  it('injects Authorization Bearer when a token is stored', () => {
    localStorage.setItem('kb-token', 'jwt-abc')
    const { headers } = runInterceptors('serving', '/api/v1/search')
    expect(headers.Authorization).toBe('Bearer jwt-abc')
  })

  it('omits Authorization when no token is stored', () => {
    const { headers } = runInterceptors('serving', '/api/v1/search')
    expect(headers.Authorization).toBeUndefined()
  })

  it('never injects X-KB-User from the frontend — the gateway derives it from the JWT', () => {
    localStorage.setItem('kb-token', 'jwt-abc')
    // Phase 2：X-KB-User 由 main_control_service/proxy.py 从 JWT 派生统一注入，
    // 前端拦截器对任何 service / 路由都不应再写这个头（旧的按 service/路径分支注入已废弃）。
    const cases: Array<[string, string]> = [
      ['serving', '/api/v1/search'],
      ['mining', '/api/kb/abc/documents'],
      ['mining', '/api/runs'],
      ['llm', '/api/v1/tasks'],
    ]
    for (const [service, url] of cases) {
      expect(runInterceptors(service, url).headers['X-KB-User']).toBeUndefined()
    }
  })
})
