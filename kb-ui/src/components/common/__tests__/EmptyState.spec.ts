import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import EmptyState from '../EmptyState.vue'
import { useLocaleStore } from '@/stores/locale'

afterEach(() => localStorage.removeItem('kb-ui-lang'))

describe('EmptyState text', () => {
  it('falls back to zh without an active pinia (pure-module t helper)', () => {
    const wrapper = mount(EmptyState)
    expect(wrapper.text()).toContain('暂无数据')
  })

  it('renders the caller-provided text as-is', () => {
    const wrapper = mount(EmptyState, { props: { text: '自定义空态' } })
    expect(wrapper.text()).toContain('自定义空态')
  })

  it('follows the language switch reactively', async () => {
    // 插件与活动 pinia 必须是同一个实例，否则 t() 读不到切换
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(EmptyState, { global: { plugins: [pinia] } })
    expect(wrapper.text()).toContain('暂无数据')
    useLocaleStore().setLang('en')
    await nextTick()
    expect(wrapper.text()).toContain('No data')
    useLocaleStore().setLang('zh')
    await nextTick()
    expect(wrapper.text()).toContain('暂无数据')
  })
})
