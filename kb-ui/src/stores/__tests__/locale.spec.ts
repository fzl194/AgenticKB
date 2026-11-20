import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// applyAppLocale 是 element-plus 内部 API 的薄封装——单测 mock 掉，
// 真实行为由 src/locales/__tests__/element.spec.ts spike 兜底。
const element = vi.hoisted(() => ({ applyAppLocale: vi.fn() }))
vi.mock('@/locales/element', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/locales/element')>()),
  applyAppLocale: element.applyAppLocale,
}))

import { useLocaleStore } from '@/stores/locale'

/** 断言 applyAppLocale 调用的用例必须先注入 app 引用（store 内 app 默认 null 时
 * 调用被跳过——生产由 main.ts attachApp 注入）。 */
function dummyApp() {
  return createApp({ render: () => null })
}

describe('locale store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    // jsdom localStorage 跨用例持久——per-user 隔离用例需清干净 kb-ui-lang*
    localStorage.clear()
    element.applyAppLocale.mockClear()
  })

  it('defaults to zh when no stored preference', () => {
    const store = useLocaleStore()
    expect(store.lang).toBe('zh')
  })

  it('falls back to zh on an invalid stored value', () => {
    localStorage.setItem('kb-ui-lang', 'fr')
    expect(useLocaleStore().lang).toBe('zh')
  })

  it('initializes from the global key before login', () => {
    localStorage.setItem('kb-ui-lang', 'en')
    expect(useLocaleStore().lang).toBe('en')
  })

  it('setLang writes the global key and calls applyAppLocale', () => {
    const store = useLocaleStore()
    store.attachApp(dummyApp())
    store.setLang('en')
    expect(store.lang).toBe('en')
    expect(localStorage.getItem('kb-ui-lang')).toBe('en')
    expect(element.applyAppLocale).toHaveBeenCalledTimes(1)
  })

  it('setLang is a no-op for the same language', () => {
    const store = useLocaleStore()
    store.setLang('zh')
    expect(element.applyAppLocale).not.toHaveBeenCalled()
  })

  it('setLang writes the per-user key once a user is known', () => {
    const store = useLocaleStore()
    store.applyUserPreference('alice')
    store.setLang('en')
    expect(localStorage.getItem('kb-ui-lang:alice')).toBe('en')
    expect(localStorage.getItem('kb-ui-lang')).toBe('en')
  })

  it('per-user preferences are isolated', () => {
    const store = useLocaleStore()
    store.applyUserPreference('alice')
    store.setLang('en')

    // 同一 pinia 下 useLocaleStore() 返回同一实例：这里验证的是
    // 换用户后 bob 的分键覆盖 alice 留下的 'en'，且 alice 的键不被改写。
    const other = useLocaleStore()
    localStorage.setItem('kb-ui-lang:bob', 'zh')
    other.applyUserPreference('bob')
    expect(other.lang).toBe('zh')
    expect(localStorage.getItem('kb-ui-lang:alice')).toBe('en')
  })

  it('applyUserPreference overrides when the user key exists', () => {
    localStorage.setItem('kb-ui-lang:alice', 'en')
    const store = useLocaleStore()
    store.attachApp(dummyApp())
    store.applyUserPreference('alice')
    expect(store.lang).toBe('en')
    expect(element.applyAppLocale).toHaveBeenCalledTimes(1)
  })

  it('applyUserPreference keeps the current language when the user has no key', () => {
    localStorage.setItem('kb-ui-lang', 'en')
    const store = useLocaleStore()
    store.applyUserPreference('carol')
    expect(store.lang).toBe('en')
    expect(localStorage.getItem('kb-ui-lang:carol')).toBeNull()
    expect(element.applyAppLocale).not.toHaveBeenCalled()
  })

  it('t() falls back current dict → zh → key', () => {
    localStorage.setItem('kb-ui-lang', 'en')
    const store = useLocaleStore()
    expect(store.t('nav.kb')).toBe('Knowledge Bases')
    // en 字典缺 key（运行时构造）→ 回落 zh；再缺 → 回落 key 本身
    expect(store.t('nonexistent.key')).toBe('nonexistent.key')
  })

  it('t() defaults to zh dict and supports {param} interpolation', () => {
    const store = useLocaleStore()
    expect(store.t('login.adminVerify', { user: 'root' })).toBe('管理员 root 验证')
    expect(store.t('login.adminVerify')).toBe('管理员 {user} 验证')
  })
})
