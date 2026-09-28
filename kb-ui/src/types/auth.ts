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

export interface DomainUserCandidate {
  id: string
  username: string
  display_name: string | null
  already_in_domain: boolean
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

export interface ManagedUser extends AuthUser {
  id: string
  status: 'active' | 'disabled'
  has_password?: boolean
  domains: string[]
  domain_grants?: UserDomainGrant[]
  deleted_at?: string | null
  deleted_by_user_id?: string | null
}

export interface OwnedKnowledgeBase {
  id: string
  domain: string
  name: string
  status: string
}

export interface UserDeletionPreview {
  user: Pick<ManagedUser, 'id' | 'username' | 'display_name' | 'status' | 'site_role'>
  owned_knowledge_bases: OwnedKnowledgeBase[]
  domain_count: number
  kb_member_count: number
  mcp_key_count: number
}

export interface UserImportErrorRow {
  row: number
  username: string
  code: string
}

export interface UserImportPlan {
  new_users: Array<{ username: string; display_name: string | null; domains: string[] }>
  bindings: Array<{ user_id: string; domain: string }>
  errors: UserImportErrorRow[]
  total_rows: number
  applied: boolean
}

export interface LoginResponse {
  token: string
  user: AuthUser
}
