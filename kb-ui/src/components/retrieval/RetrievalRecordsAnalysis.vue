<template>
  <section class="records-analysis">
    <div class="records-analysis__toolbar">
      <span class="records-analysis__toolbar-label">{{ t('records.range.label') }}</span>
      <select v-model.number="range.days" class="records-analysis__select" :aria-label="t('records.range.label')" @change="onPresetChange">
        <option :value="7">{{ t('records.range.last7Days') }}</option>
        <option :value="30">{{ t('records.range.last30Days') }}</option>
        <option :value="90">{{ t('records.range.last90Days') }}</option>
        <option value="custom">{{ t('records.range.custom') }}</option>
      </select>
      <template v-if="range.days === 'custom'">
        <input v-model="range.startDate" type="date" class="records-analysis__date" :aria-label="t('records.range.startDate')" />
        <span class="records-analysis__sep">{{ t('records.range.to') }}</span>
        <input v-model="range.endDate" type="date" class="records-analysis__date" :aria-label="t('records.range.endDate')" />
      </template>
      <span v-if="rangeError" class="records-analysis__range-error">{{ rangeError }}</span>
    </div>

    <div v-if="loading" class="records-analysis__notice">{{ t('records.analysis.loading') }}</div>
    <div v-else-if="error" class="records-analysis__notice records-analysis__notice--error">
      {{ error }} <button type="button" @click="load">{{ t('common.retry') }}</button>
    </div>
    <div v-else-if="!summary?.available" class="records-analysis__notice">
      {{ t('records.analysis.empty') }}
    </div>
    <template v-else-if="summary">
      <div class="records-analysis__stats">
        <StatsCard :label="t('records.metric.calls')" :value="summary.summary.total_calls" icon="🔍" />
        <StatsCard :label="t('records.metric.retrievalCalls')" :value="summary.summary.calls" icon="⌕" />
        <StatsCard :label="t('records.metric.noResultRate')" :value="formatRate(summary.summary.no_result_rate)" icon="🕳" />
        <StatsCard :label="t('records.metric.failureRate')" :value="formatRate(summary.summary.failure_rate)" icon="⚠" />
        <StatsCard :label="t('records.metric.avgLatency')" :value="formatDuration(summary.summary.avg_duration_ms)" icon="〽" />
        <StatsCard :label="t('records.metric.p95Latency')" :value="formatDuration(summary.summary.p95_duration_ms)" icon="⏱" />
      </div>

      <div class="records-analysis__card records-analysis__card--wide">
        <h3>{{ t('records.analysis.trend') }}</h3>
        <LineChart
          :labels="trendLabels"
          :series="[
            { name: t('records.series.allCalls'), data: trendCalls },
            { name: t('records.status.no_result'), data: trendNoResult },
            { name: t('records.status.failed'), data: trendFailed },
          ]"
          height="260px"
        />
      </div>

      <div class="records-analysis__grid">
        <div class="records-analysis__card"><h3>{{ t('records.analysis.paradigm') }}</h3><BarChart v-if="paradigmBars.length" :data="paradigmBars" horizontal height="220px" /><p v-else>{{ t('records.analysis.paradigmEmpty') }}</p></div>
        <div class="records-analysis__card"><h3>{{ t('records.analysis.tool') }}</h3><BarChart v-if="toolBars.length" :data="toolBars" horizontal height="220px" /><p v-else>{{ t('records.analysis.toolEmpty') }}</p></div>
        <div class="records-analysis__card"><h3>{{ t('records.analysis.source') }}</h3><BarChart v-if="sourceBars.length" :data="sourceBars" horizontal height="220px" /><p v-else>{{ t('records.analysis.sourceEmpty') }}</p></div>
      </div>

      <div class="records-analysis__card records-analysis__card--wide">
        <h3>{{ t('records.analysis.topTerms') }}</h3>
        <WordCloud v-if="cloudItems.length" :items="cloudItems" height="260px" />
        <p v-else>{{ t('records.analysis.topTermsEmpty') }}</p>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRetrievalRecordsApi } from '@/api/retrievalRecords'
import { apiErrorDetail } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import { t } from '@/locales/t'
import StatsCard from '@/components/common/StatsCard.vue'
import { useRetrievalNames } from '@/components/retrieval/useRetrievalNames'
import {
  prefillCustomRange, rangeFilters, validateCustomRange, type TimeRangeState,
} from '@/components/retrieval/timeRange'
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

// 时间范围是本页唯一筛选：无需筛选按钮，变更即生效（校验失败只提示不请求）。
const range = reactive<TimeRangeState>({ days: 7, startDate: '', endDate: '' })
const rangeError = ref('')

function onPresetChange(): void {
  rangeError.value = ''
  prefillCustomRange(range)
}

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
).map(item => ({ name: item.term, value: item.count })))

async function load(): Promise<void> {
  const domain = domainStore.currentDomain
  if (!domain) return
  const current = ++generation
  loading.value = true
  error.value = ''
  // 名称映射走自己的缓存与兜底，失败不连累分析本体。
  void loadNames(domain)
  try {
    const value = await api.getSummary(domain, {
      ...rangeFilters(range),
      kbId: props.kbId || undefined,
      mcpKeyId: props.mcpKeyId || undefined,
    })
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
function sourceLabel(value: string): string { return ({ web: t('records.source.web'), mcp: 'MCP', api: 'API' } as Record<string, string>)[value] ?? value }

onMounted(load)
watch(() => domainStore.currentDomain, load)
watch(() => [props.kbId, props.mcpKeyId], load)
// 时间范围三项合成一个快照 watch：预填在同 tick 完成后只触发一次加载
watch(() => [range.days, range.startDate, range.endDate], () => {
  if (range.days === 'custom') {
    rangeError.value = validateCustomRange(range)
    if (rangeError.value) return
  } else {
    rangeError.value = ''
  }
  load()
})
</script>

<style scoped>
.records-analysis { display: flex; flex-direction: column; gap: 14px; }.records-analysis__stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }.records-analysis__grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }.records-analysis__card { min-width: 0; padding: 16px; border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius); background: var(--kb-bg-card); }.records-analysis__card h3 { margin: 0 0 12px; color: var(--kb-text-secondary); font-size: 13px; }.records-analysis__card p { color: var(--kb-text-tertiary); font-size: 12px; text-align: center; }.records-analysis__notice { padding: 24px; color: var(--kb-text-tertiary); text-align: center; border: 1px dashed var(--kb-border); border-radius: var(--kb-radius); }.records-analysis__notice--error { color: var(--kb-danger); }
.records-analysis__toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 12px; border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius); background: var(--kb-bg-card); }.records-analysis__toolbar-label { color: var(--kb-text-tertiary); font-size: 12px; }.records-analysis__select, .records-analysis__date { min-height: 34px; padding: 0 10px; border: 1px solid var(--kb-border); border-radius: 6px; color: var(--kb-text-primary); background: var(--kb-bg-card); }.records-analysis__date { width: 150px; }.records-analysis__sep { color: var(--kb-text-tertiary); font-size: 12px; }.records-analysis__range-error { color: var(--kb-danger); font-size: 12px; } @media (max-width: 1100px) { .records-analysis__grid { grid-template-columns: 1fr; } }
</style>
