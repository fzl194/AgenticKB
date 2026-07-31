import { beforeEach, describe, expect, it, vi } from 'vitest'
import { shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/mining/workflows' }) }))

import Sidebar from '../Sidebar.vue'

describe('mining Workflow navigation', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('shows the Workflow entry and all registered management pages', () => {
    const wrapper = shallowMount(Sidebar)

    expect(wrapper.text()).toContain('挖掘范式')
    expect(wrapper.text()).toContain('检索范式')
    expect(wrapper.text()).toContain('实体图谱')
    expect(wrapper.text()).toContain('本体版本')
    expect(wrapper.text()).toContain('本体图谱')
  })
})

