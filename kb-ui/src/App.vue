<template>
  <el-config-provider :locale="epLocale(localeStore.lang)">
    <router-view />
  </el-config-provider>
</template>

<script setup lang="ts">
import { watch } from 'vue'
import { useRouter } from 'vue-router'
// 显式 import 而非依赖 auto-import resolver——vitest.config.ts 不带 unplugin
// 插件，显式导入在构建与测试两条通道都稳。
import { ElConfigProvider } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { useLocaleStore } from '@/stores/locale'
import { epLocale } from '@/locales/element'

const auth = useAuthStore()
const router = useRouter()
const localeStore = useLocaleStore()

// 树内 EP 组件由 el-config-provider 响应式切换；
// 登录成功/fetchMe 完成后应用该用户的语言分键偏好（spec §4 时序 step 2）；
// 登出（username→undefined）必须清 currentUser——否则下一人在登录页切换
// 语言会改写上一个用户的分键（对抗审查 HIGH-1）。
watch(() => auth.user?.username, (name) => {
  if (name) localeStore.applyUserPreference(name)
  else localeStore.clearUser()
}, { immediate: true })

// A storage event can end the session in another tab without a click or route
// change in this tab. Replace avoids leaving a protected page in browser history.
watch(() => auth.loggingOut, (loggingOut) => {
  if (loggingOut) void router.replace('/login')
}, { immediate: true })

watch(() => auth.externalSessionGeneration, async () => {
  await auth.ready
  if (auth.isAuthenticated) await router.replace('/')
})
</script>
