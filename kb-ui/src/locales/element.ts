import type { App } from 'vue'
import { provideGlobalConfig } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import en from 'element-plus/es/locale/lang/en'

export type Language = 'zh' | 'en'

/** Language → Element Plus locale 包（两包都在 element-plus 依赖内，零新增）。 */
export function epLocale(lang: Language) {
  return lang === 'en' ? en : zhCn
}

/**
 * 重注入 app 级 locale，覆盖命令式脱挂组件（ElMessageBox/ElMessage——
 * 挂 document.body，不在 el-config-provider 子树内，只吃 app 级 provides）。
 *
 * provideGlobalConfig 为 element-plus 公开导出但未文档化的 API
 * （es/components/config-provider/src/hooks/use-global-config.mjs:31-55，
 * 2.14 已源码验证：app.provide 覆写 app 级 provides + 更新模块级 globalConfig，
 * 此后新建的脱挂实例注入即取新语言）。升级 element-plus 后跑
 * __tests__/element.spec.ts 作绊网。
 *
 * ⚠ 只可传 { locale }：本函数在 setup 外调用时 provideGlobalConfig 不做
 * oldConfig 合并，会把 namespace/zIndex/size 等 app 级配置一并覆写为
 * undefined（对抗审查 M1）。当前安全的前提是 main.ts 安装时也只传
 * { locale }——若日后 app.use(ElementPlus, {...}) 加了其他选项，这里必须
 * 改成合并式注入。
 */
export function applyAppLocale(app: App, lang: Language): void {
  provideGlobalConfig({ locale: epLocale(lang) }, app, true)
}
