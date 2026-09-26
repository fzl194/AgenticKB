<template>
  <div class="users-view">
    <section class="users-view__intro">
      <template v-if="isSiteAdmin">
        <h3>全局用户管理</h3>
        <p>
          你是系统管理员，可以创建和维护全局账号，并为用户分配不同知识域中的普通用户或域管理员权限。
        </p>
      </template>
      <template v-else>
        <h3>{{ domainName }} · 域用户管理</h3>
        <p>
          你是当前域的域管理员，可以添加或移除普通成员，并管理本域所有知识库；不能修改全局账号、密码或系统角色。
        </p>
      </template>
    </section>

    <section class="users-view__card">
      <UserManagementTab :domain-id="isSiteAdmin ? undefined : domainStore.currentDomain" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import UserManagementTab from '@/components/settings/UserManagementTab.vue'
import { useAuthStore } from '@/stores/auth'
import { useDomainStore } from '@/stores/domain'

defineOptions({ name: 'UserManagementView' })

const auth = useAuthStore()
const domainStore = useDomainStore()
const isSiteAdmin = computed(() => auth.siteRole === 'admin')
const domainName = computed(
  () => domainStore.currentDomainInfo?.display_name || domainStore.currentDomain || '当前知识域',
)
</script>

<style scoped>
.users-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.users-view__intro,
.users-view__card {
  padding: 20px 22px;
  border: 1px solid var(--kb-border);
  border-radius: 12px;
  background: var(--kb-bg-card);
}

.users-view__intro h3 {
  margin: 0 0 8px;
  color: var(--kb-text-primary);
  font-size: 16px;
}

.users-view__intro p {
  margin: 0;
  color: var(--kb-text-secondary);
  font-size: 13px;
  line-height: 1.7;
}
</style>
