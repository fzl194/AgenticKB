import { load as yamlLoad, dump as yamlDump } from 'js-yaml'
import type { BrandConfig } from '@/types/brand'

/**
 * 把 ui.yaml `site:` 块（snake_case）归一化为 BrandConfig（camelCase）。
 * 缺字段不返回，交由调用方用默认值兜底。纯函数。
 *
 * 被 stores/brand.ts（JSON 路径）与 BrandAppearanceTab（YAML 文本路径）共用，
 * 避免两处各写一份 site 解析。
 */
export function normalizeSite(raw: unknown): Partial<BrandConfig> {
  const s = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const out: Partial<BrandConfig> = {}
  if (typeof s.title === 'string') out.title = s.title
  if (typeof s.name === 'string') out.name = s.name
  if (typeof s.badge === 'string') out.badge = s.badge
  if (typeof s.logo_text === 'string') out.logoText = s.logo_text
  if (typeof s.icon === 'string') out.icon = s.icon
  if (typeof s.admin_contact === 'string') out.adminContact = s.admin_contact
  return out
}

export interface UiYamlParts {
  site: Partial<BrandConfig>
  /** site 以外的键（如历史 api-base），保存时原样回写，避免误删。 */
  rest: Record<string, unknown>
}

/** 解析 ui.yaml 原文为 { site, rest }。空/非法 YAML 返回空 part（js-yaml 对空串会抛异常）。 */
export function parseUiYaml(text: string): UiYamlParts {
  let doc: Record<string, unknown> | null | undefined
  try {
    doc = yamlLoad(text) as Record<string, unknown> | null | undefined
  } catch {
    return { site: {}, rest: {} }
  }
  const obj = doc && typeof doc === 'object' ? { ...(doc as Record<string, unknown>) } : {}
  const { site: rawSite, ...rest } = obj
  return { site: normalizeSite(rawSite), rest }
}

/** 把 { site, rest } 序列化回 ui.yaml 原文（site 用 snake_case 键）。 */
export function buildUiYaml(parts: UiYamlParts): string {
  const site = {
    title: parts.site.title ?? '',
    name: parts.site.name ?? '',
    badge: parts.site.badge ?? '',
    logo_text: parts.site.logoText ?? '',
    icon: parts.site.icon ?? '',
    admin_contact: parts.site.adminContact ?? '',
  }
  return yamlDump({ ...parts.rest, site }, { lineWidth: -1 })
}
