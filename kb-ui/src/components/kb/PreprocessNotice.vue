<template>
  <section v-if="hasNotice" class="preprocess-notice" :class="`preprocess-notice--${visualState}`">
    <div class="preprocess-notice__title">{{ title }}</div>
    <p v-if="errorDetail" class="preprocess-notice__detail">{{ errorDetail }}</p>
    <p v-if="actionHint" class="preprocess-notice__action">{{ actionHint }}</p>

    <div v-if="summaryItems.length" class="preprocess-notice__summary">
      <span v-for="item in summaryItems" :key="item.label">
        {{ item.label }} {{ item.value }}
      </span>
    </div>

    <ul v-if="warnings?.length" class="preprocess-notice__warnings">
      <li v-for="(warning, index) in warnings" :key="`${warning.code}-${index}`">
        <span class="preprocess-notice__code">{{ warning.code }}</span>
        <span>{{ warning.message }}</span>
        <span v-if="warning.sheet_name || warning.cell_range" class="preprocess-notice__location">
          {{ t('kbSearch.notice.warningLocation', { location: [warning.sheet_name, warning.cell_range].filter(Boolean).join(' / ') }) }}
        </span>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ExcelPreprocessSummary, PreprocessWarning } from '@/types'
import { t } from '@/locales/t'

const props = defineProps<{
  status?: 'success' | 'partial' | 'failed' | null
  errorCode?: string | null
  errorDetail?: string | null
  warnings?: PreprocessWarning[]
  summary?: ExcelPreprocessSummary | null
}>()

const hasNotice = computed(() => Boolean(
  props.status
  || props.errorCode
  || props.errorDetail
  || props.warnings?.length
  || props.summary,
))
const visualState = computed(() => props.status || (props.errorDetail ? 'failed' : 'success'))
const title = computed(() => {
  const labels: Record<string, string> = {
    success: t('kbSearch.notice.titleSuccess'),
    partial: t('kbSearch.notice.titlePartial'),
    failed: t('kbSearch.notice.titleFailed'),
  }
  return labels[visualState.value] || t('kbSearch.notice.titleDefault')
})
const actionHint = computed(() => {
  if (props.errorCode === 'doc_converter_unavailable') {
    return t('kbSearch.notice.hintDocConverterUnavailable')
  }
  if (props.errorCode === 'excel_password_protected') {
    return t('kbSearch.notice.hintExcelPassword')
  }
  return ''
})
const summaryItems = computed(() => {
  const summary = props.summary
  if (!summary) return []
  const items: Array<{ label: string; value?: number }> = [
    { label: t('kbSearch.notice.summarySheets'), value: summary.sheet_count },
    { label: t('kbView.detail.readinessLevel.parsed'), value: summary.parsed_sheet_count },
    { label: t('kbSearch.notice.summaryEmptySheets'), value: summary.skipped_empty_sheet_count },
    { label: t('kbSearch.notice.summaryTableRegions'), value: summary.table_region_count },
    { label: t('kbSearch.notice.summaryNonEmptyCells'), value: summary.nonempty_cell_count },
  ]
  return items.filter(
    (item): item is { label: string; value: number } => typeof item.value === 'number',
  )
})
</script>

<style scoped>
.preprocess-notice {
  margin: 12px 0;
  padding: 12px 14px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
  color: #334155;
}
.preprocess-notice--partial { border-color: #f59e0b; background: #fffbeb; }
.preprocess-notice--failed { border-color: #ef4444; background: #fef2f2; }
.preprocess-notice--success { border-color: #22c55e; background: #f0fdf4; }
.preprocess-notice__title { font-weight: 600; }
.preprocess-notice__detail,
.preprocess-notice__action { margin: 6px 0 0; line-height: 1.5; }
.preprocess-notice__action { font-weight: 500; }
.preprocess-notice__summary { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 8px; font-size: 13px; }
.preprocess-notice__warnings { margin: 8px 0 0; padding-left: 20px; }
.preprocess-notice__warnings li { margin-top: 4px; line-height: 1.5; }
.preprocess-notice__code { margin-right: 8px; font-family: monospace; font-size: 12px; }
.preprocess-notice__location { color: #64748b; }
</style>
