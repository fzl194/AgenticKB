import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { App } from 'vue'
import { applyAppLocale, type Language } from '@/locales/element'
import { zh } from '@/locales/zh'
import { en } from '@/locales/en'

const GLOBAL_KEY = 'kb-ui-lang'
const USER_KEY_PREFIX = 'kb-ui-lang:'

function userKey(username: string): string {
  return `${USER_KEY_PREFIX}${username}`
}

function readStoredLang(key: string): Language | null {
  try {
    const value = localStorage.getItem(key)
    return value === 'zh' || value === 'en' ? value : null
  } catch {
    // localStorage 不可用（隐私模式等）——忽略，语言仅存内存（tokenStorage 先例）
    return null
  }
}

function writeStoredLang(key: string, lang: Language): void {
  try {
    localStorage.setItem(key, lang)
  } catch {
    // 同上：静默忽略
  }
}

export const useLocaleStore = defineStore('locale', () => {
  // 初始化完全在 store 内部（spec §3.5）：首个使用方 setup 即得语言，
  // 不依赖 main.ts 预调用——组件测试不跑 main.ts。
  const lang = ref<Language>(readStoredLang(GLOBAL_KEY) ?? 'zh')
  const currentUser = ref('')

  // 脱挂通道重注入用（spec §3.1）：setLang 在点击回调里跑，
  // getCurrentInstance() 为 null，且从 main.ts import app 会经 App.vue 成环，
  // 故由 main.ts 在 app.use(pinia) 后注入 app 引用。
  let app: App | null = null

  function attachApp(instance: App): void {
    app = instance
  }

  const messages = computed<Record<string, string>>(() =>
    lang.value === 'en' ? (en as unknown as Record<string, string>) : zh,
  )

  /** 查字典：当前语言 → zh 兜底 → key 本身（漏译开发期立即可见）。
   * params 支持 {name} 插值（壳层仅 login.adminVerify/login.contact/release.releasedAt 用到）。 */
  function t(key: string, params?: Record<string, string | number>): string {
    const text = messages.value[key] ?? zh[key] ?? key
    if (!params) return text
    return text.replace(/\{(\w+)\}/g, (token, name) =>
      params[name] !== undefined ? String(params[name]) : token,
    )
  }

  function setLang(next: Language): void {
    if (next === lang.value) return
    lang.value = next
    writeStoredLang(GLOBAL_KEY, next)
    if (currentUser.value) writeStoredLang(userKey(currentUser.value), next)
    if (app) applyAppLocale(app, next)
  }

  /** 登录成功/fetchMe 后由 App.vue 的 user watcher 调用（spec §4 时序 step 2）：
   * 该用户分键存在则覆盖当前语言；不存在则保持（不预写）。 */
  function applyUserPreference(username: string): void {
    const name = username.trim()
    currentUser.value = name
    if (!name) return
    const stored = readStoredLang(userKey(name))
    if (stored && stored !== lang.value) {
      lang.value = stored
      if (app) applyAppLocale(app, stored)
    }
  }

  return { lang, t, setLang, applyUserPreference, attachApp }
})
