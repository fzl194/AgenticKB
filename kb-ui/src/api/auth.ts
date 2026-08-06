import axios from 'axios'
import { createProxyClient, extractOne } from './proxyClient'
import type { AuthUser, LoginResponse, SiteRole } from '@/types/auth'

export { loadToken, saveToken, clearToken } from './tokenStorage'

const mining = createProxyClient('mining', { includeDomainQuery: false })

export function useAuthApi() {
  return {
    /** login/me 是 main_control 直连端点（不经 domain 代理）。 */
    async login(username: string, password: string): Promise<LoginResponse> {
      const { data } = await axios.post('/api/control-plane/api/v1/auth/login', { username, password })
      return data as LoginResponse
    },
    async getMe(): Promise<AuthUser> {
      const { data } = await axios.get('/api/control-plane/api/v1/auth/me')
      return data as AuthUser
    },
    /** 用户管理走 mining 代理（/api/kb/users）。 */
    async listUsers(): Promise<Array<AuthUser & { id: string; status: string; has_password?: boolean }>> {
      const { data } = await mining.get('/api/kb/users')
      return Array.isArray(data) ? data : (data?.items ?? [])
    },
    async createUser(body: {
      username: string; password: string; site_role: SiteRole; display_name?: string
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
    async changeMyPassword(oldPw: string, newPw: string): Promise<void> {
      await mining.post('/api/kb/users/me/password', { old: oldPw, new: newPw })
    },
  }
}
