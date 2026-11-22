<template>
  <div class="empty-state">
    <el-icon :size="40" class="empty-state__icon"><component :is="icon" /></el-icon>
    <p class="empty-state__text">{{ text || t('common.empty') }}</p>
    <slot name="action" />
  </div>
</template>

<script setup lang="ts">
import { FolderOpened } from '@element-plus/icons-vue'
import { t } from '@/locales/t'

withDefaults(defineProps<{
  text?: string
  icon?: typeof FolderOpened
}>(), {
  icon: FolderOpened,
})
</script>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  color: var(--kb-text-tertiary);
}

.empty-state__icon {
  margin-bottom: 12px;
  opacity: 0.4;
}

.empty-state__text {
  font-size: 14px;
  margin: 0 0 12px;
}
</style>
