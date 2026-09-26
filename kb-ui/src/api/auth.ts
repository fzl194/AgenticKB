import axios from 'axios'
import { createProxyClient, extractOne, installAuthInterceptors } from './proxyClient'
import type {
  AuthUser,
  DomainAccessSummary,
  DomainUser,
  LoginResponse,
  SiteRole,
  UserDomainGrant,
} from '@/types/auth'

export { loadToken, saveToken, clearToken } from './tokenStorage'

// main_control 直连端点（login/me）—— 必须装拦截器，否则 getMe 不带 token → 401 → fetchMe 登出。
const http = axios.create({ baseURL: '/api/control-plane' })
installAuthInterceptors(http)
const mining = createProxyClient('mining', { includeDomainQuery: false })

export function useAuthApi() {
  return {
    /** login/identify/me 是 main_control 直连端点（不经 domain 代理）。 */
    async login(username: string, password?: string): Promise<LoginResponse> {
      const body: Record<string, string> = { username }
      if (password) body.password = password
      const { data } = await http.post('/api/v1/auth/login', body)
      return data as LoginResponse
    },
    async identify(username: string): Promise<{ mode: 'password' | 'member' | 'not_found'; display_name?: string | null }> {
      const { data } = await http.post('/api/v1/auth/identify', { username })
      return data
    },
    async getMe(): Promise<AuthUser> {
      const { data } = await http.get('/api/v1/auth/me')
      return data as AuthUser
    },
    /** 用户管理走 mining 代理（/api/kb/users）。 */
    async listUsers(): Promise<Array<AuthUser & {
      id: string
      status: string
      has_password?: boolean
      domains: string[]
      domain_grants?: UserDomainGrant[]
    }>> {
      const { data } = await mining.get('/api/kb/users')
      return Array.isArray(data) ? data : (data?.items ?? [])
    },
    async createUser(body: {
      username: string; password?: string; site_role: SiteRole; display_name?: string
    }): Promise<AuthUser> {
      const { data } = await mining.post('/api/kb/users', body)
      return extractOne<AuthUser>(data)
    },
    async updateUser(id: string, body: {
      display_name?: string; site_role?: SiteRole; status?: string
    }): Promise<AuthUser> {
      const { data } = await mining.patch(`/api/kb/users/${id}`, body)
      return extractOne<AuthUser>(data)
    },
    async resetPassword(id: string, password: string): Promise<void> {
      await mining.post(`/api/kb/users/${id}/reset-password`, { password })
    },
    /** 当前登录用户的域角色与能力。 */
    async getMyDomainAccess(): Promise<DomainAccessSummary> {
      const { data } = await mining.get('/api/kb/domain-access/me')
      return data as DomainAccessSummary
    },
    /** 系统管理员按“用户 × 域”分配角色。 */
    async getUserDomainGrants(id: string): Promise<UserDomainGrant[]> {
      const { data } = await mining.get(`/api/kb/admin/users/${encodeURIComponent(id)}/domain-grants`)
      return data?.domain_grants ?? []
    },
    async setUserDomainGrants(id: string, grants: UserDomainGrant[]): Promise<UserDomainGrant[]> {
      const { data } = await mining.put(
        `/api/kb/admin/users/${encodeURIComponent(id)}/domain-grants`,
        { grants },
      )
      return data?.domain_grants ?? []
    },
    /** 域管理员只管理当前域的普通成员；同一接口也允许系统管理员调用。 */
    async listDomainUsers(domain: string): Promise<DomainUser[]> {
      const { data } = await mining.get(`/api/kb/domains/${encodeURIComponent(domain)}/users`)
      return data?.users ?? []
    },
    async addDomainUser(domain: string, username: string): Promise<DomainUser> {
      const { data } = await mining.post(
        `/api/kb/domains/${encodeURIComponent(domain)}/users`,
        { username },
      )
      return extractOne<DomainUser>(data)
    },
    async removeDomainUser(domain: string, userId: string): Promise<void> {
      await mining.delete(
        `/api/kb/domains/${encodeURIComponent(domain)}/users/${encodeURIComponent(userId)}`,
      )
    },
    /** 旧契约兼容：旧调用方仍可只读写 member 域列表。 */
    async getUserDomains(id: string): Promise<string[]> {
      const grants = await this.getUserDomainGrants(id)
      return grants.map(grant => grant.domain)
    },
    async setUserDomains(id: string, domains: string[]): Promise<string[]> {
      const grants = domains.map(domain => ({ domain, domain_role: 'member' as const }))
      const saved = await this.setUserDomainGrants(id, grants)
      return saved.map(grant => grant.domain)
    },
    async changeMyPassword(oldPw: string, newPw: string): Promise<void> {
      await mining.post('/api/kb/users/me/password', { old: oldPw, new: newPw })
    },
  }
}
