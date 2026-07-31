import { beforeEach, describe, expect, it, vi } from 'vitest'
import { shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('vue-router', () => ({
  useRoute: () => ({ path: '/' }),
}))

import Sidebar from '../Sidebar.vue'

describe('Sidebar navigation', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('keeps admin/exploration pages and hides removed asset/graph entries', () => {
    const wrapper = shallowMount(Sidebar)

    // 保留的 admin / 探索面
    expect(wrapper.text()).toContain('检索范式')
    expect(wrapper.text()).toContain('实体图谱')
    expect(wrapper.text()).toContain('本体版本')
    expect(wrapper.text()).toContain('本体图谱')

    // KB 中心化后砍掉的顶层入口（设计 §5.1）
    expect(wrapper.text()).not.toContain('知识资产')
    expect(wrapper.text()).not.toContain('知识图谱')
  })
})
