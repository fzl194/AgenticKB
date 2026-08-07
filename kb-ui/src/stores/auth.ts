import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useAuthApi, loadToken, saveToken, clearToken } from '@/api/auth'
import type { AuthUser, SiteRole } from '@/types/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(null)
  const user = ref<AuthUser | null>(null)
  const siteRole = computed<SiteRole>(() => user.value?.site_role ?? 'member')
  const isAuthenticated = computed(() => !!token.value && !!user.value)

  /** 从 localStorage 恢复 token（启动期调用，不触发 fetchMe）。 */
  function restore(): void {
    token.value = loadToken()
  }

  async function login(username: string, password: string): Promise<void> {
    const api = useAuthApi()
    const res = await api.login(username, password)
    token.value = res.token
    user.value = res.user
    saveToken(res.token)
  }

  function logout(): void {
    token.value = null
    user.value = null
    clearToken()
  }

  async function fetchMe(): Promise<void> {
    if (!token.value) return
    try {
      const api = useAuthApi()
      user.value = await api.getMe()
    } catch (e) {
      // 仅当 /me 明确返回 401（token 真无效/过期）才登出；网络抖动等保留 token 下次重试。
      if ((e as { response?: { status?: number } })?.response?.status === 401) {
        logout()
      }
    }
  }

  return { token, user, siteRole, isAuthenticated, restore, login, logout, fetchMe }
})
