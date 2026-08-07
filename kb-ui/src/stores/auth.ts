import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useAuthApi, loadToken, saveToken, clearToken } from '@/api/auth'
import type { AuthUser, SiteRole } from '@/types/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(null)
  const user = ref<AuthUser | null>(null)
  const siteRole = computed<SiteRole>(() => user.value?.site_role ?? 'member')
  const isAuthenticated = computed(() => !!token.value && !!user.value)

  // 首屏路由守卫 await 这个 promise：vue-router 的初始导航在 app.use(router) 时就触发，
  // 早于 fetchMe 完成；守卫必须等 fetchMe 拿到 user 才能正确判断 isAuthenticated。
  const ready = ref<Promise<void>>(Promise.resolve())

  /** 启动期：恢复 token +（若有 token）拉 profile。必须在 app.use(router) 之前调用，
   * 让初始导航的守卫 await ready。返回该 promise。 */
  function bootstrap(): Promise<void> {
    token.value = loadToken()
    ready.value = token.value ? fetchMe() : Promise.resolve()
    return ready.value
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

  return { token, user, siteRole, isAuthenticated, ready, bootstrap, login, logout, fetchMe }
})
