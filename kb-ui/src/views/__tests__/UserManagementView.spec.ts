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

  it('explains the global administrator scope', () => {
    const wrapper = shallowMount(UserManagementView)
    expect(wrapper.text()).toContain('全局用户管理')
    expect(wrapper.text()).toContain('系统管理员')
  })

  it('explains and binds the selected domain for a domain administrator', () => {
    auth.siteRole = 'member'
    const wrapper = shallowMount(UserManagementView)
    expect(wrapper.text()).toContain('Domain A')
    expect(wrapper.text()).toContain('本域所有知识库')
    expect(wrapper.findComponent({ name: 'UserManagementTab' }).props('domainId')).toBe('domain_a')
  })
})
