import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import './styles/variables.css'
import './styles/global.css'
import './styles/markdown.css'
import { useBrandStore } from './stores/brand'
import { useAuthStore } from './stores/auth'
import { useLocaleStore } from './stores/locale'
import { epLocale } from './locales/element'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

// 关键顺序：bootstrap（恢复 token + 起 fetchMe，置 auth.ready）必须在 app.use(router) 之前。
// vue-router 的初始导航在 app.use(router) 时立即触发，路由守卫靠 await auth.ready 等 fetchMe 完成。
const brand = useBrandStore(pinia)
const auth = useAuthStore(pinia)
const locale = useLocaleStore(pinia)
locale.attachApp(app) // 脱挂通道重注入用（stores/locale.ts 说明）
auth.bootstrap()
app.use(router)
// EP locale 初值取 locale store（localStorage 全局键，默认 zh）；运行时切换由
// stores/locale.ts 经 locales/element.ts applyAppLocale 重注入脱挂通道（ElMessageBox 等）。
app.use(ElementPlus, { locale: epLocale(locale.lang) })

Promise.allSettled([brand.fetchBrand(), auth.ready]).finally(() => {
  brand.applyBrand()
  app.mount('#app')
})
