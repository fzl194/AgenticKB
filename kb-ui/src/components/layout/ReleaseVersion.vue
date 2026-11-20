<template>
  <div class="release-version">
    <button
      type="button"
      class="release-version__trigger"
      data-testid="release-version"
      :disabled="!release"
      :title="release ? locale.t('release.viewNotes') : loading ? locale.t('release.loadingTip') : locale.t('release.failedTip')"
      @click="dialogVisible = true"
    >
      <span>{{ locale.t('release.label') }}</span>
      <strong v-if="release">v{{ release.version }}</strong>
      <span v-else-if="loading">{{ locale.t('release.loading') }}</span>
      <span v-else>{{ locale.t('release.unavailable') }}</span>
    </button>

    <el-dialog
      v-model="dialogVisible"
      :title="locale.t('release.title')"
      width="460px"
      append-to-body
    >
      <template v-if="release">
        <div class="release-version__heading">
          <strong>v{{ release.version }} · {{ release.title }}</strong>
          <span>{{ locale.t('release.releasedAt', { date: release.released_at }) }}</span>
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
import { useLocaleStore } from '@/stores/locale'

const locale = useLocaleStore()
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
