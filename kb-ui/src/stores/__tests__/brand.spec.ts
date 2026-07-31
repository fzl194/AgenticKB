import { describe, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  getSystemConfig: vi.fn(),
}))

vi.mock('@/api/controlPlane', () => ({ useControlPlaneApi: () => api }))

import { DEFAULT_BRAND, resolveIcon, useBrandStore } from '@/stores/brand'

describe('resolveIcon', () => {
  it('returns default favicon for empty / whitespace / nullish', () => {
    expect(resolveIcon('')).toBe('/favicon.svg')
    expect(resolveIcon('   ')).toBe('/favicon.svg')
    expect(resolveIcon(undefined)).toBe('/favicon.svg')
    expect(resolveIcon(null)).toBe('/favicon.svg')
  })

  it('passes data URIs through unchanged', () => {
    const uri = 'data:image/svg+xml;base64,e30='
    expect(resolveIcon(uri)).toBe(uri)
  })

  it('passes http(s) URLs through unchanged', () => {
    expect(resolveIcon('https://example.com/logo.png')).toBe('https://example.com/logo.png')
    expect(resolveIcon('http://example.com/logo.png')).toBe('http://example.com/logo.png')
  })
})

describe('brand store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    document.title = ''
    document.head.querySelectorAll('link[rel="icon"]').forEach((l) => l.remove())
  })

  it('fetchBrand parses site and maps logo_text → logoText', async () => {
    api.getSystemConfig.mockResolvedValue({
      site: {
        title: '我的库',
        name: 'MyKB',
        badge: 'KB',
        logo_text: 'MK',
        icon: 'data:image/png;base64,AA==',
      },
      mining_api_base: 'http://x',
    })
    const store = useBrandStore()
    await store.fetchBrand()
    expect(store.title).toBe('我的库')
    expect(store.name).toBe('MyKB')
    expect(store.badge).toBe('KB')
    expect(store.logoText).toBe('MK')
    expect(store.icon).toBe('data:image/png;base64,AA==')
    expect(store.loaded).toBe(true)
  })

  it('falls back to defaults when site block is missing', async () => {
    api.getSystemConfig.mockResolvedValue({ mining_api_base: 'http://x' })
    const store = useBrandStore()
    await store.fetchBrand()
    expect(store.title).toBe(DEFAULT_BRAND.title)
    expect(store.logoText).toBe(DEFAULT_BRAND.logoText)
    expect(store.icon).toBe(DEFAULT_BRAND.icon)
  })

  it('does not throw and keeps defaults when the API fails', async () => {
    api.getSystemConfig.mockRejectedValue(new Error('main_control down'))
    const store = useBrandStore()
    await expect(store.fetchBrand()).resolves.toBeUndefined()
    expect(store.title).toBe(DEFAULT_BRAND.title)
    expect(store.loaded).toBe(true)
  })

  it('applyBrand sets document.title and favicon href from config', async () => {
    api.getSystemConfig.mockResolvedValue({
      site: { title: 'T', icon: 'data:image/svg+xml;base64,e30=' },
    })
    const store = useBrandStore()
    await store.fetchBrand()
    store.applyBrand()
    expect(document.title).toBe('T')
    const link = document.querySelector('link[rel="icon"]') as HTMLLinkElement
    expect(link).toBeTruthy()
    expect(link.getAttribute('href')).toBe('data:image/svg+xml;base64,e30=')
  })

  it('applyBrand falls back to /favicon.svg when icon is empty', () => {
    const store = useBrandStore()
    store.applyBrand()
    const link = document.querySelector('link[rel="icon"]') as HTMLLinkElement
    expect(link.getAttribute('href')).toBe('/favicon.svg')
  })
})
