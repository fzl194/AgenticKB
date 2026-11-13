import { onBeforeUnmount, watch } from 'vue'

/**
 * 视图级浏览器标签标题（2026-11-13 用户反馈：制品页标签恒为站名
 * "AgenticKB"，看不出在哪）。
 *
 * 品牌后缀取**挂载时刻的 document.title**（main.ts 启动期 brand.applyBrand
 * 已写入站名）——不直接依赖 pinia store，测试环境（无 active pinia）安全。
 * viewTitle 变化跟随；卸载还原进站名。
 */
export function useDocumentTitle(viewTitle: () => string): void {
  const brandTitle = document.title

  const apply = (value: string): void => {
    document.title = brandTitle && brandTitle !== value
      ? `${value} · ${brandTitle}`
      : value
  }

  watch(viewTitle, apply, { immediate: true })
  onBeforeUnmount(() => { if (brandTitle) document.title = brandTitle })
}
