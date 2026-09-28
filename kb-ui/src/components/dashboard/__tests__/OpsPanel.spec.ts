import { describe, expect, it } from 'vitest'
import { shallowMount } from '@vue/test-utils'
import OpsPanel from '@/components/dashboard/OpsPanel.vue'

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

describe('dashboard retrieval summary', () => {
  it('keeps only KPI and one actionable alert', () => {
    const wrapper = shallowMount(OpsPanel, { props: { usage } })
    const labels = wrapper.findAllComponents({ name: 'StatsCard' }).map(c => c.props('label'))

    expect(labels).toEqual(['调用量', '零结果率', '失败率', 'P95 延迟'])
    expect(wrapper.text()).not.toContain('不应出现在首页的原文')
    expect(wrapper.findComponent({ name: 'LineChart' }).exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'BarChart' }).exists()).toBe(false)
  })

  it('explains that empty records start from the new version', () => {
    const wrapper = shallowMount(OpsPanel, {
      props: { usage: { ...usage, available: false } },
    })

    expect(wrapper.text()).toContain('本版本上线后')
    expect(wrapper.text()).not.toContain('serving_query_logs')
  })
  it('keeps the existing ten-percent failure alert boundary', () => {
    const below = shallowMount(OpsPanel, {
      props: { usage: { ...usage, summary: { ...usage.summary, failed_rate: 0.099 } } },
    })
    const atThreshold = shallowMount(OpsPanel, {
      props: { usage: { ...usage, summary: { ...usage.summary, failed_rate: 0.1 } } },
    })

    expect(below.text()).not.toContain('建议打开检索记录定位失败调用')
    expect(atThreshold.text()).toContain('建议打开检索记录定位失败调用')
  })
})