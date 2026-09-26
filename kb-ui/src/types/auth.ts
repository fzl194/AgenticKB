export type SiteRole = 'admin' | 'member'
export type DomainRole = 'admin' | 'member'
export type DomainCapability = 'domain.users.manage' | 'domain.kbs.manage'

export interface UserDomainGrant {
  domain: string
  domain_role: DomainRole
}

export interface DomainUser {
  id: string
  username: string
  display_name: string | null
  domain_role: DomainRole
}

export interface DomainAccessSummary {
  site_role: SiteRole
  grants: UserDomainGrant[]
  capabilities_by_domain: Record<string, DomainCapability[]>
}

export interface AuthUser {
  username: string
  display_name: string | null
  site_role: SiteRole
}

export interface LoginResponse {
  token: string
  user: AuthUser
}
