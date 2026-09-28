import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  useAuthApi, loadToken, saveToken, clearToken, subscribeTokenChanges,
} from '@/api/auth'
import type { AuthUser, SiteRole } from '@/types/auth'
import { useDomainStore } from '@/stores/domain'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(null)
  const user = ref<AuthUser | null>(null)
  const loggingOut = ref(false)
  const sessionGeneration = ref(0)
  const externalSessionGeneration = ref(0)
  const siteRole = computed<SiteRole>(() => user.value?.site_role ?? 'member')
  const isAuthenticated = computed(() => !!token.value && !!user.value)

  // 首屏路由守卫 await 这个 promise：vue-router 的初始导航在 app.use(router) 时就触发，
  // 早于 fetchMe 完成；守卫必须等 fetchMe 拿到 user 才能正确判断 isAuthenticated。
  const ready = ref<Promise<void>>(Promise.resolve())
  let stopTokenChanges: (() => void) | null = null

  function clearSession({ persist }: { persist: boolean }): void {
    sessionGeneration.value += 1
    loggingOut.value = true
    useDomainStore().resetDomains()
    token.value = null
    user.value = null
    ready.value = Promise.resolve()
    if (persist) clearToken()
  }

  function ensureTokenSync(): void {
    if (stopTokenChanges) return
    stopTokenChanges = subscribeTokenChanges((nextToken) => {
      if (nextToken === token.value) return
      if (!nextToken) {
        clearSession({ persist: false })
        return
      }
      sessionGeneration.value += 1
      loggingOut.value = false
      useDomainStore().resetDomains()
      token.value = nextToken
      user.value = null
      ready.value = fetchMe()
      externalSessionGeneration.value += 1
    })
  }

  /** 启动期：恢复 token +（若有 token）拉 profile。必须在 app.use(router) 之前调用，
   * 让初始导航的守卫 await ready。返回该 promise。 */
  function bootstrap(): Promise<void> {
    ensureTokenSync()
    token.value = loadToken()
    loggingOut.value = false
    ready.value = token.value ? fetchMe() : Promise.resolve()
    return ready.value
  }

  async function login(username: string, password?: string): Promise<void> {
    const generation = ++sessionGeneration.value
    loggingOut.value = false
    const api = useAuthApi()
    const res = await api.login(username, password)
    if (generation !== sessionGeneration.value || loggingOut.value) return
    useDomainStore().resetDomains()
    token.value = res.token
    user.value = res.user
    saveToken(res.token)
  }

  function logout(): void {
    clearSession({ persist: true })
  }

  async function fetchMe(): Promise<void> {
    if (!token.value) return
    const generation = sessionGeneration.value
    const requestedToken = token.value
    try {
      const api = useAuthApi()
      const profile = await api.getMe()
      if (
        generation === sessionGeneration.value
        && requestedToken === token.value
        && !loggingOut.value
      ) {
        user.value = profile
      }
    } catch (e) {
      // 仅当 /me 明确返回 401（token 真无效/过期）才登出；网络抖动等保留 token 下次重试。
      if (
        generation === sessionGeneration.value
        && requestedToken === token.value
        && (e as { response?: { status?: number } })?.response?.status === 401
      ) {
        logout()
      }
    }
  }

  return {
    token, user, siteRole, isAuthenticated, loggingOut, sessionGeneration,
    externalSessionGeneration,
    ready, bootstrap, login, logout, fetchMe,
  }
})
