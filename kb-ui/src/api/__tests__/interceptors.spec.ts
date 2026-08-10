import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import axios from 'axios'

const storage = vi.hoisted(() => ({
  loadToken: vi.fn<() => string | null>(() => null),
}))

vi.mock('@/api/tokenStorage', () => ({
  loadToken: storage.loadToken,
  saveToken: vi.fn(),
  clearToken: vi.fn(),
}))

import { installAuthInterceptors } from '../proxyClient'

function makeClient(adapter: (config: unknown) => Promise<unknown>) {
  const client = axios.create({ adapter: adapter as never })
  installAuthInterceptors(client)
  return client
}

describe('installAuthInterceptors (request)', () => {
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

  it('does NOT auto-logout on 401 (代理 401 是下游问题，不核会话)', async () => {
    storage.loadToken.mockReturnValue('abc')
    const client = makeClient(async (config) => {
      return Promise.reject({ response: { status: 401, data: {} }, config })
    })
    // 只是 reject，不抛导航/清 token 副作用
    await expect(client.get('/secret')).rejects.toMatchObject({ response: { status: 401 } })
  })

  it('skips installation on a mock client without request interceptors', () => {
    const mockClient = { get: vi.fn() } as unknown as Parameters<typeof installAuthInterceptors>[0]
    expect(() => installAuthInterceptors(mockClient)).not.toThrow()
  })
})
