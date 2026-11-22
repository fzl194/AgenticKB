import { zh } from './zh'
import { en } from './en'
import type { Language } from './element'

const ZH: Record<string, string> = zh
const EN: Record<string, string> = en

/**
 * 纯查表（无响应性、无 pinia 依赖）：当前语言 → zh 兜底 → key 本身（漏译开发期可见）；
 * params 做 {name} 插值，未提供的占位符原样保留。store 的 t 与非组件助手 t 都委托到这里。
 */
export function translate(
  lang: Language,
  key: string,
  params?: Record<string, string | number>,
): string {
  const text = (lang === 'en' ? EN[key] : undefined) ?? ZH[key] ?? key
  if (!params) return text
  return text.replace(/\{(\w+)\}/g, (token, name) =>
    params[name] !== undefined ? String(params[name]) : token,
  )
}
