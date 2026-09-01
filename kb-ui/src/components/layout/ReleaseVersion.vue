<template>
  <div class="release-version">
    <button
      type="button"
      class="release-version__trigger"
      data-testid="release-version"
      :disabled="!release"
      :title="release ? '查看版本说明' : loading ? '版本信息加载中' : '版本信息加载失败'"
      @click="dialogVisible = true"
    >
      <span>系统版本</span>
      <strong v-if="release">v{{ release.version }}</strong>
      <span v-else-if="loading">版本加载中…</span>
      <span v-else>版本信息不可用</span>
    </button>

    <el-dialog
      v-model="dialogVisible"
      title="版本信息"
      width="460px"
      append-to-body
    >
      <template v-if="release">
        <div class="release-version__heading">
          <strong>v{{ release.version }} · {{ release.title }}</strong>
          <span>发布日期：{{ release.released_at }}</span>
        </div>
        <ul class="release-version__changes">
          <li v-for="change in release.changes" :key="change">{{ change }}</li>
        </ul>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useControlPlaneApi, type ReleaseInfo } from '@/api/controlPlane'

const release = ref<ReleaseInfo | null>(null)
const loading = ref(true)
const dialogVisible = ref(false)
const api = useControlPlaneApi()
let active = true

onMounted(async () => {
  try {
    const result = await api.getReleaseInfo()
    if (active) release.value = result
  } catch {
    if (active) release.value = null
  } finally {
    if (active) loading.value = false
  }
})

onUnmounted(() => {
  active = false
})
</script>

<style scoped>
.release-version__trigger {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--kb-text-tertiary);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.release-version__trigger:not(:disabled):hover {
  color: var(--kb-text-sidebar-active);
}

.release-version__trigger:disabled {
  cursor: default;
  opacity: 0.7;
}

.release-version__trigger strong {
  color: #cbd5e1;
  font-weight: 600;
}

.release-version__heading {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--kb-text-primary);
}

.release-version__heading span {
  color: var(--kb-text-tertiary);
  font-size: 12px;
}

.release-version__changes {
  margin: 18px 0 0;
  padding-left: 20px;
  color: var(--kb-text-secondary);
  line-height: 1.8;
}
</style>
