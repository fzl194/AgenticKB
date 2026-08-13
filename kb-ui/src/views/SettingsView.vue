<template>
  <div class="settings-view">
    <div class="settings-view__header">
      <h2 class="settings-view__title">系统设置</h2>
    </div>

    <div v-if="store.error" class="settings-view__alert">
      {{ store.error }}
    </div>

    <div class="settings-view__card">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="系统配置" name="system">
          <SystemConfigTab />
        </el-tab-pane>
        <el-tab-pane label="品牌外观" name="brand">
          <BrandAppearanceTab />
        </el-tab-pane>
        <el-tab-pane label="用户管理" name="users">
          <UserManagementTab />
        </el-tab-pane>
        <el-tab-pane label="域管理" name="domain">
          <DomainManageTab />
        </el-tab-pane>
        <el-tab-pane label="域详情" name="scenario">
          <DomainDetailTab />
        </el-tab-pane>
        <el-tab-pane label="配置重载" name="reload">
          <ReloadConfigTab />
        </el-tab-pane>
        <el-tab-pane label="代码同步" name="sync">
          <CodeSyncTab />
        </el-tab-pane>
        <el-tab-pane label="系统状态" name="status">
          <SystemStatusTab />
        </el-tab-pane>
        <el-tab-pane label="服务日志" name="logs">
          <ServiceLogsTab />
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useControlPlaneStore } from '@/stores/controlPlane'
import SystemConfigTab from '@/components/settings/SystemConfigTab.vue'
import SystemStatusTab from '@/components/settings/SystemStatusTab.vue'
import BrandAppearanceTab from '@/components/settings/BrandAppearanceTab.vue'
import UserManagementTab from '@/components/settings/UserManagementTab.vue'
import DomainManageTab from '@/components/settings/DomainManageTab.vue'
import DomainDetailTab from '@/components/settings/DomainDetailTab.vue'
import ReloadConfigTab from '@/components/settings/ReloadConfigTab.vue'
import CodeSyncTab from '@/components/settings/CodeSyncTab.vue'
import ServiceLogsTab from '@/components/settings/ServiceLogsTab.vue'

const activeTab = ref('system')
const store = useControlPlaneStore()
</script>

<style scoped>
.settings-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.settings-view__header {
  display: flex;
  align-items: center;
}

.settings-view__title {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  color: var(--kb-text-primary);
}

.settings-view__alert {
  border-radius: var(--kb-radius);
  padding: 12px 14px;
  font-size: 13px;
  background: rgba(239, 68, 68, 0.06);
  border: 1px solid rgba(239, 68, 68, 0.15);
  color: var(--kb-danger);
}

.settings-view__card {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  border: 1px solid var(--kb-border-light);
  box-shadow: var(--kb-shadow-card);
  padding: 16px 20px;
}

.settings-view__card :deep(.el-tabs__header) {
  margin-bottom: 12px;
}
</style>
