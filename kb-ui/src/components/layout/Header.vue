<template>
  <header class="header">
    <div class="header__left">
      <h2 class="header__title">{{ pageTitle }}</h2>
    </div>

    <div class="header__right">
      <el-select
        :model-value="domainStore.currentDomain"
        class="header__domain-select"
        size="default"
        @change="onDomainChange"
      >
        <el-option
          v-for="d in domainStore.enabledDomains"
          :key="d.domain_id"
          :label="d.display_name || d.domain_id"
          :value="d.domain_id"
        />
      </el-select>

      <div class="header__health" :title="allHealthy ? locale.t('header.healthAll') : locale.t('header.healthSome')">
        <span
          class="header__health-dot"
          :class="{
            'header__health-dot--healthy': allHealthy,
            'header__health-dot--degraded': !allHealthy && someHealthy,
            'header__health-dot--unhealthy': !someHealthy,
          }"
        />
        <span class="header__health-label">{{ allHealthy ? locale.t('header.healthy') : locale.t('header.unhealthy') }}</span>
      </div>

      <button
        type="button"
        class="header__lang"
        :title="locale.lang === 'zh' ? 'Switch to English' : '切换为中文'"
        data-test="lang-toggle"
        @click="toggleLang"
      >{{ locale.lang === 'zh' ? 'EN' : '中' }}</button>

      <el-dropdown trigger="click" @command="onAccountCommand">
        <span class="header__account">
          <span class="header__account-name">{{ displayName }}</span>
          <el-tag
            size="small"
            :type="auth.siteRole === 'admin' ? 'danger' : 'info'"
            effect="plain"
          >
            {{ accountRoleLabel }}
          </el-tag>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-if="canChangePassword" command="password">{{ locale.t('header.changePassword') }}</el-dropdown-item>
            <el-dropdown-item command="logout" :divided="canChangePassword">{{ locale.t('header.logout') }}</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox, ElMessage } from 'element-plus'
import { useDomainStore } from '@/stores/domain'
import { useBrandStore } from '@/stores/brand'
import { useAuthStore } from '@/stores/auth'
import { useLocaleStore } from '@/stores/locale'
import { useAuthApi } from '@/api/auth'
import { apiErrorDetail } from '@/api/proxyClient'
import { canManageDomainUsers } from '@/utils/domainPermissions'

const route = useRoute()
const router = useRouter()
const domainStore = useDomainStore()
const brand = useBrandStore()
const auth = useAuthStore()
const locale = useLocaleStore()

const pageTitles: Record<string, string> = {
  dashboard: 'title.dashboard',
  workspaces: 'title.workspaces',
  'workspace-detail': 'title.workspaces',
  artifacts: 'title.artifacts',
  'artifact-detail': 'title.artifacts',
  kb: 'title.kb',
  'kb-detail': 'title.kb',
  'kb-run-detail': 'title.kb',
  'kb-run-doc-detail': 'title.kb',
  'kb-doc-preview': 'title.kb',
  'mining-workflows': 'title.miningWorkflows',
  'mining-workflow-editor': 'title.miningWorkflows',
  paradigm: 'title.paradigm',
  'paradigm-edit': 'title.paradigm',
  'mcp-access': 'title.mcp',
  'retrieval-records': 'title.retrievalRecords',
  llm: 'title.llm',
  'llm-task-detail': 'title.llm',
  users: 'title.users',
  settings: 'title.settings',
}

const pageTitle = computed(() => {
  const key = pageTitles[route.name as string]
  return key ? locale.t(key) : brand.title
})
const displayName = computed(
  () => auth.user?.display_name || auth.user?.username || '—',
)
const accountRoleLabel = computed(() => {
  if (auth.siteRole === 'admin') return locale.t('header.roleAdmin')
  return domainStore.currentDomainInfo?.domain_role === 'admin'
    ? locale.t('header.roleDomainAdmin')
    : locale.t('header.roleMember')
})

function toggleLang(): void {
  locale.setLang(locale.lang === 'zh' ? 'en' : 'zh')
}
/** 修改密码入口：系统管理员 + 当前域域管理员（后端要求域管理员必须有密码，
 * 无自助入口就只能每次找系统管理员重置——RBAC 审查 M3）。 */
const canChangePassword = computed(
  () => auth.siteRole === 'admin'
    || domainStore.currentDomainInfo?.domain_role === 'admin',
)

const allHealthy = ref(true)
const someHealthy = ref(true)

function onDomainChange(domainId: string) {
  domainStore.switchDomain(domainId)
  if (
    route.name === 'users'
    && !canManageDomainUsers(auth.siteRole, domainStore.currentDomainInfo)
  ) {
    void router.replace('/')
  }
  allHealthy.value = true
  someHealthy.value = true
}

async function onAccountCommand(cmd: string): Promise<void> {
  if (cmd === 'logout') {
    auth.logout()
    router.replace('/login')
  } else if (cmd === 'password') {
    try {
      const { value } = await ElMessageBox.prompt(locale.t('pwd.newPrompt'), locale.t('pwd.newTitle'), {
        inputType: 'password',
        inputPlaceholder: locale.t('pwd.newPlaceholder'),
        inputValidator: (v: string) => (v && v.length >= 8) || locale.t('pwd.newRule'),
      })
      const api = useAuthApi()
      await api.changeMyPassword(
        // me/password 端点要旧密码；账户菜单场景下让用户先输旧密码更安全，
        // 这里简化：两步提示。Element Plus 单 prompt 只能取一个值，故分两次。
        await _promptOld(),
        value,
      )
      ElMessage.success(locale.t('pwd.success'))
      auth.logout()
      router.replace('/login')
    } catch (e) {
      if (e !== 'cancel' && e !== 'close') {
        ElMessage.error((await apiErrorDetail(e)) || locale.t('pwd.failed'))
      }
    }
  }
}

async function _promptOld(): Promise<string> {
  const { value } = await ElMessageBox.prompt(locale.t('pwd.oldPrompt'), locale.t('pwd.oldTitle'), {
    inputType: 'password',
    inputPlaceholder: locale.t('pwd.oldPlaceholder'),
    inputValidator: (v: string) => !!v || locale.t('pwd.oldRule'),
  })
  return value
}
</script>

<style scoped>
.header {
  height: var(--kb-header-height);
  background: var(--kb-bg-card);
  border-bottom: 1px solid var(--kb-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 28px;
}

.header__left {
  display: flex;
  align-items: baseline;
  gap: 0;
}

.header__title {
  font-size: 17px;
  font-weight: 650;
  color: var(--kb-text-primary);
  margin: 0;
  letter-spacing: -0.2px;
}

.header__divider {
  width: 1px;
  height: 16px;
  background: var(--kb-border);
  margin: 0 14px;
  align-self: center;
}

.header__breadcrumb {
  font-size: 13px;
  color: var(--kb-text-tertiary);
}

.header__right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header__domain-select {
  width: 200px;
}

.header__health {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 20px;
  background: var(--kb-accent-soft);
}

.header__health-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--kb-text-tertiary);
  transition: background var(--kb-duration) var(--kb-ease);
}

.header__health-dot--healthy {
  background: var(--kb-success);
  box-shadow: 0 0 6px rgba(16, 185, 129, 0.4);
}

.header__health-dot--degraded {
  background: var(--kb-warning);
  box-shadow: 0 0 6px rgba(245, 158, 11, 0.4);
}

.header__health-dot--unhealthy {
  background: var(--kb-danger);
  box-shadow: 0 0 6px rgba(239, 68, 68, 0.4);
}

.header__health-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--kb-text-secondary);
}

/* 语言切换：与 .header__health 同族 pill（显式显示目标语言，避歧义） */
.header__lang {
  padding: 4px 10px;
  border: 1px solid var(--kb-border);
  border-radius: 20px;
  background: transparent;
  color: var(--kb-text-secondary);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all var(--kb-duration) var(--kb-ease);
}

.header__lang:hover {
  color: var(--kb-accent);
  border-color: var(--kb-accent);
}

.header__account {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.header__account-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--kb-text-secondary);
}
</style>
