import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import './styles/variables.css'
import './styles/global.css'
import { useBrandStore } from './stores/brand'
import { useAuthStore } from './stores/auth'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

// 关键顺序：bootstrap（恢复 token + 起 fetchMe，置 auth.ready）必须在 app.use(router) 之前。
// vue-router 的初始导航在 app.use(router) 时立即触发，路由守卫靠 await auth.ready 等 fetchMe 完成。
const brand = useBrandStore(pinia)
const auth = useAuthStore(pinia)
auth.bootstrap()
app.use(router)
// 中文 locale：否则确认弹窗按钮/表格空态渲染英文 OK/Cancel、No Data
app.use(ElementPlus, { locale: zhCn })

Promise.allSettled([brand.fetchBrand(), auth.ready]).finally(() => {
  brand.applyBrand()
  app.mount('#app')
})
