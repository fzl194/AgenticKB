import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import axios from 'axios'

const storage = vi.hoisted(() => ({
  loadToken: vi.fn<() => string | null>(() => null),
  clearToken: vi.fn(),
}))

vi.mock('@/api/tokenStorage', () => ({
  loadToken: storage.loadToken,
  saveToken: vi.fn(),
  clearToken: storage.clearToken,
}))

import { installAuthInterceptors } from '../proxyClient'

function makeClient(adapter: (config: unknown) => Promise<unknown>) {
  const client = axios.create({ adapter: adapter as never })
  installAuthInterceptors(client)
  return client
}

describe('installAuthInterceptors', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    storage.loadToken.mockReturnValue(null)
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('adds Authorization: Bearer when token present', async () => {
    storage.loadToken.mockReturnValue('abc')
    let captured: unknown = null
    const client = makeClient(async (config) => {
      captured = config
      return { status: 200, data: {}, statusText: 'OK', headers: {}, config }
    })
    await client.get('/x')
    const headers = (captured as { headers: Record<string, string> }).headers
    expect(headers.Authorization).toBe('Bearer abc')
  })

  it('does not add Authorization when no token', async () => {
    let captured: unknown = null
    const client = makeClient(async (config) => {
      captured = config
      return { status: 200, data: {}, statusText: 'OK', headers: {}, config }
    })
    await client.get('/x')
    const headers = (captured as { headers: Record<string, string> }).headers
    expect(headers.Authorization).toBeUndefined()
  })

  it('on 401 clears token', async () => {
    storage.loadToken.mockReturnValue('abc')
    const client = makeClient(async (config) => {
      return Promise.reject({
        response: { status: 401, data: { detail: 'unauthenticated' } },
        config,
      })
    })
    await expect(client.get('/secret')).rejects.toBeTruthy()
    // 安全关键行为：401 必清 token（登出）。重定向是 UX 副作用，不在此断言。
    expect(storage.clearToken).toHaveBeenCalled()
  })

  it('on non-401 error does not clear token', async () => {
    const client = makeClient(async (config) => {
      return Promise.reject({ response: { status: 500, data: {} }, config })
    })
    await expect(client.get('/x')).rejects.toBeTruthy()
    expect(storage.clearToken).not.toHaveBeenCalled()
  })

  it('skips installation on a mock client without interceptors', () => {
    // 部分 axios mock 没有 interceptors —— 不应抛
    const mockClient = { get: vi.fn() } as unknown as Parameters<typeof installAuthInterceptors>[0]
    expect(() => installAuthInterceptors(mockClient)).not.toThrow()
  })
})
