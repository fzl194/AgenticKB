import { beforeEach, describe, expect, it, vi } from 'vitest'
import { shallowMount } from '@vue/test-utils'

const auth = vi.hoisted(() => ({ siteRole: 'admin' as 'admin' | 'member' }))
const domains = vi.hoisted(() => ({
  currentDomain: 'domain_a',
  currentDomainInfo: {
    domain_id: 'domain_a', display_name: 'Domain A', enabled: true,
    domain_role: 'admin', capabilities: ['domain.users.manage', 'domain.kbs.manage'],
  },
}))

vi.mock('@/stores/auth', () => ({ useAuthStore: () => auth }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => domains }))

import UserManagementView from '@/views/UserManagementView.vue'

describe('UserManagementView', () => {
  beforeEach(() => {
    auth.siteRole = 'admin'
    domains.currentDomain = 'domain_a'
  })

  it('explains the global administrator scope without an in-page title', () => {
    const wrapper = shallowMount(UserManagementView)
    expect(wrapper.text()).toContain('系统管理员')
    expect(wrapper.text()).toContain('分配不同知识域中的普通用户或域管理员权限')
    // 页面标题只由顶栏 Header 渲染，页内不再重复
    expect(wrapper.find('h3').exists()).toBe(false)
  })

  it('explains and binds the selected domain for a domain administrator', () => {
    auth.siteRole = 'member'
    const wrapper = shallowMount(UserManagementView)
    expect(wrapper.text()).toContain('域管理员')
    expect(wrapper.text()).toContain('本域所有知识库')
    expect(wrapper.find('h3').exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'UserManagementTab' }).props('domainId')).toBe('domain_a')
  })
})
