import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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
import ReleaseVersion from '../ReleaseVersion.vue'
import { useBrandStore } from '@/stores/brand'
import { useDomainStore } from '@/stores/domain'

describe('Sidebar navigation', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    auth.siteRole = 'admin'
  })

  it('keeps admin/exploration pages and hides removed asset/graph entries', () => {
    const wrapper = shallowMount(Sidebar)

    expect(wrapper.findComponent(ReleaseVersion).exists()).toBe(true)

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

  it('member sees the unified retrieval records entry without exposing admin pages', () => {
    auth.siteRole = 'member'
    const wrapper = shallowMount(Sidebar)
    expect(wrapper.text()).toContain('概览')
    expect(wrapper.text()).toContain('知识库')
    expect(wrapper.text()).toContain('检索记录')
    // 61号批次4：知识制品对普通成员开放（viewer 也有运行态视图）
    expect(wrapper.text()).toContain('知识制品')
    expect(wrapper.html()).toContain('/artifacts')
    expect(wrapper.html()).toContain('/retrieval-records')
    expect(wrapper.text()).not.toContain('检索范式')
    expect(wrapper.text()).not.toContain('挖掘范式')
    expect(wrapper.text()).not.toContain('系统设置')
    expect(wrapper.text()).not.toContain('用户管理')
    // 2026-11-17 收紧：制作工作台仅站点/域管理员可见
    expect(wrapper.text()).not.toContain('制作工作台')
    expect(wrapper.html()).not.toContain('/workspaces')
  })

  it('shows standalone user management to the selected domain administrator', () => {
    auth.siteRole = 'member'
    const domains = useDomainStore()
    domains.domains = [{
      domain_id: 'domain_a', display_name: 'Domain A', enabled: true,
      default_channel: 'prod', scenario_pack_ref: 'domain_a',
      domain_role: 'admin', capabilities: ['domain.users.manage', 'domain.kbs.manage'],
    }]
    domains.currentDomain = 'domain_a'

    const wrapper = shallowMount(Sidebar)

    expect(wrapper.text()).toContain('用户管理')
    // 2026-11-17 收紧：域管理员可见制作工作台
    expect(wrapper.text()).toContain('制作工作台')
    expect(wrapper.html()).toContain('/workspaces')
    expect(wrapper.text()).not.toContain('系统设置')
  })

  it('shows standalone user management to the site administrator', () => {
    const wrapper = shallowMount(Sidebar)
    expect(wrapper.text()).toContain('用户管理')
    expect(wrapper.html()).toContain('/users')
    // 2026-11-17 收紧：站点管理员可见制作工作台
    expect(wrapper.text()).toContain('制作工作台')
    expect(wrapper.html()).toContain('/workspaces')
  })

  it('does not present an unmatched raw domain id as a display name', () => {
    const domains = useDomainStore()
    domains.currentDomain = 'cloud_core_network'

    const wrapper = shallowMount(Sidebar)

    expect(wrapper.text()).toContain('未选择知识域')
    expect(wrapper.text()).not.toContain('cloud_core_network')
  })
})

describe('Sidebar language', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  // jsdom localStorage 跨用例持久——防止 en 残留污染文件内后续 zh 断言
  afterEach(() => localStorage.removeItem('kb-ui-lang'))

  it('renders English nav when the global language is en', () => {
    localStorage.setItem('kb-ui-lang', 'en')
    auth.siteRole = 'admin'
    const wrapper = shallowMount(Sidebar)
    expect(wrapper.text()).toContain('Knowledge Bases')
    expect(wrapper.text()).toContain('System Settings')
    expect(wrapper.text()).toContain('No domain selected')
    expect(wrapper.text()).not.toContain('知识库')
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
