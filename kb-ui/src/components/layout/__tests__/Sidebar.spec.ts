import { beforeEach, describe, expect, it, vi } from 'vitest'
import { shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('vue-router', () => ({
  useRoute: () => ({ path: '/' }),
}))

const auth = vi.hoisted(() => ({ siteRole: 'admin' }))
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => auth,
}))

import Sidebar from '../Sidebar.vue'
import { useBrandStore } from '@/stores/brand'

describe('Sidebar navigation', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    auth.siteRole = 'admin'
  })

  it('keeps admin/exploration pages and hides removed asset/graph entries', () => {
    const wrapper = shallowMount(Sidebar)

    // 保留的 admin / 探索面
    expect(wrapper.text()).toContain('检索范式')
    // 批次8：实体/本体研究线下线，产品不展示（25 号 §11.2）
    expect(wrapper.text()).not.toContain('实体图谱')
    expect(wrapper.text()).not.toContain('本体版本')
    expect(wrapper.text()).not.toContain('本体图谱')

    // KB 中心化后砍掉的顶层入口（设计 §5.1）
    expect(wrapper.text()).not.toContain('知识资产')
    expect(wrapper.text()).not.toContain('知识图谱')
  })

  it('member only sees 概览/知识库（批次6：独立检索菜单已下线）', () => {
    auth.siteRole = 'member'
    const wrapper = shallowMount(Sidebar)
    expect(wrapper.text()).toContain('概览')
    expect(wrapper.text()).toContain('知识库')
    // 批次6：检索入口收进知识库详情的「检索」tab——member 侧边栏不再有 /search。
    expect(wrapper.html()).not.toContain('/search')
    expect(wrapper.text()).not.toContain('检索测试')
    expect(wrapper.text()).not.toContain('检索范式')
    expect(wrapper.text()).not.toContain('挖掘范式')
    expect(wrapper.text()).not.toContain('系统设置')
  })
})

describe('Sidebar brand', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders default brand name / badge / logoText', () => {
    const wrapper = shallowMount(Sidebar)
    expect(wrapper.text()).toContain('CoreMaster')
    expect(wrapper.text()).toContain('Knowledge Base')
    expect(wrapper.find('.sidebar__logo-icon').text()).toBe('KB')
    expect(wrapper.find('.sidebar__logo-img').exists()).toBe(false)
  })

  it('renders <img> when brand.icon is set; custom name/badge apply', () => {
    const brand = useBrandStore()
    brand.applyValues({ icon: 'data:image/svg+xml;base64,e30=' })

    const wrapper = shallowMount(Sidebar)
    const img = wrapper.find('.sidebar__logo-img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe('data:image/svg+xml;base64,e30=')
    // 有图标时渐变块里不再写 logoText
    expect(wrapper.find('.sidebar__logo-icon').text()).toBe('')

    brand.applyValues({ name: 'MyKB', badge: '智识库' })
    const wrapper2 = shallowMount(Sidebar)
    expect(wrapper2.text()).toContain('MyKB')
    expect(wrapper2.text()).toContain('智识库')
  })
})
