import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({ getReleaseInfo: vi.fn() }))

vi.mock('@/api/controlPlane', () => ({ useControlPlaneApi: () => api }))

import ReleaseVersion from '../ReleaseVersion.vue'

const release = {
  version: '1.0.0',
  released_at: '2026-09-01',
  title: '首个可部署版本',
  changes: ['知识库管理', '挖掘与检索闭环'],
}

const DialogStub = {
  props: ['modelValue', 'title'],
  template: '<section v-if="modelValue" role="dialog"><h2>{{ title }}</h2><slot /></section>',
}

describe('ReleaseVersion', () => {
  beforeEach(() => {
    api.getReleaseInfo.mockReset().mockResolvedValue(release)
  })

  it('does not report a failure while release information is still loading', async () => {
    let resolveRelease!: (value: typeof release) => void
    api.getReleaseInfo.mockReturnValueOnce(
      new Promise(resolve => { resolveRelease = resolve }),
    )
    const wrapper = mount(ReleaseVersion, {
      global: { stubs: { ElDialog: DialogStub } },
    })

    expect(wrapper.get('[data-testid="release-version"]').text()).toContain('版本加载中')
    expect(wrapper.text()).not.toContain('版本信息不可用')

    resolveRelease(release)
    await flushPromises()
    expect(wrapper.get('[data-testid="release-version"]').text()).toContain('v1.0.0')
  })

  it('shows the deployed version at the bottom and opens its release notes', async () => {
    const wrapper = mount(ReleaseVersion, {
      global: { stubs: { ElDialog: DialogStub } },
    })
    await flushPromises()

    const trigger = wrapper.get('[data-testid="release-version"]')
    expect(trigger.text()).toContain('v1.0.0')

    await trigger.trigger('click')

    expect(wrapper.get('[role="dialog"]').text()).toContain('首个可部署版本')
    expect(wrapper.get('[role="dialog"]').text()).toContain('2026-09-01')
    expect(wrapper.get('[role="dialog"]').text()).toContain('挖掘与检索闭环')
  })

  it('fails quietly without breaking sidebar navigation', async () => {
    api.getReleaseInfo.mockRejectedValueOnce(new Error('offline'))
    const wrapper = mount(ReleaseVersion, {
      global: { stubs: { ElDialog: DialogStub } },
    })
    await flushPromises()

    const trigger = wrapper.get('[data-testid="release-version"]')
    expect(trigger.text()).toContain('版本信息不可用')
    expect(trigger.attributes('disabled')).toBeDefined()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })
})
