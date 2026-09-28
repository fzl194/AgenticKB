<template>
  <section class="records-panel">
    <div v-if="showSummary" class="records-panel__summary">
      <StatsCard label="调用量" :value="summary?.summary.total_calls ?? '—'" icon="🔍" />
      <StatsCard label="零结果率" :value="formatRate(summary?.summary.no_result_rate)" icon="🕳" />
      <StatsCard label="失败率" :value="formatRate(summary?.summary.failure_rate)" icon="⚠" />
      <StatsCard label="P95 延迟" :value="formatDuration(summary?.summary.p95_duration_ms)" icon="⏱" />
    </div>

    <form class="records-panel__filters" @submit.prevent="applyFilters">
      <select v-model.number="draft.days" class="records-panel__select" aria-label="时间范围">
        <option :value="7">近 7 天</option>
        <option :value="30">近 30 天</option>
        <option :value="90">近 90 天</option>
      </select>
      <select v-model="draft.source" class="records-panel__select" aria-label="来源">
        <option value="">全部来源</option>
        <option value="web">网页</option>
        <option value="mcp">MCP</option>
        <option value="api">API</option>
      </select>
      <select v-model="draft.status" class="records-panel__select" aria-label="状态">
        <option value="">全部状态</option>
        <option v-for="item in STATUS_OPTIONS" :key="item.value" :value="item.value">{{ item.label }}</option>
      </select>
      <select v-model="draft.operation" class="records-panel__select" aria-label="操作">
        <option value="">全部操作</option><option value="search">检索</option><option value="read">读取</option><option value="upload">上传</option>
      </select>
      <input v-model.trim="draft.tool" class="records-panel__input records-panel__input--tool" placeholder="Tool 名称" aria-label="Tool 名称" />
      <input v-if="!kbId" v-model.trim="draft.kbId" class="records-panel__input records-panel__input--tool" placeholder="知识库 ID" aria-label="知识库 ID" />
      <input v-model.trim="draft.actorUserId" class="records-panel__input records-panel__input--tool" placeholder="用户 ID" aria-label="用户 ID" />
      <input v-model.trim="draft.paradigmId" class="records-panel__input records-panel__input--tool" placeholder="范式 ID" aria-label="范式 ID" />
      <button type="submit" class="records-panel__button">筛选</button>
      <button type="button" class="records-panel__button records-panel__button--plain" @click="resetFilters">重置</button>
    </form>

    <div v-if="error" class="records-panel__notice records-panel__notice--error">
      {{ error }}
      <button type="button" class="records-panel__link" @click="reload">重试</button>
    </div>
    <div v-else-if="loading && !records.length" class="records-panel__notice">正在加载检索记录…</div>
    <div v-else-if="!records.length" class="records-panel__empty">
      <strong>还没有检索记录</strong>
      <span>统一记录从本版本上线后开始记录，旧 Java 日志不会迁入这里。</span>
    </div>
    <div v-else class="records-panel__table-wrap">
      <table class="records-panel__table">
        <thead><tr><th>时间</th><th>用户</th><th>来源</th><th>Tool / 操作</th><th>查询或动作</th><th>目标知识库</th><th>范式</th><th>状态</th><th>结果</th><th>耗时</th><th></th></tr></thead>
        <tbody>
          <tr v-for="item in records" :key="item.id">
            <td class="records-panel__time">{{ formatTime(item.occurred_at) }}</td>
            <td>{{ item.actor_username || '—' }}</td>
            <td>{{ sourceLabel(item.source) }}</td>
            <td>{{ item.tool_name || operationLabel(item.operation) }}</td>
            <td class="records-panel__query" :title="actionSummary(item)">{{ actionSummary(item) }}</td>
            <td>{{ item.kb_ids.length ? item.kb_ids.join('、') : '—' }}</td>
            <td>{{ paradigmText(item) }}</td>
            <td><span class="records-panel__status" :class="`is-${item.status}`">{{ statusLabel(item.status) }}</span></td>
            <td>{{ item.result_count ?? '—' }}</td>
            <td>{{ formatDuration(item.duration_ms) }}</td>
            <td><button type="button" class="records-panel__link" :data-test="`record-detail-${item.id}`" @click="openDetail(item.id)">详情</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="records.length" class="records-panel__pager">
      <span>当前页 {{ records.length }} 条</span>
      <button type="button" class="records-panel__button records-panel__button--plain" :disabled="!cursorStack.length || loading" @click="previousPage">上一页</button>
      <button type="button" class="records-panel__button records-panel__button--plain" :disabled="!nextCursor || loading" @click="nextPage">下一页</button>
    </div>

    <div v-if="detailVisible" class="records-panel__backdrop" @click.self="closeDetail">
      <aside class="records-panel__drawer" role="dialog" aria-modal="true" aria-label="调用详情">
        <div class="records-panel__drawer-head"><h3>调用详情</h3><button type="button" aria-label="关闭" class="records-panel__close" @click="closeDetail">×</button></div>
        <div v-if="detailLoading" class="records-panel__notice">正在加载…</div>
        <template v-else-if="detail">
          <dl class="records-panel__details">
            <dt>调用 ID</dt><dd><code>{{ detail.id }}</code></dd>
            <dt>发生时间</dt><dd>{{ formatTime(detail.occurred_at) }}</dd>
            <dt>用户</dt><dd>{{ detail.actor_username || '—' }}</dd>
            <dt>来源</dt><dd>{{ sourceLabel(detail.source) }}</dd>
            <dt>Tool / 操作</dt><dd>{{ detail.tool_name || operationLabel(detail.operation) }}</dd>
            <dt>查询内容</dt><dd>{{ detail.query_text || '—' }}</dd>
            <dt>目标知识库</dt><dd>{{ detail.kb_ids.join('、') || '—' }}</dd>
            <dt>范式</dt><dd>{{ paradigmText(detail) }}</dd>
            <dt>状态</dt><dd>{{ statusLabel(detail.status) }}</dd>
            <dt>结果数</dt><dd>{{ detail.result_count ?? '—' }}</dd>
            <dt>总耗时</dt><dd>{{ formatDuration(detail.duration_ms) }}</dd>
            <dt>错误码</dt><dd>{{ detail.error_code || '—' }}</dd>
            <template v-for="field in safeDetailFields(detail)" :key="field.key">
              <dt>{{ field.label }}</dt><dd>{{ field.value }}</dd>
            </template>
          </dl>

          <div v-if="detail.payload" class="records-panel__payload">
            <details open class="records-panel__payload-section">
              <summary>调用参数</summary>
              <pre>{{ formatJson(detail.payload.request_json) }}</pre>
            </details>
            <details open class="records-panel__payload-section">
              <summary>有效执行参数</summary>
              <pre>{{ formatJson(detail.payload.effective_context_json) }}</pre>
            </details>
            <details open class="records-panel__payload-section">
              <summary>返回内容</summary>
              <div v-if="detail.payload.response_truncated" class="records-panel__payload-warning">
                返回快照已截断<span v-if="detail.payload.response_omitted_count">，省略 {{ detail.payload.response_omitted_count }} 项</span>。
              </div>
              <div v-if="detail.payload.response_mode === 'reference'" class="records-panel__reference-note">
                此操作读取已有文档或章节，正文未重复保存；下方仅保留可回查引用。
              </div>
              <div v-if="evidenceItems(detail).length" class="records-panel__evidence-list">
                <article v-for="(evidence, index) in evidenceItems(detail)" :key="evidenceKey(evidence, index)" class="records-panel__evidence">
                  <div class="records-panel__evidence-head">
                    <strong>证据 {{ index + 1 }}</strong>
                    <code v-if="jsonString(evidence.ref)">{{ jsonString(evidence.ref) }}</code>
                    <span v-if="jsonString(evidence.type)">{{ jsonString(evidence.type) }}</span>
                  </div>
                  <p v-if="jsonString(evidence.content)" class="records-panel__evidence-content">{{ jsonString(evidence.content) }}</p>
                  <p v-if="evidenceSource(evidence)" class="records-panel__evidence-source">来源：{{ evidenceSource(evidence) }}</p>
                  <span v-if="evidence.truncated === true" class="records-panel__evidence-truncated">该条证据内容已截断</span>
                </article>
              </div>
              <details class="records-panel__raw-json">
                <summary>原始 JSON</summary>
                <pre>{{ formatJson(responseValue(detail)) }}</pre>
              </details>
            </details>
          </div>
          <div v-else class="records-panel__payload-empty">该记录没有留存调用参数与返回快照。</div>
        </template>
        <div v-else class="records-panel__notice records-panel__notice--error">
          <strong>详情加载失败</strong><span v-if="detailError">：{{ detailError }}</span>
        </div>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useRetrievalRecordsApi } from '@/api/retrievalRecords'
import { apiErrorDetail } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import StatsCard from '@/components/common/StatsCard.vue'
import type {
  RetrievalOperation, RetrievalRecord, RetrievalRecordFilters, RetrievalRecordsSummary,
  RetrievalSource, RetrievalStatus,
} from '@/types/retrievalRecords'

const props = withDefaults(defineProps<{
  kbId?: string
  mcpKeyId?: string
  showSummary?: boolean
}>(), { kbId: '', mcpKeyId: '', showSummary: true })

const STATUS_OPTIONS: Array<{ value: RetrievalStatus; label: string }> = [
  { value: 'pending', label: '处理中' }, { value: 'success', label: '成功' },
  { value: 'no_result', label: '零结果' }, { value: 'denied', label: '无权限' },
  { value: 'invalid', label: '参数错误' }, { value: 'timeout', label: '超时' },
  { value: 'failed', label: '失败' },
]

const api = useRetrievalRecordsApi()
const domainStore = useDomainStore()
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

const draft = reactive({ days: 7, source: '', status: '', operation: '', tool: '', kbId: '', actorUserId: '', paradigmId: '' })
const applied = reactive({ days: 7, source: '', status: '', operation: '', tool: '', kbId: '', actorUserId: '', paradigmId: '' })

function appliedFilters(): RetrievalRecordFilters {
  return {
    days: applied.days,
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
  Object.assign(applied, draft)
  cursorStack.value = []
  currentCursor.value = undefined
  void reload()
}

function resetFilters(): void {
  Object.assign(draft, { days: 7, source: '', status: '', operation: '', tool: '', kbId: '', actorUserId: '', paradigmId: '' })
  applyFilters()
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
function sourceLabel(value: RetrievalSource): string { return ({ web: '网页', mcp: 'MCP', api: 'API' })[value] }
function operationLabel(value: string): string { return ({ search: '检索', read: '读取', upload: '上传' } as Record<string, string>)[value] ?? value }
function statusLabel(value: RetrievalStatus): string { return STATUS_OPTIONS.find(item => item.value === value)?.label ?? value }
function paradigmText(item: RetrievalRecord): string { return item.paradigm_id ? `${item.paradigm_id}${item.paradigm_version == null ? '' : ` v${item.paradigm_version}`}` : '—' }
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
const DETAIL_FIELD_LABELS = {
  action: '动作', ref_type: '引用类型', file_count: '文件数',
  uploaded_count: '上传成功数', failed_count: '失败数',
} as const
function safeDetailFields(item: RetrievalRecord): Array<{ key: string; label: string; value: string }> {
  return Object.entries(DETAIL_FIELD_LABELS).flatMap(([key, label]) => {
    const value = item.details_json?.[key]
    return typeof value === 'string' || typeof value === 'number'
      ? [{ key, label, value: String(value) }]
      : []
  })
}

onMounted(reload)
watch(() => domainStore.currentDomain, () => { cursorStack.value = []; currentCursor.value = undefined; void reload() })
watch(() => [props.kbId, props.mcpKeyId], () => { cursorStack.value = []; currentCursor.value = undefined; void reload() })
</script>

<style scoped>
.records-panel { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.records-panel__summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.records-panel__filters { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px; border: 1px solid var(--kb-border-light); border-radius: var(--kb-radius); background: var(--kb-bg-card); }
.records-panel__input, .records-panel__select { min-height: 34px; padding: 0 10px; border: 1px solid var(--kb-border); border-radius: 6px; color: var(--kb-text-primary); background: var(--kb-bg-card); }
.records-panel__input { flex: 1 1 220px; }.records-panel__input--tool { flex: 0 1 180px; }
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
