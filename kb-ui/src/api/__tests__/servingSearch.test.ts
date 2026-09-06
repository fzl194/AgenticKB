import axios from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createProxyClient } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'

// 瘦身批次4：旧 /api/v1/search wrapper（useServingApi.search）已随固定检索链退役，
// 其 payload 测试一并删除；本文件保留 proxyClient 拦截器行为测试。
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
