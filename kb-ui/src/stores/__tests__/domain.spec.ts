import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  getDomains: vi.fn(),
}))

vi.mock('@/api/controlPlane', () => ({ useControlPlaneApi: () => api }))

import { useDomainStore } from '@/stores/domain'

const domain = (domainId: string, displayName: string) => ({
  domain_id: domainId,
  display_name: displayName,
  enabled: true,
  default_channel: 'prod',
  scenario_pack_ref: domainId,
})

describe('domain store user-scoped lifecycle', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    api.getDomains.mockReset()
  })

  it('ignores a legacy default-domain id and selects the member assigned domain with display metadata', async () => {
    localStorage.setItem('kb-ui-current-domain', 'cloud_core_network')
    api.getDomains.mockResolvedValue([domain('domain_a', 'Domain A')])

    const store = useDomainStore()
    await store.fetchDomains('alice')

    expect(store.currentDomain).toBe('domain_a')
    expect(store.currentDomainInfo?.display_name).toBe('Domain A')
    expect(localStorage.getItem('kb-ui-current-domain:alice')).toBe('domain_a')
  })

  it('reloads domains when the authenticated username changes', async () => {
    api.getDomains
      .mockResolvedValueOnce([domain('domain_a', 'Domain A')])
      .mockResolvedValueOnce([domain('domain_b', 'Domain B')])

    const store = useDomainStore()
    await store.fetchDomains('alice')
    await store.fetchDomains('bob')

    expect(api.getDomains).toHaveBeenCalledTimes(2)
    expect(store.loadedForUser).toBe('bob')
    expect(store.enabledDomains.map(item => item.domain_id)).toEqual(['domain_b'])
    expect(store.currentDomainInfo?.display_name).toBe('Domain B')
  })

  it('keeps a failed load retryable instead of preserving a stale raw domain id', async () => {
    api.getDomains
      .mockRejectedValueOnce(new Error('temporary failure'))
      .mockResolvedValueOnce([domain('domain_a', 'Domain A')])

    const store = useDomainStore()
    store.currentDomain = 'cloud_core_network'

    await store.fetchDomains('alice')
    expect(store.loaded).toBe(false)
    expect(store.loadedForUser).toBe('')
    expect(store.currentDomain).toBe('')

    await store.fetchDomains('alice')
    expect(api.getDomains).toHaveBeenCalledTimes(2)
    expect(store.currentDomainInfo?.display_name).toBe('Domain A')
  })

  it('persists the selected domain independently for each user', async () => {
    api.getDomains
      .mockResolvedValueOnce([
        domain('domain_a', 'Domain A'),
        domain('domain_b', 'Domain B'),
      ])
      .mockResolvedValueOnce([domain('domain_c', 'Domain C')])

    const store = useDomainStore()
    await store.fetchDomains('alice')
    store.switchDomain('domain_b')
    await store.fetchDomains('bob')

    expect(localStorage.getItem('kb-ui-current-domain:alice')).toBe('domain_b')
    expect(localStorage.getItem('kb-ui-current-domain:bob')).toBe('domain_c')
  })

  it('keeps the selection empty when the authenticated user has no enabled domains', async () => {
    localStorage.setItem('kb-ui-current-domain:alice', 'cloud_core_network')
    api.getDomains.mockResolvedValue([])

    const store = useDomainStore()
    await store.fetchDomains('alice')

    expect(store.loaded).toBe(true)
    expect(store.currentDomain).toBe('')
    expect(store.currentDomainInfo).toBeUndefined()
  })

  it('does not let a slow response from the previous user overwrite the current user', async () => {
    let resolveAlice!: (value: ReturnType<typeof domain>[]) => void
    const aliceResponse = new Promise<ReturnType<typeof domain>[]>(resolve => {
      resolveAlice = resolve
    })
    api.getDomains
      .mockReturnValueOnce(aliceResponse)
      .mockResolvedValueOnce([domain('domain_b', 'Domain B')])

    const store = useDomainStore()
    const aliceLoad = store.fetchDomains('alice')
    const bobLoad = store.fetchDomains('bob')
    await bobLoad
    resolveAlice([domain('domain_a', 'Domain A')])
    await aliceLoad

    expect(store.loadedForUser).toBe('bob')
    expect(store.currentDomain).toBe('domain_b')
    expect(store.currentDomainInfo?.display_name).toBe('Domain B')
  })

  it('reuses an in-flight request for the same user', async () => {
    let resolveDomains!: (value: ReturnType<typeof domain>[]) => void
    api.getDomains.mockReturnValue(new Promise(resolve => {
      resolveDomains = resolve
    }))

    const store = useDomainStore()
    const firstLoad = store.fetchDomains('alice')
    const secondLoad = store.fetchDomains('alice')

    expect(api.getDomains).toHaveBeenCalledTimes(1)
    resolveDomains([domain('domain_a', 'Domain A')])
    await Promise.all([firstLoad, secondLoad])
    expect(store.currentDomain).toBe('domain_a')
  })
})
