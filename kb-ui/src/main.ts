import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import './styles/variables.css'
import './styles/global.css'
import { useBrandStore } from './stores/brand'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)
app.use(ElementPlus)

// 启动期注入品牌（title/favicon），在 mount 前完成以避免首屏闪烁。
// fetchBrand 内部已捕获异常并兜底默认，finally 必触发——不会阻塞挂载。
const brand = useBrandStore(pinia)
brand.fetchBrand().finally(() => {
  brand.applyBrand()
  app.mount('#app')
})
