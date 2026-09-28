<template>
  <div class="retrieval-records-view">
    <div class="retrieval-records-view__head">
      <div><h2>检索记录</h2><p>统一查看网页、API 与 MCP Tool 的知识访问调用；明细从本版本上线后开始。</p></div>
    </div>
    <div class="retrieval-records-view__switch" role="tablist" aria-label="检索记录视图">
      <button type="button" role="tab" :aria-selected="mode === 'records'" :class="{ active: mode === 'records' }" @click="mode = 'records'">记录明细</button>
      <button type="button" role="tab" :aria-selected="mode === 'analysis'" :class="{ active: mode === 'analysis' }" @click="mode = 'analysis'">使用分析</button>
    </div>
    <RetrievalRecordsPanel v-if="mode === 'records'" :kb-id="kbId" :mcp-key-id="mcpKeyId" />
    <RetrievalRecordsAnalysis v-else :kb-id="kbId" :mcp-key-id="mcpKeyId" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import RetrievalRecordsPanel from '@/components/retrieval/RetrievalRecordsPanel.vue'
import RetrievalRecordsAnalysis from '@/components/retrieval/RetrievalRecordsAnalysis.vue'

const route = useRoute()
const mode = ref<'records' | 'analysis'>('records')
const kbId = computed(() => typeof route.query.kbId === 'string' ? route.query.kbId : '')
const mcpKeyId = computed(() => typeof route.query.mcpKeyId === 'string' ? route.query.mcpKeyId : '')
</script>

<style scoped>
.retrieval-records-view { display: flex; flex-direction: column; gap: 16px; min-width: 0; }.retrieval-records-view__head h2 { margin: 0 0 6px; font-size: 18px; }.retrieval-records-view__head p { margin: 0; color: var(--kb-text-secondary); font-size: 13px; }.retrieval-records-view__switch { display: flex; gap: 4px; border-bottom: 1px solid var(--kb-border-light); }.retrieval-records-view__switch button { padding: 9px 16px; border: 0; border-bottom: 2px solid transparent; color: var(--kb-text-secondary); background: transparent; cursor: pointer; }.retrieval-records-view__switch button.active { color: var(--kb-accent); border-bottom-color: var(--kb-accent); font-weight: 600; }
</style>
