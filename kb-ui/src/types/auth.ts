export type SiteRole = 'admin' | 'member'

export interface AuthUser {
  username: string
  display_name: string | null
  site_role: SiteRole
}

export interface LoginResponse {
  token: string
  user: AuthUser
}
