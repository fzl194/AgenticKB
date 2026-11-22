<template>
  <section class="kb-search-workspace">
    <div class="kb-search-workspace__switch" role="tablist" :aria-label="t('kbSearch.workspace.switchLabel')">
      <button type="button" role="tab" :aria-selected="mode === 'online'" :class="{ active: mode === 'online' }" @click="mode = 'online'">{{ t('kbSearch.workspace.onlineTab') }}</button>
      <button type="button" role="tab" :aria-selected="mode === 'records'" :class="{ active: mode === 'records' }" @click="mode = 'records'">{{ t('title.retrievalRecords') }}</button>
      <router-link v-if="mode === 'records'" class="kb-search-workspace__full" :to="{ name: 'retrieval-records', query: { kbId: kb.id } }">{{ t('kbSearch.workspace.fullRecords') }}</router-link>
    </div>
    <KbSearchPanel v-if="mode === 'online'" :kb="kb" :can-write="canWrite" :readiness="readiness" @updated="$emit('updated')" @go-mining="$emit('go-mining')" />
    <RetrievalRecordsPanel v-else :kb-id="kb.id" />
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { t } from '@/locales/t'
import KbSearchPanel from '@/components/kb/KbSearchPanel.vue'
import RetrievalRecordsPanel from '@/components/retrieval/RetrievalRecordsPanel.vue'
import type { KbReadiness, KbSummary } from '@/types/kb'

defineProps<{ kb: KbSummary; canWrite: boolean; readiness: KbReadiness | null }>()
defineEmits<{ updated: []; 'go-mining': [] }>()
const mode = ref<'online' | 'records'>('online')
</script>

<style scoped>
.kb-search-workspace { display: flex; flex-direction: column; gap: 14px; }.kb-search-workspace__switch { display: flex; align-items: center; gap: 4px; border-bottom: 1px solid var(--kb-border-light); }.kb-search-workspace__switch button { padding: 8px 14px; border: 0; border-bottom: 2px solid transparent; color: var(--kb-text-secondary); background: transparent; cursor: pointer; }.kb-search-workspace__switch button.active { color: var(--kb-accent); border-bottom-color: var(--kb-accent); font-weight: 600; }.kb-search-workspace__full { margin-left: auto; color: var(--kb-accent); text-decoration: none; font-size: 12px; }
</style>
