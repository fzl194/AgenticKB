<template>
  <div class="login">
    <form class="login__card" @submit.prevent="onSubmit">
      <h2 class="login__title">{{ brand.title }}</h2>
      <p class="login__hint">{{ step === 'username' ? '登录到知识库' : `管理员 ${username} 验证` }}</p>

      <input
        class="login__input"
        v-model="username"
        placeholder="用户名 / 工号"
        autocomplete="username"
        :disabled="step === 'password'"
      />

      <input
        v-if="step === 'password'"
        class="login__input"
        v-model="password"
        type="password"
        placeholder="密码"
        autocomplete="current-password"
      />

      <div v-if="errorMsg" class="login__error">
        {{ errorMsg }}
        <span v-if="brand.adminContact" class="login__contact">联系管理员：{{ brand.adminContact }}</span>
      </div>

      <button class="login__submit" type="submit" :disabled="loading">
        {{ loading ? '处理中…' : (step === 'password' ? '登录' : '下一步') }}
      </button>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useBrandStore } from '@/stores/brand'
import { useAuthApi } from '@/api/auth'
import { apiErrorDetail } from '@/api/proxyClient'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const brand = useBrandStore()
const api = useAuthApi()

const step = ref<'username' | 'password'>('username')
const username = ref('')
const password = ref('')
const loading = ref(false)
const errorMsg = ref('')

/** 【SSO 口子】工号（member）识别后：现在直接登录；未来换成 window.location = intranetSSOUrl(u)。 */
async function onMemberIdentified(u: string): Promise<void> {
  await auth.login(u)
  redirectAfterLogin()
}

function redirectAfterLogin(): void {
  const r = (route.query.redirect as string) || '/'
  router.push(r)
}

async function onSubmit(): Promise<void> {
  errorMsg.value = ''
  if (!username.value.trim()) {
    errorMsg.value = '请输入用户名 / 工号'
    return
  }
  loading.value = true
  try {
    if (step.value === 'username') {
      const res = await api.identify(username.value.trim())
      if (res.mode === 'not_found') {
        errorMsg.value = '用户未在系统'
      } else if (res.mode === 'member') {
        await onMemberIdentified(username.value.trim())
      } else {
        // password（admin）→ 进第二步
        step.value = 'password'
        password.value = ''
      }
    } else {
      // 第二步：管理员密码
      if (!password.value) {
        errorMsg.value = '请输入密码'
        return
      }
      await auth.login(username.value.trim(), password.value)
      redirectAfterLogin()
    }
  } catch (e) {
    errorMsg.value = (await apiErrorDetail(e)) || (step.value === 'password' ? '用户名或密码错误' : '请求失败')
  } finally {
    loading.value = false
  }
}

defineExpose({ onSubmit, step, username, password, errorMsg })
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
.login__contact {
  display: block;
  margin-top: 4px;
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
