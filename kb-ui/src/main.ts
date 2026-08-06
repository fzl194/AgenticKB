import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
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
app.use(router)
app.use(ElementPlus)

// 启动期注入品牌 + 恢复登录态。token 存在时 fetchMe 取 profile，使首屏侧边栏按角色渲染。
const brand = useBrandStore(pinia)
const auth = useAuthStore(pinia)
auth.restore() // 从 localStorage 恢复 token（不触发网络）

Promise.allSettled([
  brand.fetchBrand(),
  auth.token ? auth.fetchMe() : Promise.resolve(),
]).finally(() => {
  brand.applyBrand()
  app.mount('#app')
})
