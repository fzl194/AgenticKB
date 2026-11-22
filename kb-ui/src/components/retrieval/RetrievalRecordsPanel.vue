<template>
  <section class="records-panel">
    <div v-if="showSummary" class="records-panel__summary">
      <StatsCard :label="t('records.metric.calls')" :value="summary?.summary.total_calls ?? '—'" icon="🔍" />
      <StatsCard :label="t('records.metric.noResultRate')" :value="formatRate(summary?.summary.no_result_rate)" icon="🕳" />
      <StatsCard :label="t('records.metric.failureRate')" :value="formatRate(summary?.summary.failure_rate)" icon="⚠" />
      <StatsCard :label="t('records.metric.p95Latency')" :value="formatDuration(summary?.summary.p95_duration_ms)" icon="⏱" />
    </div>

    <form class="records-panel__filters" @submit.prevent="applyFilters">
      <select v-model.number="draft.days" class="records-panel__select" :aria-label="t('records.range.label')" @change="onRangePresetChange">
        <option :value="7">{{ t('records.range.last7Days') }}</option>
        <option :value="30">{{ t('records.range.last30Days') }}</option>
        <option :value="90">{{ t('records.range.last90Days') }}</option>
        <option value="custom">{{ t('records.range.custom') }}</option>
      </select>
      <template v-if="draft.days === 'custom'">
        <input v-model="draft.startDate" type="date" class="records-panel__input records-panel__input--date" :aria-label="t('records.range.startDate')" />
        <span class="records-panel__range-sep">{{ t('records.range.to') }}</span>
        <input v-model="draft.endDate" type="date" class="records-panel__input records-panel__input--date" :aria-label="t('records.range.endDate')" />
      </template>
      <select v-model="draft.source" class="records-panel__select" :aria-label="t('records.source.label')">
        <option value="">{{ t('records.filter.allSources') }}</option>
        <option value="web">{{ t('records.source.web') }}</option>
        <option value="mcp">MCP</option>
        <option value="api">API</option>
      </select>
      <select v-model="draft.status" class="records-panel__select" :aria-label="t('common.status')">
        <option value="">{{ t('records.filter.allStatuses') }}</option>
        <option v-for="item in STATUS_OPTIONS" :key="item.value" :value="item.value">{{ t(item.labelKey) }}</option>
      </select>
      <select v-model="draft.operation" class="records-panel__select" :aria-label="t('records.operation.label')">
        <option value="">{{ t('records.filter.allOperations') }}</option><option value="search">{{ t('records.operation.search') }}</option><option value="read">{{ t('records.operation.read') }}</option><option value="upload">{{ t('records.operation.upload') }}</option><option value="replace">{{ t('records.operation.replace') }}</option>
      </select>
      <input v-model.trim="draft.tool" class="records-panel__input records-panel__input--tool" :placeholder="t('records.filter.toolName')" :aria-label="t('records.filter.toolName')" />
      <select v-if="!kbId" v-model="draft.kbId" class="records-panel__select" :aria-label="t('records.filter.kb')">
        <option value="">{{ t('records.filter.allKbs') }}</option>
        <option v-for="kb in kbFilterOptions" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
      </select>
      <input v-model.trim="draft.actorUserId" class="records-panel__input records-panel__input--tool" :placeholder="t('records.filter.userId')" :aria-label="t('records.filter.userId')" />
      <select v-model="draft.paradigmId" class="records-panel__select" :aria-label="t('records.paradigm.label')">
        <option value="">{{ t('records.filter.allParadigms') }}</option>
        <option v-for="paradigm in paradigmFilterOptions" :key="paradigm.id" :value="paradigm.id">{{ paradigm.name }}</option>
      </select>
      <button type="submit" class="records-panel__button">{{ t('records.filter.apply') }}</button>
      <button type="button" class="records-panel__button records-panel__button--plain" @click="resetFilters">{{ t('records.filter.reset') }}</button>
      <button type="button" class="records-panel__button records-panel__button--plain" :disabled="exporting" @click="exportCsv">{{ exporting ? t('records.export.inProgress') : t('records.export.submit') }}</button>
      <p v-if="filterError" class="records-panel__filter-error">{{ filterError }}</p>
      <p v-else-if="exportNotice" class="records-panel__filter-notice">{{ exportNotice }}</p>
    </form>

    <div v-if="error" class="records-panel__notice records-panel__notice--error">
      {{ error }}
      <button type="button" class="records-panel__link" @click="reload">{{ t('common.retry') }}</button>
    </div>
    <div v-else-if="loading && !records.length" class="records-panel__notice">{{ t('records.list.loading') }}</div>
    <div v-else-if="!records.length" class="records-panel__empty">
      <strong>{{ t('records.list.emptyTitle') }}</strong>
      <span>{{ t('records.list.emptyHint') }}</span>
    </div>
    <div v-else class="records-panel__table-wrap">
      <table class="records-panel__table">
        <thead><tr><th>{{ t('records.column.time') }}</th><th>{{ t('records.column.user') }}</th><th>{{ t('records.source.label') }}</th><th>{{ t('records.column.toolOrOperation') }}</th><th>{{ t('records.column.queryOrAction') }}</th><th>{{ t('records.column.targetKb') }}</th><th>{{ t('records.paradigm.label') }}</th><th>{{ t('common.status') }}</th><th>{{ t('records.column.result') }}</th><th>{{ t('common.duration') }}</th><th></th></tr></thead>
        <tbody>
          <tr v-for="item in records" :key="item.id">
            <td class="records-panel__time">{{ formatTime(item.occurred_at) }}</td>
            <td>{{ item.actor_username || '—' }}</td>
            <td>{{ sourceLabel(item.source) }}</td>
            <td>{{ item.tool_name || operationLabel(item.operation) }}</td>
            <td class="records-panel__query" :title="actionSummary(item)">{{ actionSummary(item) }}</td>
            <td>{{ item.kb_ids.length ? item.kb_ids.map(kbName).join(t('records.list.separator')) : '—' }}</td>
            <td>{{ paradigmText(item) }}</td>
            <td><span class="records-panel__status" :class="`is-${item.status}`">{{ statusLabel(item.status) }}</span></td>
            <td>{{ item.result_count ?? '—' }}</td>
            <td>{{ formatDuration(item.duration_ms) }}</td>
            <td><button type="button" class="records-panel__link" :data-test="`record-detail-${item.id}`" @click="openDetail(item.id)">{{ t('records.list.detail') }}</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="records.length" class="records-panel__pager">
      <span>{{ t('records.pager.currentPage', { n: records.length }) }}</span>
      <button type="button" class="records-panel__button records-panel__button--plain" :disabled="!cursorStack.length || loading" @click="previousPage">{{ t('records.pager.previous') }}</button>
      <button type="button" class="records-panel__button records-panel__button--plain" :disabled="!nextCursor || loading" @click="nextPage">{{ t('records.pager.next') }}</button>
    </div>

    <div v-if="detailVisible" class="records-panel__backdrop" @click.self="closeDetail">
      <aside class="records-panel__drawer" role="dialog" aria-modal="true" :aria-label="t('records.detail.title')">
        <div class="records-panel__drawer-head"><h3>{{ t('records.detail.title') }}</h3><button type="button" :aria-label="t('records.detail.close')" class="records-panel__close" @click="closeDetail">×</button></div>
        <div v-if="detailLoading" class="records-panel__notice">{{ t('records.detail.loading') }}</div>
        <template v-else-if="detail">
          <dl class="records-panel__details">
            <dt>{{ t('records.column.callId') }}</dt><dd><code>{{ detail.id }}</code></dd>
            <dt>{{ t('records.column.occurredAt') }}</dt><dd>{{ formatTime(detail.occurred_at) }}</dd>
            <dt>{{ t('records.column.user') }}</dt><dd>{{ detail.actor_username || '—' }}</dd>
            <dt>{{ t('records.source.label') }}</dt><dd>{{ sourceLabel(detail.source) }}</dd>
            <dt>{{ t('records.column.toolOrOperation') }}</dt><dd>{{ detail.tool_name || operationLabel(detail.operation) }}</dd>
            <dt>{{ t('records.detail.query') }}</dt><dd>{{ detail.query_text || '—' }}</dd>
            <dt>{{ t('records.column.targetKb') }}</dt><dd>{{ detail.kb_ids.map(kbName).join(t('records.list.separator')) || '—' }}</dd>
            <dt>{{ t('records.paradigm.label') }}</dt><dd>{{ paradigmText(detail) }}</dd>
            <dt>{{ t('common.status') }}</dt><dd>{{ statusLabel(detail.status) }}</dd>
            <dt>{{ t('records.column.resultCount') }}</dt><dd>{{ detail.result_count ?? '—' }}</dd>
            <dt>{{ t('records.detail.totalDuration') }}</dt><dd>{{ formatDuration(detail.duration_ms) }}</dd>
            <dt>{{ t('records.column.errorCode') }}</dt><dd>{{ detail.error_code || '—' }}</dd>
            <template v-for="field in safeDetailFields(detail)" :key="field.key">
              <dt>{{ field.label }}</dt><dd>{{ field.value }}</dd>
            </template>
          </dl>

          <div v-if="detail.payload" class="records-panel__payload">
            <details open class="records-panel__payload-section">
              <summary>{{ t('records.detail.requestParams') }}</summary>
              <pre>{{ formatJson(detail.payload.request_json) }}</pre>
            </details>
            <details open class="records-panel__payload-section">
              <summary>{{ t('records.detail.effectiveParams') }}</summary>
              <pre>{{ formatJson(detail.payload.effective_context_json) }}</pre>
            </details>
            <details open class="records-panel__payload-section">
              <summary>{{ t('records.detail.response') }}</summary>
              <div v-if="detail.payload.response_truncated" class="records-panel__payload-warning">
                {{ t('records.detail.responseTruncated') }}<span v-if="detail.payload.response_omitted_count">{{ t('records.detail.omitted', { n: detail.payload.response_omitted_count }) }}</span>{{ t('records.detail.sentenceEnd') }}
              </div>
              <div v-if="detail.payload.response_mode === 'reference'" class="records-panel__reference-note">
                {{ t('records.detail.referenceNote') }}
              </div>
              <div v-if="evidenceItems(detail).length" class="records-panel__evidence-list">
                <article v-for="(evidence, index) in evidenceItems(detail)" :key="evidenceKey(evidence, index)" class="records-panel__evidence">
                  <div class="records-panel__evidence-head">
                    <strong>{{ t('records.detail.evidence', { n: index + 1 }) }}</strong>
                    <code v-if="jsonString(evidence.ref)">{{ jsonString(evidence.ref) }}</code>
                    <span v-if="jsonString(evidence.type)">{{ jsonString(evidence.type) }}</span>
                  </div>
                  <p v-if="jsonString(evidence.content)" class="records-panel__evidence-content">{{ jsonString(evidence.content) }}</p>
                  <p v-if="evidenceSource(evidence)" class="records-panel__evidence-source">{{ t('records.detail.evidenceSource', { source: evidenceSource(evidence) }) }}</p>
                  <span v-if="evidence.truncated === true" class="records-panel__evidence-truncated">{{ t('records.detail.evidenceTruncated') }}</span>
                </article>
              </div>
              <details class="records-panel__raw-json">
                <summary>{{ t('records.detail.rawJson') }}</summary>
                <pre>{{ formatJson(responseValue(detail)) }}</pre>
              </details>
            </details>
          </div>
          <div v-else class="records-panel__payload-empty">{{ t('records.detail.payloadEmpty') }}</div>
        </template>
        <div v-else class="records-panel__notice records-panel__notice--error">
          <strong>{{ t('records.detail.loadFailed') }}</strong><span v-if="detailError">{{ t('records.detail.errorDetail', { error: detailError }) }}</span>
        </div>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRetrievalRecordsApi } from '@/api/retrievalRecords'
import { apiErrorDetail } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import { saveBlob } from '@/utils/download'
import { t } from '@/locales/t'
import StatsCard from '@/components/common/StatsCard.vue'
import { useRetrievalNames } from '@/components/retrieval/useRetrievalNames'
import {
  prefillCustomRange, rangeFilters, validateCustomRange,
  type RangePreset, type TimeRangeState,
} from '@/components/retrieval/timeRange'
import type {
  RetrievalOperation, RetrievalRecord, RetrievalRecordFilters, RetrievalRecordsSummary,
  RetrievalSource, RetrievalStatus,
} from '@/types/retrievalRecords'

const props = withDefaults(defineProps<{
  kbId?: string
  mcpKeyId?: string
  showSummary?: boolean
}>(), { kbId: '', mcpKeyId: '', showSummary: true })

/** 非响应式常量表只存文案 key，使用处再 t()——定义期翻译不会随切语言重算。 */
const STATUS_OPTIONS: Array<{ value: RetrievalStatus; labelKey: string }> = [
  { value: 'pending', labelKey: 'records.status.pending' }, { value: 'success', labelKey: 'records.status.success' },
  { value: 'no_result', labelKey: 'records.status.no_result' }, { value: 'denied', labelKey: 'records.status.denied' },
  { value: 'invalid', labelKey: 'records.status.invalid' }, { value: 'timeout', labelKey: 'records.status.timeout' },
  { value: 'failed', labelKey: 'records.status.failed' },
]

const api = useRetrievalRecordsApi()
const domainStore = useDomainStore()
// ID→名称映射：记录接口只回 ID，名称在这里解析（拉失败回落原始 ID）。
const { state: nameOptions, load: loadNames, kbName, paradigmName } = useRetrievalNames()
const summary = ref<RetrievalRecordsSummary | null>(null)
const records = ref<RetrievalRecord[]>([])
const nextCursor = ref<string | null>(null)
const cursorStack = ref<string[]>([])
const currentCursor = ref<string | undefined>()
const loading = ref(false)
const error = ref('')
const detail = ref<RetrievalRecord | null>(null)
const detailVisible = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
let loadGeneration = 0

type PanelFilters = TimeRangeState & {
  source: string; status: string; operation: string; tool: string
  kbId: string; actorUserId: string; paradigmId: string
}
const draft = reactive<PanelFilters>({
  days: 7, source: '', status: '', operation: '', tool: '',
  kbId: '', actorUserId: '', paradigmId: '', startDate: '', endDate: '',
})
const applied = reactive<PanelFilters>({
  days: 7, source: '', status: '', operation: '', tool: '',
  kbId: '', actorUserId: '', paradigmId: '', startDate: '', endDate: '',
})
const filterError = ref('')
const exporting = ref(false)
const exportNotice = ref('')

/** 切到"自定义"时预填近 7 天（共享逻辑见 timeRange.ts）。 */
function onRangePresetChange(): void {
  filterError.value = ''
  prefillCustomRange(draft)
}

function validateDraft(): string {
  return validateCustomRange(draft)
}

/**
 * 筛选下拉的选项 = 名称映射 ∪ 当前已加载记录里出现过的未知 ID。
 * 列表拉失败或库/范式已删除时，记录里仍会有它们的 ID——并入选项保证
 * "看到的 ID 永远筛得到"，不因换下拉而丢掉原先手填 ID 的能力。
 */
const kbFilterOptions = computed(() => {
  const known = new Set(nameOptions.kbOptions.map(option => option.id))
  const extras = [...new Set(records.value.flatMap(item => item.kb_ids))]
    .filter(id => id && !known.has(id))
    .map(id => ({ id, name: id }))
  return [...nameOptions.kbOptions, ...extras]
})

const paradigmFilterOptions = computed(() => {
  const known = new Set(nameOptions.paradigmOptions.map(option => option.id))
  const extras = [...new Set(records.value.map(item => item.paradigm_id).filter((id): id is string => !!id))]
    .filter(id => !known.has(id))
    .map(id => ({ id, name: id }))
  return [...nameOptions.paradigmOptions, ...extras]
})

function appliedFilters(): RetrievalRecordFilters {
  return {
    ...rangeFilters(applied),
    kbId: props.kbId || applied.kbId || undefined,
    mcpKeyId: props.mcpKeyId || undefined,
    source: applied.source as RetrievalSource | '',
    status: applied.status as RetrievalStatus | '',
    operation: applied.operation as RetrievalOperation | '',
    toolName: applied.tool || undefined,
    actorUserId: applied.actorUserId || undefined,
    paradigmId: applied.paradigmId || undefined,
  }
}

function filters(cursor = currentCursor.value): RetrievalRecordFilters {
  return {
    ...appliedFilters(), cursor, pageSize: 25,
  }
}

async function reload(): Promise<void> {
  const domain = domainStore.currentDomain
  if (!domain) return
  const generation = ++loadGeneration
  loading.value = true
  error.value = ''
  // 名称映射走自己的缓存与兜底，不并入下面的 Promise.all——它失败不该连累记录本体。
  void loadNames(domain)
  try {
    const [summaryValue, page] = await Promise.all([
      api.getSummary(domain, appliedFilters()),
      api.list(domain, filters()),
    ])
    if (generation !== loadGeneration || domain !== domainStore.currentDomain) return
    summary.value = summaryValue
    records.value = page.items
    nextCursor.value = page.next_cursor
  } catch (reason) {
    if (generation !== loadGeneration) return
    error.value = await apiErrorDetail(reason)
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

function applyFilters(): void {
  // 校验失败只提示不请求，applied 保持旧值，列表维持上一次生效的筛选
  filterError.value = validateDraft()
  if (filterError.value) return
  Object.assign(applied, draft)
  cursorStack.value = []
  currentCursor.value = undefined
  void reload()
}

function resetFilters(): void {
  Object.assign(draft, {
    days: 7 as RangePreset, source: '', status: '', operation: '', tool: '',
    kbId: '', actorUserId: '', paradigmId: '', startDate: '', endDate: '',
  })
  applyFilters()
}

// ── 导出：当前筛选条件下拉全量（上限后端兜底），名称解析后拼 CSV ──

/** CSV 表头跟随界面语言：导出时求值，不在模块常量里缓存译文。 */
function exportHeaders(): string[] {
  return [
    t('records.column.time'), t('records.column.user'), t('records.source.label'), t('records.column.toolOrOperation'),
    t('records.column.queryOrAction'), t('records.column.targetKb'),
    t('records.paradigm.label'), t('common.status'), t('records.column.resultCount'), t('records.export.durationMs'),
    t('records.column.errorCode'), t('records.column.callId'),
  ]
}

/** 标准 CSV 转义：含逗号/引号/换行的单元格包引号，引号翻倍。 */
function csvCell(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value
}

function exportStamp(): string {
  const now = new Date()
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

function exportRow(item: RetrievalRecord): string[] {
  return [
    formatTime(item.occurred_at),
    item.actor_username || '',
    sourceLabel(item.source),
    item.tool_name || operationLabel(item.operation),
    actionSummary(item),
    item.kb_ids.length ? item.kb_ids.map(kbName).join(t('records.list.separator')) : '',
    item.paradigm_id
      ? `${paradigmName(item.paradigm_id)}${item.paradigm_version == null ? '' : ` v${item.paradigm_version}`}`
      : '',
    statusLabel(item.status),
    item.result_count == null ? '' : String(item.result_count),
    item.duration_ms == null ? '' : String(Math.round(item.duration_ms)),
    item.error_code || '',
    item.id,
  ]
}

async function exportCsv(): Promise<void> {
  const domain = domainStore.currentDomain
  if (!domain || exporting.value) return
  exporting.value = true
  exportNotice.value = ''
  filterError.value = ''
  try {
    const result = await api.exportAll(domain, appliedFilters())
    if (!result.items.length) {
      exportNotice.value = t('records.export.empty')
      return
    }
    const lines = [exportHeaders().map(csvCell).join(',')]
    for (const item of result.items) lines.push(exportRow(item).map(csvCell).join(','))
    // BOM 让 Excel 直接以 UTF-8 打开中文不乱码
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
    saveBlob(blob, t('records.export.filename', { stamp: exportStamp() }))
    if (result.truncated) exportNotice.value = t('records.export.truncated', { limit: result.row_limit })
  } catch (reason) {
    filterError.value = await apiErrorDetail(reason)
  } finally {
    exporting.value = false
  }
}

function nextPage(): void {
  if (!nextCursor.value) return
  cursorStack.value = [...cursorStack.value, currentCursor.value ?? '']
  currentCursor.value = nextCursor.value
  void reload()
}

function previousPage(): void {
  if (!cursorStack.value.length) return
  const previous = cursorStack.value[cursorStack.value.length - 1]
  cursorStack.value = cursorStack.value.slice(0, -1)
  currentCursor.value = previous || undefined
  void reload()
}

async function openDetail(id: string): Promise<void> {
  const domain = domainStore.currentDomain
  if (!domain) return
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  detailError.value = ''
  try {
    detail.value = await api.getOne(domain, id)
  } catch (reason) {
    detailError.value = await apiErrorDetail(reason)
  } finally {
    detailLoading.value = false
  }
}

function closeDetail(): void { detailVisible.value = false }
function formatTime(value: string): string { return value.replace('T', ' ').slice(0, 19) }
function formatDuration(value?: number | null): string { return value == null ? '—' : `${Math.round(value)} ms` }
function formatRate(value?: number | null): string { return value == null ? '—' : `${(value * 100).toFixed(1)}%` }
function sourceLabel(value: RetrievalSource): string { return ({ web: t('records.source.web'), mcp: 'MCP', api: 'API' })[value] }
function operationLabel(value: string): string {
  return ({
    search: t('records.operation.search'), read: t('records.operation.read'),
    upload: t('records.operation.upload'), replace: t('records.operation.replace'),
  } as Record<string, string>)[value] ?? value
}
function statusLabel(value: RetrievalStatus): string {
  const labelKey = STATUS_OPTIONS.find(item => item.value === value)?.labelKey
  return labelKey ? t(labelKey) : value
}
function paradigmText(item: RetrievalRecord): string { return item.paradigm_id ? `${paradigmName(item.paradigm_id)}${item.paradigm_version == null ? '' : ` v${item.paradigm_version}`}` : '—' }
function detailText(item: RetrievalRecord, key: string): string | null {
  const value = item.details_json?.[key]
  return typeof value === 'string' && value ? value : null
}
function actionSummary(item: RetrievalRecord): string {
  if (item.query_text) return item.query_text
  const detail = [detailText(item, 'action'), detailText(item, 'ref_type')].filter(Boolean)
  return detail.length ? detail.join(' · ') : operationLabel(item.operation)
}

type JsonObject = Record<string, unknown>

function jsonObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : null
}

function jsonString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function evidenceItems(item: RetrievalRecord): JsonObject[] {
  if (item.operation !== 'search' || item.payload?.response_mode !== 'snapshot') return []
  const response = jsonObject(item.payload.response_json ?? undefined)
  const evidenceResponse = jsonObject(response?.evidenceResponse)
  const evidence = response?.evidence ?? evidenceResponse?.evidence
  return Array.isArray(evidence)
    ? evidence.map(jsonObject).filter((value): value is JsonObject => value !== null)
    : []
}

function evidenceKey(evidence: JsonObject, index: number): string {
  return jsonString(evidence.ref) || `evidence-${index}`
}

function evidenceSource(evidence: JsonObject): string {
  const source = jsonObject(evidence.source)
  if (!source) return ''
  return [source.file_name, source.section, source.page, source.document_ref]
    .filter(value => typeof value === 'string' || typeof value === 'number')
    .map(String)
    .join(' · ')
}

function responseValue(item: RetrievalRecord): unknown {
  const payload = item.payload
  if (!payload) return null
  return payload.response_mode === 'reference'
    ? payload.response_refs_json
    : payload.response_json
}

function formatJson(value: unknown): string {
  return value === undefined || value === null ? '—' : JSON.stringify(value, null, 2)
}
/** 详情白名单字段 → 文案 key（常量表只存 key，取用时 t()）。 */
const DETAIL_FIELD_LABEL_KEYS = {
  action: 'records.detailField.action', ref_type: 'records.detailField.ref_type', file_count: 'records.detailField.file_count',
  uploaded_count: 'records.detailField.uploaded_count', failed_count: 'records.detailField.failed_count',
} as const
function safeDetailFields(item: RetrievalRecord): Array<{ key: string; label: string; value: string }> {
  return Object.entries(DETAIL_FIELD_LABEL_KEYS).flatMap(([key, labelKey]) => {
    const value = item.details_json?.[key]
    return typeof value === 'string' || typeof value === 'number'
      ? [{ key, label: t(labelKey), value: String(value) }]
      : []
  })
}

onMounted(reload)
// 切域必须清 kbId 筛选：旧域的库 ID 在新域下拉里没有对应选项，留着就成了
// 一个 UI 显示不出来也清不掉的隐形过滤器（范式跨域通用，无需清）。
watch(() => domainStore.currentDomain, () => { cursorStack.value = []; currentCursor.value = undefined; draft.kbId = ''; applied.kbId = ''; void reload() })
watch(() => [props.kbId, props.mcpKeyId], () => { cursorStack.value = []; currentCursor.value = undefined; void reload() })
</script>

<style scoped>
.records-panel { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.records-panel__summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.records-panel__filters { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px; border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius); background: var(--kb-bg-card); }
.records-panel__input, .records-panel__select { min-height: 34px; padding: 0 10px; border: 1px solid var(--kb-border); border-radius: 6px; color: var(--kb-text-primary); background: var(--kb-bg-card); }
.records-panel__input { flex: 1 1 220px; }.records-panel__input--tool { flex: 0 1 180px; }
.records-panel__input--date { flex: 0 0 150px; }.records-panel__range-sep { align-self: center; color: var(--kb-text-tertiary); font-size: 12px; }
.records-panel__filter-error { flex-basis: 100%; margin: 0; color: var(--kb-danger); font-size: 12px; }
.records-panel__filter-notice { flex-basis: 100%; margin: 0; color: var(--kb-warning); font-size: 12px; }
.records-panel__button { min-height: 34px; padding: 0 14px; border: 1px solid var(--kb-accent); border-radius: 6px; color: #fff; background: var(--kb-accent); cursor: pointer; }
.records-panel__button--plain { color: var(--kb-text-secondary); background: var(--kb-bg-card); border-color: var(--kb-border); }
.records-panel__button:disabled { opacity: .45; cursor: not-allowed; }
.records-panel__notice, .records-panel__empty { padding: 24px; text-align: center; color: var(--kb-text-tertiary); border: 1px dashed var(--kb-border); border-radius: var(--kb-radius); }
.records-panel__empty { display: flex; flex-direction: column; gap: 6px; }.records-panel__empty strong { color: var(--kb-text-primary); }
.records-panel__notice--error { color: var(--kb-danger); }.records-panel__table-wrap { overflow-x: auto; border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius); background: var(--kb-bg-card); }
.records-panel__table { width: 100%; min-width: 1060px; border-collapse: collapse; font-size: 12px; }.records-panel__table th, .records-panel__table td { padding: 10px 12px; border-bottom: 1px solid var(--kb-border-light); text-align: left; vertical-align: middle; }.records-panel__table th { color: var(--kb-text-tertiary); font-weight: 600; background: var(--el-fill-color-extra-light); }.records-panel__time { white-space: nowrap; }.records-panel__query { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.records-panel__status { white-space: nowrap; }.records-panel__status.is-failed, .records-panel__status.is-timeout, .records-panel__status.is-denied { color: var(--kb-danger); }.records-panel__status.is-no_result, .records-panel__status.is-invalid { color: var(--kb-warning); }.records-panel__status.is-success { color: var(--kb-success); }
.records-panel__link { padding: 0; border: 0; color: var(--kb-accent); background: transparent; cursor: pointer; white-space: nowrap; }.records-panel__pager { display: flex; justify-content: flex-end; align-items: center; gap: 8px; color: var(--kb-text-tertiary); font-size: 12px; }
.records-panel__backdrop { position: fixed; inset: 0; z-index: 500; background: rgba(15, 23, 42, .32); }.records-panel__drawer { position: absolute; top: 0; right: 0; bottom: 0; width: min(560px, 92vw); padding: 20px; overflow-y: auto; background: var(--kb-bg-card); box-shadow: -8px 0 24px rgba(15, 23, 42, .18); }.records-panel__drawer-head { display: flex; align-items: center; justify-content: space-between; }.records-panel__drawer-head h3 { margin: 0; }.records-panel__close { border: 0; background: transparent; font-size: 24px; cursor: pointer; }.records-panel__details { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 12px; margin-top: 24px; font-size: 13px; }.records-panel__details dt { color: var(--kb-text-tertiary); }.records-panel__details dd { margin: 0; color: var(--kb-text-primary); overflow-wrap: anywhere; }
.records-panel__payload { display: flex; flex-direction: column; gap: 10px; margin-top: 22px; }
.records-panel__payload-section { border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius-sm); background: var(--el-fill-color-extra-light); }
.records-panel__payload-section > summary, .records-panel__raw-json > summary { padding: 10px 12px; color: var(--kb-text-primary); font-weight: 650; cursor: pointer; }
.records-panel__payload-section > pre, .records-panel__raw-json > pre { max-height: 360px; margin: 0; padding: 12px; overflow: auto; border-top: 1px solid var(--kb-border-light); color: var(--kb-text-secondary); background: var(--kb-bg-card); font-size: 12px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
.records-panel__payload-warning { margin: 0 12px 10px; padding: 9px 11px; border: 1px solid var(--kb-warning); border-radius: 6px; color: var(--kb-text-primary); background: var(--kb-warning-soft); font-size: 12px; }
.records-panel__reference-note, .records-panel__payload-empty { margin-top: 16px; padding: 12px; border: 1px dashed var(--kb-border); border-radius: var(--kb-radius-sm); color: var(--kb-text-tertiary); font-size: 12px; line-height: 1.6; }
.records-panel__reference-note { margin: 0 12px 10px; }
.records-panel__evidence-list { display: flex; flex-direction: column; gap: 8px; padding: 0 12px 12px; }
.records-panel__evidence { padding: 12px; border: 1px solid var(--kb-border-light); border-radius: 8px; background: var(--kb-bg-card); }
.records-panel__evidence-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 12px; }
.records-panel__evidence-head span, .records-panel__evidence-truncated { color: var(--kb-warning); }
.records-panel__evidence-content { margin: 10px 0 0; color: var(--kb-text-primary); line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }
.records-panel__evidence-source { margin: 8px 0 0; color: var(--kb-text-tertiary); font-size: 12px; }
.records-panel__evidence-truncated { display: inline-block; margin-top: 8px; font-size: 12px; }
.records-panel__raw-json { margin: 0 12px 12px; border-top: 1px solid var(--kb-border-light); }
</style>
