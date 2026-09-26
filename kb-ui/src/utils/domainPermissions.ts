import type { DomainInfo } from '@/types'
import type { DomainCapability, SiteRole } from '@/types/auth'

function hasDomainCapability(
  siteRole: SiteRole,
  domain: DomainInfo | undefined,
  capability: DomainCapability,
): boolean {
  if (siteRole === 'admin') return true
  if (!domain?.enabled || domain.domain_role !== 'admin') return false
  return domain.capabilities?.includes(capability) ?? false
}

export function canManageDomainUsers(
  siteRole: SiteRole,
  domain: DomainInfo | undefined,
): boolean {
  return hasDomainCapability(siteRole, domain, 'domain.users.manage')
}

export function canManageDomainKnowledgeBases(
  siteRole: SiteRole,
  domain: DomainInfo | undefined,
): boolean {
  return hasDomainCapability(siteRole, domain, 'domain.kbs.manage')
}
