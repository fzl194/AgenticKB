<template>
  <div class="doc-detail" v-loading="miningStore.loading">
    <div class="doc-detail__back">
      <el-button text @click="$router.push(`/kb/${props.kbId}/run/${props.runId}`)">
        <el-icon><ArrowLeft /></el-icon> {{ t('kbView.runDoc.back') }}
      </el-button>
    </div>

    <div v-if="miningStore.error && !miningStore.currentDocument" class="doc-detail__load-error">
      <p>{{ t('kbView.runDoc.loadFailed', { error: miningStore.error }) }}</p>
      <el-button size="small" @click="loadAll">{{ t('common.retry') }}</el-button>
    </div>

    <template v-if="miningStore.currentDocument">
      <!-- Info Card -->
      <div class="doc-detail__info-card">
        <div class="doc-detail__info-left">
          <h3 class="doc-detail__name">{{ docDisplayName }}</h3>
          <StatusBadge :status="miningStore.currentDocument.status" size="small">
            {{ docStatusLabel(miningStore.currentDocument.status) }}
          </StatusBadge>
          <span class="action-badge" :class="`action-badge--${miningStore.currentDocument.action}`">
            {{ actionLabel(miningStore.currentDocument.action) }}
          </span>
        </div>
        <div class="doc-detail__info-right">
          <div class="metric" v-if="docDuration">
            <span class="metric__value">{{ docDuration }}</span>
            <span class="metric__label">{{ t('common.duration') }}</span>
          </div>
          <div class="metric" v-if="miningStore.currentDocument.error_message">
            <span class="metric__value metric__value--danger">{{ t('kbView.runDoc.hasErrors') }}</span>
          </div>
        </div>
      </div>

      <!-- Error Banner -->
      <div v-if="miningStore.currentDocument.error_message" class="doc-detail__error-banner">
        {{ miningStore.currentDocument.error_message }}
      </div>

      <PreprocessNotice
        :status="miningStore.currentDocument.preprocess_status"
        :error-code="miningStore.currentDocument.error_code"
        :error-detail="miningStore.currentDocument.error_detail"
        :warnings="miningStore.currentDocument.warnings"
        :summary="miningStore.currentDocument.excel_summary"
      />

      <!-- Skip Reason Banner -->
      <div
        v-if="skipReasonText"
        class="doc-detail__skip-banner"
        :class="{ 'doc-detail__skip-banner--faulty': skipIsFaulty }"
      >
        <el-icon><WarningFilled v-if="skipIsFaulty" /><InfoFilled v-else /></el-icon>
        <div class="skip-banner__body">
          <div><strong>{{ t('kbView.runDoc.skipReason') }}</strong>{{ skipReasonText }}</div>
          <div v-if="skipReasonDetail" class="skip-banner__detail">{{ skipReasonDetail }}</div>
        </div>
      </div>

      <!-- Stage Timeline -->
      <div class="doc-detail__section">
        <h4 class="section-label">{{ t('kbView.runDoc.stageTimeline') }}</h4>
        <div class="stage-timeline">
          <div
            v-for="(stage, idx) in mergedStages"
            :key="stage.id"
            class="stage-item"
            :class="`stage-item--${stage.status}`"
          >
            <div class="stage-item__line" v-if="idx < mergedStages.length - 1" />
            <div class="stage-item__dot" />
            <div class="stage-item__content">
              <div class="stage-item__header">
                <span class="stage-item__name">{{ stageLabel(stage.stage || '') }}</span>
                <StatusBadge :status="mapStageStatus(stage.status)" size="small">
                  {{ t(stage.status === 'completed' ? 'kbView.runDoc.docStatus.committed'
                    : stage.status === 'failed' ? 'common.docStatus.failed'
                      : stage.status === 'started' ? 'kbView.runDoc.stageInProgress' : 'kbView.runDoc.docStatus.skipped') }}
                </StatusBadge>
              </div>
              <div class="stage-item__meta">
                <span v-if="stage.duration_ms" class="stage-item__duration">{{ formatMs(stage.duration_ms) }}</span>
                <span class="stage-item__time">{{ formatTime(stage.created_at) }}</span>
              </div>
              <div v-if="stage.error_message" class="stage-item__error">{{ stage.error_message }}</div>
              <div v-if="stage.output_summary" class="stage-item__summary">{{ stage.output_summary }}</div>
            </div>
          </div>
          <div v-if="mergedStages.length === 0" class="stage-empty">{{ t('kbView.runDoc.noStageData') }}</div>
        </div>
      </div>

      <!-- Artifact Summary -->
      <div class="doc-detail__section" v-if="miningStore.documentArtifacts">
        <h4 class="section-label">{{ t('kbView.runDoc.artifactSummary') }}</h4>
        <div class="artifact-stats">
          <div class="artifact-stat">
            <span class="artifact-stat__value">{{ miningStore.documentArtifacts.segment_count }}</span>
            <span class="artifact-stat__label">{{ t('kbView.runDoc.rawSegments') }}</span>
          </div>
          <div class="artifact-stat">
            <span class="artifact-stat__value">{{ miningStore.documentArtifacts.unit_count }}</span>
            <span class="artifact-stat__label">{{ t('dashboard.tile.units') }}</span>
          </div>
        </div>
      </div>

      <!-- Artifact Tabs -->
      <div class="doc-detail__section">
        <div class="section-header">
          <h4 class="section-label" style="margin-bottom: 0">{{ t('kbView.runDoc.knowledgeArtifacts') }}</h4>
          <div class="artifact-filters">
            <button
              v-for="f in artifactTabs"
              :key="f.key"
              class="filter-tag"
              :class="{ 'filter-tag--active': activeArtifactTab === f.key }"
              @click="activeArtifactTab = f.key"
            >{{ t(f.labelKey) }}</button>
          </div>
        </div>

        <!-- Segments Table -->
        <el-table
          v-if="activeArtifactTab === 'segments'"
          :data="segments"
          class="kb-table"
          :header-cell-style="{ background: 'transparent' }"
          v-loading="artifactsLoading"
        >
          <el-table-column label="#" width="60" prop="segment_index" />
          <el-table-column :label="t('common.type')" width="100" prop="block_type" />
          <el-table-column :label="t('kbView.runDoc.column.role')" width="100" prop="semantic_role" />
          <el-table-column :label="t('kbView.runDoc.column.title')" min-width="150" prop="section_title" />
          <el-table-column :label="t('kbView.runDoc.column.content')" min-width="300">
            <template #default="{ row }">
              <span class="text-preview expandable" :class="{ 'is-expanded': expandedKeys.has(`seg-${row.segment_index}`) }" @click="toggleExpand(`seg-${row.segment_index}`)">{{ row.raw_text || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="Token" width="80" prop="token_count" />
        </el-table>
        <div class="tab-pagination" v-if="segTotal > PAGE_SIZE">
          <el-pagination v-model:current-page="segPage" :page-size="PAGE_SIZE" :total="segTotal" layout="prev, pager, next" size="small" />
        </div>
        <el-table
          v-if="activeArtifactTab === 'units'"
          :data="units"
          class="kb-table"
          :header-cell-style="{ background: 'transparent' }"
          v-loading="artifactsLoading"
        >
          <el-table-column :label="t('common.type')" width="120" prop="unit_type" />
          <el-table-column :label="t('kbView.runDoc.column.structuralContext')" min-width="200" prop="title" />
          <el-table-column :label="t('kbView.runDoc.column.content')" min-width="300">
            <template #default="{ row }">
              <span class="text-preview expandable" :class="{ 'is-expanded': expandedKeys.has(`unit-${row.id ?? row.title}`) }" @click="toggleExpand(`unit-${row.id ?? row.title}`)">{{ row.text || '-' }}</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="tab-pagination" v-if="unitTotal > PAGE_SIZE">
          <el-pagination v-model:current-page="unitPage" :page-size="PAGE_SIZE" :total="unitTotal" layout="prev, pager, next" size="small" />
        </div>
        <!-- Raw Content -->
        <div v-if="activeArtifactTab === 'raw-content'" v-loading="rawLoading" class="raw-content-wrapper">
          <div v-if="rawError" class="raw-content-error">
            <el-icon><WarningFilled /></el-icon>
            {{ rawError }}
          </div>
          <div v-else-if="rawHtml" class="raw-content-body" v-html="rawHtml" />
          <div v-else-if="!rawLoading" class="stage-empty">{{ t('kbView.runDoc.noRawContent') }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { ArrowLeft, WarningFilled, InfoFilled } from '@element-plus/icons-vue'
import { useMiningStore } from '@/stores/mining'
import { useMiningApi } from '@/api/mining'
import StatusBadge from '@/components/common/StatusBadge.vue'
import PreprocessNotice from '@/components/kb/PreprocessNotice.vue'
import { miningOperatorLabel } from '@/utils/miningWorkflowPresentation'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { t } from '@/locales/t'

const props = defineProps<{ kbId: string; runId: string; docId: string }>()
const miningStore = useMiningStore()
const miningApi = useMiningApi()

const activeArtifactTab = ref('segments')
const artifactsLoading = ref(false)
const expandedKeys = ref(new Set<string>())
const PAGE_SIZE = 50

// Pagination state per tab
const segTotal = ref(0)
const segPage = ref(1)
const unitTotal = ref(0)
const unitPage = ref(1)

// Raw content
const rawLoading = ref(false)
const rawHtml = ref('')
const rawError = ref('')

function toggleExpand(key: string) {
  if (expandedKeys.value.has(key)) {
    expandedKeys.value.delete(key)
  } else {
    expandedKeys.value.add(key)
  }
  expandedKeys.value = new Set(expandedKeys.value)
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const segments = ref<any[]>([])
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const units = ref<any[]>([])
// eslint-disable-next-line @typescript-eslint/no-explicit-any

/** 产物 Tab：非响应式常量表只存 labelKey，渲染期经 t() 翻译（切语言即重算——R8） */
const artifactTabs = [
  { key: 'segments', labelKey: 'kbView.runDoc.rawSegments' },
  { key: 'units', labelKey: 'dashboard.tile.units' },
  { key: 'raw-content', labelKey: 'kbView.runDoc.rawText' },
]

// ── Computed ──

// Merge adjacent started+completed events for the same stage into one row
const mergedStages = computed(() => {
  const raw = miningStore.documentStages
  const result: typeof raw = []
  for (let i = 0; i < raw.length; i++) {
    const cur = raw[i]
    const next = i + 1 < raw.length ? raw[i + 1] : null
    if (
      cur.status === 'started' &&
      next &&
      next.stage === cur.stage &&
      next.status === 'completed'
    ) {
      result.push({ ...next })
      i++
    } else {
      result.push(cur)
    }
  }
  return result
})

const docDisplayName = computed(() => {
  const doc = miningStore.currentDocument
  if (!doc) return '-'
  if (doc.document_name) return doc.document_name
  const dk = doc.document_key as string | undefined
  if (dk?.startsWith('doc:/')) return dk.replace('doc:/', '')
  return dk || doc.id || '-'
})

const docDuration = computed(() => {
  const doc = miningStore.currentDocument
  if (!doc || !doc.started_at) return null
  const s = new Date(doc.started_at as string).getTime()
  const e = doc.finished_at ? new Date(doc.finished_at as string).getTime() : Date.now()
  const diff = Math.round((e - s) / 1000)
  if (diff < 60) return `${diff}s`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`
  return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`
})

// 跳过原因：后端在 mining_run_documents.metadata_json.skip_reason 里记录机器码，
// 这里翻译成界面文案。老数据没有该字段 → 按状态给出兜底说明。
// 非响应式常量表只存文案 key，computed 内经 t() 翻译（切语言即重算——R8）。
const SKIP_REASON_LABEL_KEYS: Record<string, string> = {
  // 复用已有成果 —— 正常，无需处理
  unchanged: 'kbView.runDoc.skip.unchanged',
  restored: 'kbView.runDoc.skip.restored',
  // 系统没能处理它 —— 属于故障，需排查
  preprocess_failed: 'kbView.runDoc.skip.preprocess_failed',
  parser_failed: 'kbView.runDoc.skip.parser_failed',
  unsupported_type: 'kbView.runDoc.skip.unsupported_type',
  // 文件本身没内容 —— 通常无需处理
  empty_file: 'kbView.runDoc.skip.empty_file',
  no_segments: 'kbView.runDoc.skip.no_segments',
  // 兜底：解析未产出内容但不属于以上任何一种
  parse_no_tree: 'kbView.runDoc.skip.parse_no_tree',
}

/** 系统故障类原因 → 横幅标红，与"正常复用/空文件"区分开 */
const SKIP_REASON_FAULTY = new Set(['preprocess_failed', 'parser_failed', 'unsupported_type'])

const isSkippedDoc = computed(() => {
  const doc = miningStore.currentDocument
  if (!doc) return false
  return doc.status === 'skipped' || String(doc.action).toUpperCase() === 'SKIP'
})

const skipReasonText = computed(() => {
  const doc = miningStore.currentDocument
  if (!doc || !isSkippedDoc.value) return ''
  const code = doc.skip_reason
  if (code) {
    const key = SKIP_REASON_LABEL_KEYS[code]
    return key ? t(key) : code
  }
  // 老数据没有 skip_reason 字段 → 只能按状态给泛化说明
  return doc.status === 'skipped'
    ? t('kbView.runDoc.skip.noContent')
    : t('kbView.runDoc.skip.unchangedLegacy')
})

const skipReasonDetail = computed(() => {
  const doc = miningStore.currentDocument
  if (!doc || !isSkippedDoc.value) return ''
  return doc.skip_reason_detail || ''
})

const skipIsFaulty = computed(() => {
  const code = miningStore.currentDocument?.skip_reason
  return !!code && SKIP_REASON_FAULTY.has(code)
})

// ── Formatters ──

/** Run 内文档状态（mining_run_documents.status）→ 文案 key；与 Run 详情文档表同一套 */
const DOC_STATUS_LABEL_KEYS: Record<string, string> = {
  committed: 'kbView.runDoc.docStatus.committed',
  processing: 'common.docStatus.mining',
  failed: 'common.docStatus.failed',
  pending: 'kbView.runDoc.docStatus.pending',
  skipped: 'kbView.runDoc.docStatus.skipped',
}

function docStatusLabel(status: string) {
  const key = DOC_STATUS_LABEL_KEYS[status]
  return key ? t(key) : status
}

/** 文档动作（action）→ 文案 key */
const ACTION_LABEL_KEYS: Record<string, string> = {
  new: 'kbView.runDoc.action.new', updated: 'kbView.runDoc.action.updated',
  unchanged: 'kbView.runDoc.action.unchanged',
  NEW: 'kbView.runDoc.action.new', UPDATE: 'kbView.runDoc.action.updated',
  SKIP: 'kbView.runDoc.docStatus.skipped',
}

function actionLabel(action: string) {
  const key = ACTION_LABEL_KEYS[action]
  return key ? t(key) : action
}

/** 阶段名 → 文案 key（正式算子名由 miningOperatorLabel 先行翻译） */
const STAGE_LABEL_KEYS: Record<string, string> = {
  parse: 'kbView.runDoc.stage.parse', segment: 'kbView.runDoc.stage.segment',
  enrich: 'kbView.runDoc.stage.enrich', discourse: 'kbView.runDoc.stage.discourse',
  retrieval_units: 'kbView.runDoc.stage.retrieval_units', embedding: 'common.miningOperator.embedding',
  db_write: 'kbView.runDoc.stage.db_write', commit_segments: 'kbView.runDoc.stage.commit_segments',
  build_relations: 'kbView.runDoc.stage.build_relations',
  build_retrieval_units: 'kbView.runDoc.stage.build_retrieval_units',
  select_snapshot: 'kbView.runDoc.stage.select_snapshot', assemble_build: 'kbView.runDoc.stage.assemble_build',
  validate_build: 'kbView.runDoc.stage.validate_build',
  publish_release: 'kbView.runDoc.stage.publish_release',
}

function stageLabel(stage: string) {
  const operatorLabel = miningOperatorLabel(stage)
  if (operatorLabel !== stage) return operatorLabel
  const key = STAGE_LABEL_KEYS[stage]
  return key ? t(key) : stage
}

function mapStageStatus(status: string): 'completed' | 'running' | 'failed' | 'cancelled' | 'pending' | 'queued' | 'succeeded' | 'dead_letter' | 'committed' | 'processing' | 'skipped' {
  const map: Record<string, string> = { started: 'running', skipped: 'skipped' }
  return (map[status] || status) as typeof mapStageStatus extends (...args: unknown[]) => infer R ? R : never
}

function formatTime(value: string | undefined) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN')
}

function formatMs(ms: number) {
  if (ms < 1000) return `${ms}ms`
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

// ── Data loading ──

async function loadArtifacts() {
  if (activeArtifactTab.value === 'segments') {
    artifactsLoading.value = true
    try {
      const result = await miningApi.getRunDocumentSegments(props.runId, props.docId, {
        limit: PAGE_SIZE, offset: (segPage.value - 1) * PAGE_SIZE,
      })
      segments.value = result.items
      segTotal.value = result.total ?? 0
    } catch { /* ignore */ }
    finally { artifactsLoading.value = false }
  } else if (activeArtifactTab.value === 'units') {
    artifactsLoading.value = true
    try {
      const result = await miningApi.getRunDocumentUnits(props.runId, props.docId, {
        limit: PAGE_SIZE, offset: (unitPage.value - 1) * PAGE_SIZE,
      })
      units.value = result.items
      unitTotal.value = result.total ?? 0
    } catch { /* ignore */ }
    finally { artifactsLoading.value = false }
  } else if (activeArtifactTab.value === 'raw-content') {
    loadRawContent()
  }
}

async function loadRawContent() {
  rawLoading.value = true
  rawError.value = ''
  rawHtml.value = ''
  try {
    const res = await miningApi.getRunDocumentRawContent(props.runId, props.docId)
    if (res.format === 'markdown') {
      rawHtml.value = DOMPurify.sanitize(await marked(res.content))
    } else if (res.format === 'html') {
      rawHtml.value = DOMPurify.sanitize(res.content)
    } else {
      rawHtml.value = `<pre class="raw-plain">${escapeHtml(res.content)}</pre>`
    }
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : t('common.loadFailed')
    // 「not renderable」/「404」是与错误原文比对的匹配数据（R2：不翻）
    if (msg.includes('not renderable') || msg.includes('404')) {
      rawError.value = t('kbView.runDoc.rawUnpreviewable')
    } else {
      rawError.value = msg
    }
  } finally {
    rawLoading.value = false
  }
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

async function loadAll() {
  await miningStore.fetchDocumentDetail(props.runId, props.docId)
  loadArtifacts()
}

onMounted(loadAll)
watch(() => activeArtifactTab.value, () => {
  segPage.value = 1
  unitPage.value = 1
  loadArtifacts()
})
watch(segPage, loadArtifacts)
watch(unitPage, loadArtifacts)
</script>

<style scoped>
.doc-detail__load-error { display: flex; align-items: center; gap: 10px; padding: 14px; color: #b91c1c; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; font-size: 13px; }
.doc-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.doc-detail__back { margin-bottom: 0; }

/* Info card */
.doc-detail__info-card {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 20px 22px;
  border: 1px solid var(--kb-border-light);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}

.doc-detail__info-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.doc-detail__name {
  font-size: 16px;
  font-weight: 650;
  color: var(--kb-text-primary);
  margin: 0;
}

.doc-detail__info-right {
  display: flex;
  gap: 16px;
}

.metric {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.metric__value {
  font-size: 14px;
  font-weight: 700;
  color: var(--kb-text-primary);
  font-variant-numeric: tabular-nums;
}

.metric__value--danger { color: var(--kb-danger); }
.metric__label { font-size: 11px; color: var(--kb-text-tertiary); text-transform: uppercase; letter-spacing: 0.5px; }

/* Error banner */
.doc-detail__error-banner {
  background: var(--kb-danger-soft);
  color: var(--kb-danger);
  padding: 12px 18px;
  border-radius: var(--kb-radius-sm);
  font-size: 13px;
  border-left: 3px solid var(--kb-danger);
}

/* Skip banner */
.doc-detail__skip-banner {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  background: var(--kb-border-light);
  color: var(--kb-text-secondary);
  padding: 12px 18px;
  border-radius: var(--kb-radius-sm);
  font-size: 13px;
  border-left: 3px solid var(--kb-text-tertiary);
}

/* 系统没能处理它 —— 与"正常复用/空文件"区别对待 */
.doc-detail__skip-banner--faulty {
  background: var(--kb-warning-soft, var(--kb-border-light));
  color: var(--kb-warning);
  border-left-color: var(--kb-warning);
}

.skip-banner__body { flex: 1; min-width: 0; }

.skip-banner__detail {
  margin-top: 4px;
  font-size: 12px;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  color: var(--kb-text-tertiary);
  word-break: break-word;
}

/* Section */
.doc-detail__section {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 20px 22px;
  border: 1px solid var(--kb-border-light);
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 8px;
}

.section-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 0 0 16px;
}

/* Stage timeline */
.stage-timeline {
  display: flex;
  flex-direction: column;
  gap: 0;
  position: relative;
}

.stage-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  position: relative;
  padding-bottom: 18px;
}

.stage-item__dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 4px;
  background: var(--kb-border-light);
}

.stage-item--completed .stage-item__dot { background: var(--kb-success); }
.stage-item--failed .stage-item__dot { background: var(--kb-danger); }
.stage-item--started .stage-item__dot { background: var(--kb-warning); animation: pulse-dot 1.5s ease-in-out infinite; }

.stage-item__line {
  position: absolute;
  left: 4px;
  top: 14px;
  bottom: 0;
  width: 2px;
  background: var(--kb-border-light);
}

.stage-item__content {
  flex: 1;
  min-width: 0;
}

.stage-item__header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.stage-item__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-primary);
}

.stage-item__meta {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: var(--kb-text-tertiary);
}

.stage-item__duration { font-variant-numeric: tabular-nums; font-weight: 600; }
.stage-item__error { font-size: 12px; color: var(--kb-danger); margin-top: 4px; }
.stage-item__summary { font-size: 12px; color: var(--kb-text-secondary); margin-top: 4px; }

.stage-empty { color: var(--kb-text-tertiary); font-size: 13px; padding: 16px 0; }

/* Artifact stats */
.artifact-stats {
  display: flex;
  gap: 24px;
}

.artifact-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.artifact-stat__value {
  font-size: 20px;
  font-weight: 700;
  color: var(--kb-text-primary);
  font-variant-numeric: tabular-nums;
}

.artifact-stat__label {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* Artifact filters */
.artifact-filters { display: flex; gap: 6px; }

.filter-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 12px;
  border-radius: 14px;
  border: 1px solid var(--kb-border);
  background: var(--kb-bg-card);
  font-size: 11px;
  font-weight: 500;
  color: var(--kb-text-secondary);
  cursor: pointer;
  transition: all var(--kb-duration) var(--kb-ease);
}

.filter-tag:hover { border-color: var(--kb-accent-medium); color: var(--kb-accent); }
.filter-tag--active { background: var(--kb-accent-soft); border-color: var(--kb-accent); color: var(--kb-accent); }

.tab-pagination {
  display: flex;
  justify-content: center;
  margin-top: 12px;
}

/* Common */
.text-muted { color: var(--kb-text-tertiary); font-size: 13px; }
.text-preview { font-size: 12px; color: var(--kb-text-secondary); line-height: 1.4; }

.text-preview.expandable {
  cursor: pointer;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
  transition: color 0.15s ease;
}

.text-preview.expandable:hover {
  color: var(--kb-accent);
}

.text-preview.expandable.is-expanded {
  -webkit-line-clamp: unset;
  display: block;
  white-space: pre-wrap;
}
.relation-type-tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 3px;
  background: var(--kb-accent-soft);
  color: var(--kb-accent);
  font-size: 11px;
  font-weight: 600;
}

.action-badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}

.action-badge--new, .action-badge--NEW { background: var(--kb-success-soft); color: var(--kb-success); }
.action-badge--updated, .action-badge--UPDATE { background: var(--kb-accent-soft); color: var(--kb-accent); }
.action-badge--unchanged, .action-badge--SKIP { background: var(--kb-border-light); color: var(--kb-text-tertiary); }

@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

/* Raw content */
.raw-content-wrapper {
  min-height: 200px;
}

.raw-content-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 20px;
  color: var(--kb-text-tertiary);
  font-size: 13px;
}

.raw-content-body {
  font-size: 14px;
  line-height: 1.7;
  color: var(--kb-text-primary);
  max-height: 70vh;
  overflow-y: auto;
  padding: 4px 0;
}

.raw-content-body :deep(h1),
.raw-content-body :deep(h2),
.raw-content-body :deep(h3),
.raw-content-body :deep(h4) {
  color: var(--kb-text-primary);
  margin: 1em 0 0.5em;
}

.raw-content-body :deep(p) {
  margin: 0.5em 0;
}

.raw-content-body :deep(pre) {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm);
  padding: 12px 16px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.5;
}

.raw-content-body :deep(code) {
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  font-size: 0.9em;
  background: var(--kb-bg-card);
  padding: 2px 4px;
  border-radius: 3px;
}

.raw-content-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.8em 0;
}

.raw-content-body :deep(th),
.raw-content-body :deep(td) {
  border: 1px solid var(--kb-border);
  padding: 6px 10px;
  text-align: left;
}

.raw-content-body :deep(th) {
  background: var(--kb-bg-card);
  font-weight: 600;
}

.raw-content-body :deep(blockquote) {
  border-left: 3px solid var(--kb-accent);
  padding-left: 12px;
  color: var(--kb-text-secondary);
  margin: 0.8em 0;
}

.raw-content-body :deep(img) {
  max-width: 100%;
  border-radius: var(--kb-radius-sm);
}

.raw-content-body :deep(.raw-plain) {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  font-size: 13px;
  line-height: 1.5;
  color: var(--kb-text-secondary);
}
</style>
