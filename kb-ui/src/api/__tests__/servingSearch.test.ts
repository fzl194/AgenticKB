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

describe('X-KB-User injection', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  /** Run the real request interceptor and report the headers it set. */
  function runInterceptor(service: string, url: string) {
    let interceptor: ((c: Record<string, unknown>) => Record<string, unknown>) | undefined
    vi.spyOn(axios, 'create').mockReturnValue({
      interceptors: { request: { use: (fn: never) => { interceptor = fn } } },
    } as never)

    createProxyClient(service)
    const domainStore = useDomainStore()
    domainStore.currentDomain = 'cloud_core_network'

    const headers: Record<string, string> = {}
    const config = { url, headers: { set: (k: string, v: string) => { headers[k] = v } } }
    interceptor?.(config as never)
    return { headers, config: config as unknown as { baseURL?: string } }
  }

  it('injects the identity header on serving requests', () => {
    // Without it every KB-narrowed search would be anonymous and 404 on private KBs.
    const { headers } = runInterceptor('serving', '/api/v1/search')
    expect(headers['X-KB-User']).toBeTruthy()
  })

  it('still injects on mining KB routes', () => {
    const { headers } = runInterceptor('mining', '/api/kb/abc/documents')
    expect(headers['X-KB-User']).toBeTruthy()
  })

  it('does not inject on unrelated mining routes', () => {
    const { headers } = runInterceptor('mining', '/api/runs')
    expect(headers['X-KB-User']).toBeUndefined()
  })

  it('does not inject on control-plane-ish services', () => {
    const { headers } = runInterceptor('llm', '/api/v1/tasks')
    expect(headers['X-KB-User']).toBeUndefined()
  })
})
