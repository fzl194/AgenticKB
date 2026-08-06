import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { load as yamlLoad } from 'js-yaml'

const api = vi.hoisted(() => ({
  getSystemConfigRaw: vi.fn(),
  updateSystemConfigRaw: vi.fn(),
  getSystemConfig: vi.fn(),
}))

vi.mock('@/api/controlPlane', () => ({ useControlPlaneApi: () => api }))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

import BrandAppearanceTab from '../BrandAppearanceTab.vue'

const YAML_IN =
  'site:\n' +
  '  title: 我的库\n' +
  '  name: MyKB\n' +
  '  badge: 智识\n' +
  '  logo_text: MK\n' +
  '  icon: "data:image/svg+xml;base64,e30="\n' +
  'mining_api_base: http://localhost:8901\n' +
  'extra_key: hello\n'

describe('BrandAppearanceTab', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    api.getSystemConfigRaw.mockResolvedValue(YAML_IN)
    api.updateSystemConfigRaw.mockResolvedValue(undefined)
    api.getSystemConfig.mockResolvedValue({})
  })

  it('loads ui.yaml on mount and renders site values into the preview', async () => {
    const wrapper = shallowMount(BrandAppearanceTab)
    await flushPromises()
    expect(api.getSystemConfigRaw).toHaveBeenCalledWith('ui')
    // 预览回显 site 值（title / name / badge）
    expect(wrapper.text()).toContain('我的库')
    expect(wrapper.text()).toContain('MyKB')
    expect(wrapper.text()).toContain('智识')
    // 有 icon → 渲染预览 <img>
    expect(wrapper.findAll('img').length).toBeGreaterThan(0)
  })

  it('save re-serializes ui.yaml preserving non-site keys', async () => {
    const wrapper = shallowMount(BrandAppearanceTab)
    await flushPromises()

    await wrapper.vm.save()
    await flushPromises()

    expect(api.updateSystemConfigRaw).toHaveBeenCalledTimes(1)
    const [name, text] = api.updateSystemConfigRaw.mock.calls[0]
    expect(name).toBe('ui')
    const doc = yamlLoad(text as string) as Record<string, unknown>
    const site = doc.site as Record<string, string>
    expect(site.title).toBe('我的库')
    expect(site.logo_text).toBe('MK')
    // site 以外的键必须保留（未被误删）
    expect(doc.mining_api_base).toBe('http://localhost:8901')
    expect(doc.extra_key).toBe('hello')
  })

  it('shows error toast when load fails', async () => {
    api.getSystemConfigRaw.mockRejectedValue(new Error('down'))
    const { ElMessage } = await import('element-plus')
    shallowMount(BrandAppearanceTab)
    await flushPromises()
    expect(ElMessage.error).toHaveBeenCalled()
  })
})
