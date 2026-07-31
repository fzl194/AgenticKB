import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useControlPlaneApi } from '@/api/controlPlane'
import type { BrandConfig } from '@/types/brand'

/** 当前硬编码值的兜底；main_control 不可达或缺 site 块时使用。 */
export const DEFAULT_BRAND: BrandConfig = {
  title: 'CoreMasterKB',
  name: 'CoreMaster',
  badge: 'Knowledge Base',
  logoText: 'KB',
  icon: '',
}

const FAVICON_SELECTOR = 'link[rel="icon"]'

/**
 * 把 site.icon 字段解析为可用的图标 URL：
 * - 空/空白 → 默认 /favicon.svg
 * - data: URI 或 http(s) URL → 原样
 * - 其它（裸路径等）→ 原样交由浏览器解析
 */
export function resolveIcon(icon: string | undefined | null): string {
  const v = (icon ?? '').trim()
  if (!v) return '/favicon.svg'
  if (v.startsWith('data:') || /^https?:\/\//i.test(v)) return v
  return v
}

/** 从 GET /api/v1/system/ui 的返回里提取 site 块（容错：缺字段不覆盖默认）。 */
function readSite(raw: unknown): Partial<BrandConfig> {
  const root = raw as Record<string, unknown> | null | undefined
  const site = root?.site
  if (!site || typeof site !== 'object') return {}
  const s = site as Record<string, unknown>
  const out: Partial<BrandConfig> = {}
  if (typeof s.title === 'string') out.title = s.title
  if (typeof s.name === 'string') out.name = s.name
  if (typeof s.badge === 'string') out.badge = s.badge
  if (typeof s.logo_text === 'string') out.logoText = s.logo_text
  if (typeof s.icon === 'string') out.icon = s.icon
  return out
}

export const useBrandStore = defineStore('brand', () => {
  const title = ref(DEFAULT_BRAND.title)
  const name = ref(DEFAULT_BRAND.name)
  const badge = ref(DEFAULT_BRAND.badge)
  const logoText = ref(DEFAULT_BRAND.logoText)
  const icon = ref(DEFAULT_BRAND.icon)
  const loaded = ref(false)

  function applyValues(partial: Partial<BrandConfig>): void {
    if (partial.title !== undefined) title.value = partial.title
    if (partial.name !== undefined) name.value = partial.name
    if (partial.badge !== undefined) badge.value = partial.badge
    if (partial.logoText !== undefined) logoText.value = partial.logoText
    if (partial.icon !== undefined) icon.value = partial.icon
  }

  /** 拉取 site 品牌配置。失败静默兜底默认，绝不抛——启动期不能因品牌拉取失败而崩。 */
  async function fetchBrand(): Promise<void> {
    try {
      const api = useControlPlaneApi()
      const raw = await api.getSystemConfig('ui')
      applyValues(readSite(raw))
    } catch {
      // main_control 不可达或缺 site 块：保持默认值
    } finally {
      loaded.value = true
    }
  }

  /** 把品牌应用到 DOM：document.title + favicon <link>。 */
  function applyBrand(): void {
    if (title.value) document.title = title.value
    const href = resolveIcon(icon.value)
    let link = document.querySelector<HTMLLinkElement>(FAVICON_SELECTOR)
    if (!link) {
      link = document.createElement('link')
      link.rel = 'icon'
      document.head.appendChild(link)
    }
    link.href = href
  }

  return { title, name, badge, logoText, icon, loaded, fetchBrand, applyBrand, applyValues }
})
