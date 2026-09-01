<template>
  <header class="header">
    <div class="header__left">
      <h2 class="header__title">{{ pageTitle }}</h2>
    </div>

    <div class="header__right">
      <el-select
        v-model="domainStore.currentDomain"
        class="header__domain-select"
        size="default"
        @change="onDomainChange"
      >
        <el-option
          v-for="d in domainStore.enabledDomains"
          :key="d.domain_id"
          :label="d.display_name"
          :value="d.domain_id"
        />
      </el-select>

      <div class="header__health" :title="allHealthy ? '全部正常' : '存在异常'">
        <span
          class="header__health-dot"
          :class="{
            'header__health-dot--healthy': allHealthy,
            'header__health-dot--degraded': !allHealthy && someHealthy,
            'header__health-dot--unhealthy': !someHealthy,
          }"
        />
        <span class="header__health-label">{{ allHealthy ? '正常' : '异常' }}</span>
      </div>

      <el-dropdown trigger="click" @command="onAccountCommand">
        <span class="header__account">
          <span class="header__account-name">{{ displayName }}</span>
          <el-tag
            size="small"
            :type="auth.siteRole === 'admin' ? 'danger' : 'info'"
            effect="plain"
          >
            {{ auth.siteRole === 'admin' ? '管理员' : '用户' }}
          </el-tag>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-if="auth.siteRole === 'admin'" command="password">修改密码</el-dropdown-item>
            <el-dropdown-item command="logout" :divided="auth.siteRole === 'admin'">登出</el-dropdown-item>
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
import { useAuthApi } from '@/api/auth'
import { apiErrorDetail } from '@/api/proxyClient'

const route = useRoute()
const router = useRouter()
const domainStore = useDomainStore()
const brand = useBrandStore()
const auth = useAuthStore()

const pageTitles: Record<string, string> = {
  dashboard: '概览',
  kb: '知识库',
  'kb-detail': '知识库',
  'kb-run-detail': '知识库',
  'kb-run-doc-detail': '知识库',
  'kb-doc-preview': '知识库',
  'mining-workflows': '挖掘范式',
  'mining-workflow-editor': '挖掘范式',
  paradigm: '检索范式',
  'paradigm-edit': '检索范式',
  'mcp-access': 'MCP 接入',
  llm: 'LLM 服务',
  'llm-task-detail': 'LLM 服务',
  settings: '系统设置',
}

const pageTitle = computed(() => pageTitles[route.name as string] || brand.title)
const displayName = computed(
  () => auth.user?.display_name || auth.user?.username || '—',
)

const allHealthy = ref(true)
const someHealthy = ref(true)

function onDomainChange() {
  allHealthy.value = true
  someHealthy.value = true
}

async function onAccountCommand(cmd: string): Promise<void> {
  if (cmd === 'logout') {
    auth.logout()
    router.push('/login')
  } else if (cmd === 'password') {
    try {
      const { value } = await ElMessageBox.prompt('输入新密码（≥8 位）', '修改密码', {
        inputType: 'password',
        inputPlaceholder: '新密码',
        inputValidator: (v: string) => (v && v.length >= 8) || '至少 8 位',
      })
      const api = useAuthApi()
      await api.changeMyPassword(
        // me/password 端点要旧密码；账户菜单场景下让用户先输旧密码更安全，
        // 这里简化：两步提示。Element Plus 单 prompt 只能取一个值，故分两次。
        await _promptOld(),
        value,
      )
      ElMessage.success('密码已更新，请重新登录')
      auth.logout()
      router.push('/login')
    } catch (e) {
      if (e !== 'cancel' && e !== 'close') {
        ElMessage.error((await apiErrorDetail(e)) || '修改失败')
      }
    }
  }
}

async function _promptOld(): Promise<string> {
  const { value } = await ElMessageBox.prompt('输入当前密码', '验证身份', {
    inputType: 'password',
    inputPlaceholder: '当前密码',
    inputValidator: (v: string) => !!v || '必填',
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
