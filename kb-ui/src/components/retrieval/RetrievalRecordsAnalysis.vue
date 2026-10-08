<template>
  <section class="records-analysis">
    <div v-if="loading" class="records-analysis__notice">正在加载使用分析…</div>
    <div v-else-if="error" class="records-analysis__notice records-analysis__notice--error">
      {{ error }} <button type="button" @click="load">重试</button>
    </div>
    <div v-else-if="!summary?.available" class="records-analysis__notice">
      统一记录从本版本上线后开始产生，当前尚无可分析的调用。
    </div>
    <template v-else-if="summary">
      <div class="records-analysis__stats">
        <StatsCard label="调用量" :value="summary.summary.total_calls" icon="🔍" />
        <StatsCard label="检索调用" :value="summary.summary.calls" icon="⌕" />
        <StatsCard label="零结果率" :value="formatRate(summary.summary.no_result_rate)" icon="🕳" />
        <StatsCard label="失败率" :value="formatRate(summary.summary.failure_rate)" icon="⚠" />
        <StatsCard label="平均延迟" :value="formatDuration(summary.summary.avg_duration_ms)" icon="〽" />
        <StatsCard label="P95 延迟" :value="formatDuration(summary.summary.p95_duration_ms)" icon="⏱" />
      </div>

      <div class="records-analysis__card records-analysis__card--wide">
        <h3>调用趋势</h3>
        <LineChart
          :labels="trendLabels"
          :series="[
            { name: '全部调用', data: trendCalls },
            { name: '零结果', data: trendNoResult },
            { name: '失败', data: trendFailed },
          ]"
          height="260px"
        />
      </div>

      <div class="records-analysis__grid">
        <div class="records-analysis__card"><h3>范式分布</h3><BarChart v-if="paradigmBars.length" :data="paradigmBars" horizontal height="220px" /><p v-else>暂无范式调用</p></div>
        <div class="records-analysis__card"><h3>MCP Tool 分布</h3><BarChart v-if="toolBars.length" :data="toolBars" horizontal height="220px" /><p v-else>暂无 Tool 调用</p></div>
        <div class="records-analysis__card"><h3>来源分布</h3><BarChart v-if="sourceBars.length" :data="sourceBars" horizontal height="220px" /><p v-else>暂无来源数据</p></div>
      </div>

      <div class="records-analysis__card records-analysis__card--wide">
        <h3>查询热词</h3>
        <WordCloud v-if="cloudItems.length" :items="cloudItems" height="260px" />
        <p v-else>窗口内没有查询</p>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRetrievalRecordsApi } from '@/api/retrievalRecords'
import { apiErrorDetail } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import StatsCard from '@/components/common/StatsCard.vue'
import { useRetrievalNames } from '@/components/retrieval/useRetrievalNames'
import { aggregateTerms } from '@/components/retrieval/segmentTerms'
import LineChart from '@/components/charts/LineChart.vue'
import BarChart from '@/components/charts/BarChart.vue'
import WordCloud from '@/components/charts/WordCloud.vue'
import type { RetrievalRecordsSummary } from '@/types/retrievalRecords'

const props = withDefaults(defineProps<{ kbId?: string; mcpKeyId?: string }>(), { kbId: '', mcpKeyId: '' })
const api = useRetrievalRecordsApi()
const domainStore = useDomainStore()
// 范式分布图按名称展示：接口只回 paradigm_id，这里解析（拉失败回落 ID）。
const { load: loadNames, paradigmName } = useRetrievalNames()
const summary = ref<RetrievalRecordsSummary | null>(null)
const loading = ref(false)
const error = ref('')
let generation = 0

const trendLabels = computed(() => summary.value?.trend.map(item => item.date.slice(5)) ?? [])
const trendCalls = computed(() => summary.value?.trend.map(item => item.total_calls) ?? [])
const trendNoResult = computed(() => summary.value?.trend.map(item => item.no_result) ?? [])
const trendFailed = computed(() => summary.value?.trend.map(item => item.failed) ?? [])
const paradigmBars = computed(() => summary.value?.paradigms.map(item => ({ name: paradigmName(item.paradigm_id), value: item.calls })) ?? [])
// tools 后端已只回带 tool_name 的 MCP 调用，这里不再兜底"非 MCP"桶
const toolBars = computed(() => summary.value?.tools.map(item => ({ name: item.tool_name, value: item.calls })) ?? [])
const sourceBars = computed(() => Object.entries(summary.value?.sources ?? {}).map(([name, value]) => ({ name: sourceLabel(name), value })))
// 热词 = 去重问句分词后的词频（原句统计几乎全是 count=1 的噪音）；超量由词云内部截断
const cloudItems = computed(() => aggregateTerms(
  (summary.value?.top_queries ?? []).map(q => ({ text: q.query_text, count: q.count, noResult: q.no_result ?? 0 })),
).map(t => ({ name: t.term, value: t.count })))

async function load(): Promise<void> {
  const domain = domainStore.currentDomain
  if (!domain) return
  const current = ++generation
  loading.value = true
  error.value = ''
  // 名称映射走自己的缓存与兜底，失败不连累分析本体。
  void loadNames(domain)
  try {
    const value = await api.getSummary(domain, { days: 7, kbId: props.kbId || undefined, mcpKeyId: props.mcpKeyId || undefined })
    if (current !== generation || domain !== domainStore.currentDomain) return
    summary.value = value
  } catch (reason) {
    if (current !== generation) return
    error.value = await apiErrorDetail(reason)
  } finally {
    if (current === generation) loading.value = false
  }
}

function formatRate(value: number): string { return `${(value * 100).toFixed(1)}%` }
function formatDuration(value: number): string { return `${Math.round(value)} ms` }
function sourceLabel(value: string): string { return ({ web: '网页', mcp: 'MCP', api: 'API' } as Record<string, string>)[value] ?? value }

onMounted(load)
watch(() => domainStore.currentDomain, load)
watch(() => [props.kbId, props.mcpKeyId], load)
</script>

<style scoped>
.records-analysis { display: flex; flex-direction: column; gap: 14px; }.records-analysis__stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }.records-analysis__grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }.records-analysis__card { min-width: 0; padding: 16px; border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius); background: var(--kb-bg-card); }.records-analysis__card h3 { margin: 0 0 12px; color: var(--kb-text-secondary); font-size: 13px; }.records-analysis__card p { color: var(--kb-text-tertiary); font-size: 12px; text-align: center; }.records-analysis__notice { padding: 24px; color: var(--kb-text-tertiary); text-align: center; border: 1px dashed var(--kb-border); border-radius: var(--kb-radius); }.records-analysis__notice--error { color: var(--kb-danger); } @media (max-width: 1100px) { .records-analysis__grid { grid-template-columns: 1fr; } }
</style>
