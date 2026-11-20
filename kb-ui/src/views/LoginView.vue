<template>
  <div class="login">
    <button
      type="button"
      class="login__lang"
      data-test="lang-toggle"
      :title="locale.lang === 'zh' ? 'Switch to English' : '切换为中文'"
      @click="toggleLang"
    >{{ locale.lang === 'zh' ? 'EN' : '中' }}</button>

    <form class="login__card" @submit.prevent="onSubmit">
      <h2 class="login__title">{{ brand.title }}</h2>
      <p class="login__hint">{{ step === 'username' ? locale.t('login.hint') : locale.t('login.adminVerify', { user: username.trim() }) }}</p>

      <input
        class="login__input"
        v-model="username"
        :placeholder="locale.t('login.usernamePlaceholder')"
        autocomplete="username"
        :disabled="step === 'password'"
      />

      <input
        v-if="step === 'password'"
        class="login__input"
        v-model="password"
        type="password"
        :placeholder="locale.t('login.passwordPlaceholder')"
        autocomplete="current-password"
      />

      <div v-if="errorMsg" class="login__error">
        {{ errorMsg }}
        <span v-if="brand.adminContact" class="login__contact">{{ locale.t('login.contact', { contact: brand.adminContact }) }}</span>
      </div>

      <button class="login__submit" type="submit" :disabled="loading">
        {{ loading ? locale.t('login.processing') : (step === 'password' ? locale.t('login.submit') : locale.t('login.next')) }}
      </button>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useBrandStore } from '@/stores/brand'
import { useLocaleStore } from '@/stores/locale'
import { useAuthApi } from '@/api/auth'
import { apiErrorDetail } from '@/api/proxyClient'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const brand = useBrandStore()
const locale = useLocaleStore()
const api = useAuthApi()

function toggleLang(): void {
  locale.setLang(locale.lang === 'zh' ? 'en' : 'zh')
}

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
    errorMsg.value = locale.t('login.emptyUsername')
    return
  }
  loading.value = true
  try {
    if (step.value === 'username') {
      const res = await api.identify(username.value.trim())
      if (res.mode === 'not_found') {
        errorMsg.value = locale.t('login.userNotFound')
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
        errorMsg.value = locale.t('login.emptyPassword')
        return
      }
      await auth.login(username.value.trim(), password.value)
      redirectAfterLogin()
    }
  } catch (e) {
    errorMsg.value = (await apiErrorDetail(e))
      || (step.value === 'password' ? locale.t('login.badCredentials') : locale.t('login.requestFailed'))
  } finally {
    loading.value = false
  }
}

defineExpose({ onSubmit, step, username, password, errorMsg })
</script>

<style scoped>
.login {
  position: relative;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--kb-bg-page);
}

/* 语言切换：右上角 pill（显式显示目标语言，避歧义） */
.login__lang {
  position: absolute;
  top: 20px;
  right: 24px;
  padding: 4px 12px;
  border: 1px solid var(--kb-border);
  border-radius: 16px;
  background: var(--kb-bg-card);
  color: var(--kb-text-secondary);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.login__lang:hover {
  color: var(--kb-accent);
  border-color: var(--kb-accent);
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
