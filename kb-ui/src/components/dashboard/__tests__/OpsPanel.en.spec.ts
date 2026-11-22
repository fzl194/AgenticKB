/**
 * OpsPanel 的 EN 渲染断言（四区英化 Task 5 / 规则 R11）。
 *
 * 挂载代码与同目录 OpsPanel.spec.ts 相同，区别只在语言：全局语言键必须在 pinia 创建
 * **之前**写入——locale store 在实例化时读它。装进插件的 pinia 与活动 pinia 是同一个
 * 实例（否则 t() 读不到切换）。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { shallowMount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import type { Pinia } from 'pinia'
import OpsPanel from '@/components/dashboard/OpsPanel.vue'
import { useLocaleStore } from '@/stores/locale'

const usage = {
  available: true,
  days: 7,
  trend_days: 30,
  summary: {
    queries: 100, no_result: 7, no_result_rate: 0.07,
    failed: 3, failed_rate: 0.03,
    p95_duration_ms: 412, avg_duration_ms: 180, active_paradigms: 2,
  },
  no_result_queries: [{ query_text: '不应出现在首页的原文', count: 12, last_at: null }],
  top_queries: [], paradigms: [], trend: [], intents: {}, channels: {},
}

/** 任何 CJK 字符都算泄漏：EN 态下整块界面不该残留中文。 */
const CJK = /[一-龥]/

let pinia: Pinia

// 顺序铁律：全局键先于 pinia 写入——store 在创建时读它
beforeEach(() => {
  localStorage.setItem('kb-ui-lang', 'en')
  pinia = createPinia()
  setActivePinia(pinia)
})
afterEach(() => localStorage.removeItem('kb-ui-lang'))

describe('dashboard retrieval summary (EN)', () => {
  it('renders the heading and the KPI labels in English', () => {
    const wrapper = shallowMount(OpsPanel, { props: { usage }, global: { plugins: [pinia] } })

    expect(wrapper.find('.ops__title').text()).toBe('Retrieval Overview')
    expect(wrapper.find('.ops__scope').text()).toBe('Last 7 days · Current domain')
    expect(wrapper.find('.ops__head button').text()).toBe('View Retrieval Records →')
    const labels = wrapper.findAllComponents({ name: 'StatsCard' }).map(c => c.props('label'))
    expect(labels).toEqual(['Calls', 'No-result Rate', 'Failure Rate', 'P95 Latency'])
    expect(wrapper.text()).not.toMatch(CJK)
  })

  it('renders the no-result alert in English with the interpolated rate', () => {
    const wrapper = shallowMount(OpsPanel, {
      props: {
        usage: {
          ...usage,
          summary: { ...usage.summary, queries: 200, no_result: 60, no_result_rate: 0.3, failed_rate: 0.01 },
        },
      },
      global: { plugins: [pinia] },
    })

    expect(wrapper.find('.ops__alert').text())
      .toBe('No-result rate is 30.0%; open Retrieval Records to see what knowledge needs to be added.')
  })

  it('renders the failure alert in English with the interpolated rate', () => {
    const wrapper = shallowMount(OpsPanel, {
      props: { usage: { ...usage, summary: { ...usage.summary, failed_rate: 0.1 } } },
      global: { plugins: [pinia] },
    })

    expect(wrapper.find('.ops__alert').text())
      .toBe('Failure rate is 10.0%; open Retrieval Records to locate the failed calls.')
  })

  it('renders the load-failure notice and its retry button in English', () => {
    const wrapper = shallowMount(OpsPanel, {
      props: { usage: null, error: true },
      global: { plugins: [pinia] },
    })
    const notice = wrapper.find('.ops__notice--error')

    expect(notice.text()).toContain('Failed to load retrieval overview')
    expect(notice.text()).toContain('Retry')
    expect(notice.text()).not.toMatch(CJK)
  })

  it('explains in English that empty records start from the new version', () => {
    const wrapper = shallowMount(OpsPanel, {
      props: { usage: { ...usage, available: false } },
      global: { plugins: [pinia] },
    })

    expect(wrapper.find('.ops__notice--info strong').text()).toBe('No new retrieval records yet')
    expect(wrapper.find('.ops__notice--info span').text())
      .toBe('Unified records are produced starting from this release; earlier records are not migrated.')
    expect(wrapper.find('.ops__notice--info').text()).not.toMatch(CJK)
  })

  it('follows a runtime language switch reactively', async () => {
    const wrapper = shallowMount(OpsPanel, { props: { usage }, global: { plugins: [pinia] } })
    expect(wrapper.find('.ops__title').text()).toBe('Retrieval Overview')

    useLocaleStore().setLang('zh')
    await nextTick()
    expect(wrapper.find('.ops__title').text()).toBe('检索概况')
    expect(wrapper.find('.ops__scope').text()).toBe('近 7 天 · 当前知识域')
  })
})
