<template>
  <div class="login">
    <form class="login__card" @submit.prevent="submit(username, password)">
      <h2 class="login__title">{{ brand.title }}</h2>
      <p class="login__hint">登录到知识库</p>
      <input class="login__input" v-model="username" placeholder="用户名" autocomplete="username" />
      <input
        class="login__input"
        v-model="password"
        type="password"
        placeholder="密码"
        autocomplete="current-password"
      />
      <div v-if="errorMsg" class="login__error">{{ errorMsg }}</div>
      <button class="login__submit" type="submit" :disabled="loading">
        {{ loading ? '登录中…' : '登录' }}
      </button>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useBrandStore } from '@/stores/brand'
import { apiErrorDetail } from '@/api/proxyClient'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const brand = useBrandStore()

const username = ref('')
const password = ref('')
const loading = ref(false)
const errorMsg = ref('')

async function submit(u: string, p: string): Promise<void> {
  if (!u || !p) {
    errorMsg.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    await auth.login(u, p)
    const redirect = (route.query.redirect as string) || '/'
    router.push(redirect)
  } catch (e) {
    errorMsg.value = (await apiErrorDetail(e)) || '用户名或密码错误'
  } finally {
    loading.value = false
  }
}

defineExpose({ submit, errorMsg })
</script>

<style scoped>
.login {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--kb-bg-page);
}
.login__card {
  width: 340px;
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: 12px;
  padding: 32px;
  box-shadow: var(--kb-shadow-card);
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.login__title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: var(--kb-text-primary);
}
.login__hint {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--kb-text-tertiary);
}
.login__input {
  padding: 10px 12px;
  border: 1px solid var(--kb-border);
  border-radius: 8px;
  font-size: 14px;
}
.login__error {
  color: var(--kb-danger);
  font-size: 13px;
}
.login__submit {
  margin-top: 8px;
  padding: 10px;
  border: none;
  border-radius: 8px;
  background: var(--kb-accent);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.login__submit:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
