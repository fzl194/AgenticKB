import { describe, expect, it } from 'vitest'
import { canManageDomainUsers, canManageDomainKnowledgeBases } from '@/utils/domainPermissions'
import type { DomainInfo } from '@/types'

const domain = (overrides: Partial<DomainInfo> = {}): DomainInfo => ({
  domain_id: 'domain_a',
  display_name: 'Domain A',
  enabled: true,
  default_channel: 'prod',
  scenario_pack_ref: 'domain_a',
  domain_role: 'member',
  capabilities: [],
  ...overrides,
})

describe('domain permissions', () => {
  it('gives a site administrator domain management in every domain', () => {
    expect(canManageDomainUsers('admin', domain())).toBe(true)
    expect(canManageDomainKnowledgeBases('admin', domain())).toBe(true)
  })

  it('uses capabilities returned for the selected domain', () => {
    const managed = domain({
      domain_role: 'admin',
      capabilities: ['domain.users.manage', 'domain.kbs.manage'],
    })
    expect(canManageDomainUsers('member', managed)).toBe(true)
    expect(canManageDomainKnowledgeBases('member', managed)).toBe(true)
  })

  it('fails closed for a member, missing metadata, or a disabled domain', () => {
    expect(canManageDomainUsers('member', domain())).toBe(false)
    expect(canManageDomainUsers('member', undefined)).toBe(false)
    expect(canManageDomainUsers('member', domain({ enabled: false, domain_role: 'admin' }))).toBe(false)
  })
})
