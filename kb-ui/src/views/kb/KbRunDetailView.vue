<template>
  <div class="run-detail" v-loading="initialLoading">
    <div class="run-detail__back">
      <el-button text @click="$router.push(`/kb/${props.kbId}`)">
        <el-icon><ArrowLeft /></el-icon> {{ t('kbView.detail.backToList') }}
      </el-button>
    </div>

    <div v-if="!initialLoading && miningStore.error && !miningStore.currentRun" class="run-detail__error-banner">
      {{ miningStore.error }}
      <el-button size="small" @click="startPolling">{{ t('common.retry') }}</el-button>
    </div>

    <template v-if="miningStore.currentRun">
      <!-- Run Meta Card -->
      <div class="run-detail__meta-card">
        <div class="run-detail__meta-left">
          <h3 class="run-detail__id">{{ miningStore.currentRun.id.slice(0, 8) }}</h3>
          <StatusBadge :status="miningStore.currentRun.status">{{ statusLabel(miningStore.currentRun.status) }}</StatusBadge>
        </div>
        <div class="run-detail__metrics">
          <div class="metric">
            <span class="metric__value">{{ miningStore.currentRun.total_documents }}</span>
            <span class="metric__label">{{ t('dashboard.tile.docs') }}</span>
          </div>
          <div class="metric" v-if="miningStore.currentRun.new_count">
            <span class="metric__value metric__value--success">{{ miningStore.currentRun.new_count }}</span>
            <span class="metric__label">{{ t('kbView.runDoc.action.new') }}</span>
          </div>
          <div class="metric" v-if="miningStore.currentRun.updated_count">
            <span class="metric__value metric__value--accent">{{ miningStore.currentRun.updated_count }}</span>
            <span class="metric__label">{{ t('kbView.runDoc.action.updated') }}</span>
          </div>
          <div class="metric" v-if="miningStore.currentRun.failed_count">
            <span class="metric__value metric__value--danger">{{ miningStore.currentRun.failed_count }}</span>
            <span class="metric__label">{{ t('common.docStatus.failed') }}</span>
          </div>
          <div class="metric">
            <span class="metric__value">{{ formatDuration(miningStore.currentRun.started_at, miningStore.currentRun.finished_at) }}</span>
            <span class="metric__label">{{ t('kbView.run.totalDuration') }}</span>
          </div>
          <div class="metric">
            <span class="metric__value">{{ formatTime(miningStore.currentRun.started_at) }}</span>
            <span class="metric__label">{{ t('common.submittedAt') }}</span>
          </div>
        </div>
      </div>

      <!-- Error Banner -->
      <div v-if="miningStore.currentRun.error_summary" class="run-detail__error-banner">
        {{ miningStore.currentRun.error_summary }}
      </div>
      <div v-else-if="miningStore.error" class="run-detail__error-banner">
        {{ miningStore.error }}
      </div>

      <!-- 36号 §九：部分成功横幅——成功文档已入库，失败文档等待重试 -->
      <div v-if="partialStats" class="run-detail__partial-banner" data-testid="run-partial-banner">
        <div class="partial-banner__title">{{ t('kbView.run.partialTitle') }}</div>
        <div class="partial-banner__stats">
          <span>{{ t('kbView.run.partial.committed', { n: partialStats.committed }) }}</span>
          <span v-if="partialStats.failed">{{ t('kbView.run.partial.failed', { n: partialStats.failed }) }}</span>
          <span v-if="partialStats.skipped">{{ t('kbView.run.partial.skipped', { n: partialStats.skipped }) }}</span>
        </div>
        <div class="partial-banner__hint">{{ t('kbView.run.partialHint') }}</div>
      </div>

      <!-- 人审暂停横幅（B7）-->
      <div v-if="miningStore.currentRun.status === 'awaiting_review'" class="run-detail__review-banner">
        <div class="review-banner__main">
          <div class="review-banner__icon">⏸</div>
          <div class="review-banner__text">
            <div class="review-banner__title">{{ t('kbView.run.pausedTitle') }}</div>
            <div class="review-banner__sub">
              {{ trace?.pause_step ? t('kbView.run.pausedNode', { node: trace.pause_step }) : t('kbView.run.resumeHint') }}
            </div>
          </div>
        </div>
        <div class="review-banner__actions">
          <el-button type="success" :loading="resuming" @click="handleResume">{{ t('kbView.run.resume') }}</el-button>
        </div>
      </div>

      <!-- Progress Overview Card -->
      <div v-if="miningStore.progress" class="run-detail__progress-card">
        <div class="progress-card__row">
          <div class="progress-card__bar-wrap">
            <div class="progress-card__bar">
              <div
                class="progress-card__fill progress-card__fill--completed"
                :style="{ width: pctCompleted + '%' }"
              />
              <div
                class="progress-card__fill progress-card__fill--failed"
                :style="{ width: pctFailed + '%', left: pctCompleted + '%' }"
              />
            </div>
            <span class="progress-card__percent">{{ miningStore.progress.progress_percent }}%</span>
          </div>
        </div>
        <div class="progress-card__stats">
          <span class="progress-stat progress-stat--completed">{{ t('kbView.run.progress.completed', { n: miningStore.progress.completed }) }}</span>
          <span class="progress-stat progress-stat--failed" v-if="miningStore.progress.failed">{{ t('kbView.run.progress.failed', { n: miningStore.progress.failed }) }}</span>
          <span class="progress-stat progress-stat--skipped" v-if="miningStore.progress.skipped">{{ t('kbView.run.progress.skipped', { n: miningStore.progress.skipped }) }}</span>
          <span class="progress-stat progress-stat--processing" v-if="miningStore.progress.processing">{{ t('kbView.run.progress.processing', { n: miningStore.progress.processing }) }}</span>
          <span class="progress-stat progress-stat--stage" v-if="miningStore.progress.current_stage">{{ stageLabel(miningStore.progress.current_stage) }}</span>
        </div>
      </div>

      <!-- Pipeline Flow -->
      <div class="run-detail__section">
        <div class="run-detail__stage-header">
          <h4 class="section-label" style="margin-bottom: 0">{{ t('kbView.run.pipelineStages') }}</h4>
          <!-- A0-6：历史 legacy Run（真实记录过实体/本体阶段）如实标识，
               不因兼容组件宣称当前产品仍有实体/本体能力 -->
          <el-tag
            v-if="isLegacyRun"
            size="small" effect="plain" type="info"
            data-testid="legacy-run-tag"
          >{{ t('kbView.run.legacyTask') }}</el-tag>
        </div>
        <MiningWorkflowTrace v-if="trace?.workflow" :trace="trace" />
        <!-- A0-6：真实阶段事件存在才渲染流程条（PipelineFlow 按事件推导，
             不再回退固定实体/本体阶段） -->
        <PipelineFlow
          v-else-if="miningStore.stages.length"
          :stage-events="miningStore.stages" :all-docs-settled="allDocsSettled"
        />
        <div
          v-else-if="traceFailed"
          class="run-detail__stages-unavailable"
          data-testid="stages-unavailable"
        >
          {{ t('kbView.run.stagesUnavailable') }}
          <el-button
            size="small" text type="primary"
            data-testid="stages-retry"
            @click="fetchTrace"
          >{{ t('common.retry') }}</el-button>
        </div>
      </div>

      <!-- Documents Table -->
      <div class="run-detail__section">
        <div class="section-header">
          <h4 class="section-label" style="margin-bottom: 0">{{ t('kbView.run.docResults', { n: miningStore.documentsTotal }) }}</h4>
          <div class="doc-filters">
            <button
              v-for="f in docFilters"
              :key="f.key"
              class="filter-tag"
              :class="{ 'filter-tag--active': activeDocFilter === f.key }"
              @click="activeDocFilter = f.key"
            >
              {{ f.label }}
              <span class="filter-tag__count" v-if="f.count">{{ f.count }}</span>
            </button>
          </div>
        </div>
        <el-table
          :data="filteredDocs"
          class="kb-table"
          :header-cell-style="{ background: 'transparent' }"
          @row-click="(row: Record<string, unknown>) => $router.push(`/kb/${props.kbId}/run/${props.runId}/doc/${row.id}`)"
          style="cursor: pointer"
        >
          <el-table-column :label="t('kbView.run.column.fileName')" min-width="200">
            <template #default="{ row }">
              <span class="doc-name">{{ docDisplayName(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.size')" width="100">
            <template #default="{ row }">
              <span class="duration-text">{{ row.file_size != null ? formatFileSize(row.file_size) : '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.status')" width="120">
            <template #default="{ row }">
              <StatusBadge :status="row.status" size="small">{{ docStatusLabel(row.status) }}</StatusBadge>
            </template>
          </el-table-column>
          <el-table-column :label="t('kbView.run.column.stage')" width="120">
            <template #default="{ row }">
              <span class="stage-text">{{ row.current_stage ? stageLabel(row.current_stage) : '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.actions')" width="100">
            <template #default="{ row }">
              <span class="action-badge" :class="`action-badge--${String(row.action).toLowerCase()}`">{{ actionLabel(row.action) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.duration')" width="100">
            <template #default="{ row }">
              <span class="duration-text">{{ row.duration_ms ? formatMs(row.duration_ms) : '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('kbView.run.column.error')" min-width="200">
            <template #default="{ row }">
              <span class="text-error" v-if="row.error_message">{{ row.error_message }}</span>
              <span v-else class="text-muted">-</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="run-detail__pagination" v-if="miningStore.documentsTotal > 50">
          <el-pagination
            v-model:current-page="docPageProxy"
            :page-size="50"
            :total="miningStore.documentsTotal"
            layout="prev, pager, next"
            size="small"
          />
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useDomainStore } from '@/stores/domain'
import { useMiningStore } from '@/stores/mining'
import { useMiningApi } from '@/api/mining'
// 共用一份 run 状态文案：这里原先缺 queued、多一个 DB CHECK 里不存在的 pending
import { isActiveRunStatus, runStatusLabel as statusLabel } from '@/utils/runStatus'
import type { RunTrace } from '@/types'
import StatusBadge from '@/components/common/StatusBadge.vue'
import PipelineFlow from '@/components/kb/PipelineFlow.vue'
import MiningWorkflowTrace from '@/components/mining/workflow/MiningWorkflowTrace.vue'
import { miningOperatorLabel } from '@/utils/miningWorkflowPresentation'
import { t } from '@/locales/t'

const props = defineProps<{ kbId: string; runId: string }>()
const domainStore = useDomainStore()
const miningStore = useMiningStore()
const miningApi = useMiningApi()

const activeDocFilter = ref('all')
const initialLoading = ref(true)
const trace = ref<RunTrace | null>(null)
// A0-6：trace 拉取失败不再静默——无真实阶段事件时显示「暂不可用 + 重试」，
// 而不是回退到固定的实体/本体/落图阶段条
const traceFailed = ref(false)
let traceGeneration = 0
const resuming = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null
// resume 后延迟启动轮询的句柄，需在卸载/切域时一并清除，避免离开页面后仍触发轮询
let resumeTimer: ReturnType<typeof setTimeout> | null = null

// ── Formatters ──

/** 36号 §九：部分成功统计（finalize 写入 metadata 的 partial_success 元数据）。 */
const partialStats = computed<{ committed: number; failed: number; skipped: number } | null>(() => {
  const run = miningStore.currentRun
  if (!run || run.status !== 'completed') return null
  const meta = run.metadata_json || {}
  if (meta.partial_success !== true) return null
  return {
    committed: Number(meta.committed_count ?? run.committed_count ?? 0),
    failed: Number(meta.failed_count ?? run.failed_count ?? 0),
    skipped: Number(meta.skipped_count ?? run.skipped_count ?? 0),
  }
})

/** Run 内文档状态（mining_run_documents.status）→ 文案 key；表里只存 key，调用期经 t() 翻译 */
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
  NEW: 'kbView.runDoc.action.new', UPDATE: 'kbView.runDoc.action.updated',
  SKIP: 'kbView.runDoc.action.unchanged', REMOVE: 'kbView.runDoc.action.removed',
  new: 'kbView.runDoc.action.new', updated: 'kbView.runDoc.action.updated',
  unchanged: 'kbView.runDoc.action.unchanged',
}

function actionLabel(action: string) {
  const key = ACTION_LABEL_KEYS[action]
  return key ? t(key) : action
}

/** 兼容阶段名 → 文案 key（正式算子名由 miningOperatorLabel 先行翻译） */
const STAGE_LABEL_KEYS: Record<string, string> = {
  parse: 'kbView.runDoc.stage.parse', segment: 'kbView.runDoc.stage.segment',
  enrich: 'kbView.run.stage.enrich', entity_extract: 'kbView.run.stage.entity_extract',
  resolve: 'kbView.run.stage.resolve', entity_relations: 'kbView.run.stage.entity_relations',
  graph_write: 'kbView.run.stage.graph_write',
  discourse: 'kbView.runDoc.stage.discourse', discourse_relations: 'kbView.runDoc.stage.discourse',
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

function docDisplayName(row: Record<string, unknown>) {
  if (row.document_name) return row.document_name
  const dk = row.document_key as string | undefined
  if (dk?.startsWith('doc:/')) return dk.replace('doc:/', '')
  if (row.document_id) return (row.document_id as string).slice(0, 8)
  return '-'
}

function formatTime(value: string | undefined) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN')
}

function formatDuration(start?: string, end?: string) {
  if (!start) return '-'
  const s = new Date(start).getTime()
  const e = end ? new Date(end).getTime() : Date.now()
  const diff = Math.round((e - s) / 1000)
  if (diff < 60) return `${diff}s`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`
  return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`
}

function formatMs(ms: number) {
  if (ms < 1000) return `${ms}ms`
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

// ── Progress card ──

// 红条（失败占比）仍按文档维度展示
const pctFailed = computed(() => {
  const p = miningStore.progress
  if (!p || !p.total) return 0
  return Math.round((p.failed / p.total) * 100)
})

// 绿条 = 整轮总进度（含全局尾段，来自后端 progress_percent）减去失败占比，
// 保证彩条宽度与右侧百分比数字口径一致（文档全提交≠100%）。
const pctCompleted = computed(() => {
  const p = miningStore.progress
  if (!p) return 0
  return Math.max(0, Math.round(p.progress_percent) - pctFailed.value)
})

// 全部文档是否已尘埃落定（无处理中、且已提交+失败+跳过≥总数）。
// 供 PipelineFlow 判断：逐文档阶段的"孤儿 started 事件"不应再把阶段永久钉在运行中。
const allDocsSettled = computed(() => {
  const p = miningStore.progress
  if (!p || !p.total) return false
  return p.processing === 0 && (p.completed + p.failed + p.skipped) >= p.total
})

// ── Document filters ──

const docFilters = computed(() => {
  const docs = miningStore.documents
  return [
    { key: 'all', label: t('kbView.run.filter.all'), count: docs.length },
    { key: 'processing', label: t('common.docStatus.mining'), count: docs.filter(d => d.status === 'processing' || d.status === 'pending').length },
    { key: 'failed', label: t('common.docStatus.failed'), count: docs.filter(d => d.status === 'failed').length },
    { key: 'committed', label: t('common.runStatus.completed'), count: docs.filter(d => d.status === 'committed').length },
  ]
})

// ── Document pagination ──

const docPageProxy = computed({
  get: () => miningStore.documentsPage,
  set: (val: number) => { miningStore.documentsPage = val },
})

// ── Filtered docs ──

const filteredDocs = computed(() => {
  if (activeDocFilter.value === 'all') return miningStore.documents
  if (activeDocFilter.value === 'processing') {
    return miningStore.documents.filter(d => d.status === 'processing' || d.status === 'pending')
  }
  return miningStore.documents.filter(d => d.status === activeDocFilter.value)
})

// ── Polling ──

async function fetchTrace() {
  const generation = ++traceGeneration
  const domain = domainStore.currentDomain
  const runId = props.runId
  try {
    const result = await miningApi.getRunTrace(runId)
    if (
      generation === traceGeneration
      && domain === domainStore.currentDomain
      && runId === props.runId
    ) {
      trace.value = result
      traceFailed.value = false
    }
  } catch {
    if (generation === traceGeneration) traceFailed.value = true
  }
}

// 历史 legacy 阶段键（仅实体/本体/落图旧链——run.py 现行无条件阶段
// parse/segment/enrich/discourse/retrieval_units/embedding/db_write 都不属于）。
// 注意：enrich（段落理解）与 discourse（语篇分析）是当前链阶段，
// 误入此集合会把所有现役 Run 标成「历史任务」。
const LEGACY_STAGE_KEYS = new Set([
  'entity_extract', 'resolve', 'entity_relations',
  'graph_write', 'graph_write_final', 'ontology_induction',
  'build_relations', 'commit_segments', 'discourse_relations',
])

/** 该 Run 是否真实记录过旧链阶段（历史任务标识） */
const isLegacyRun = computed(() =>
  miningStore.stages.some(s => LEGACY_STAGE_KEYS.has(s.stage)))

async function handleResume() {
  resuming.value = true
  try {
    const res = await miningApi.resumeRun(props.runId, domainStore.currentDomain)
    if (res.status === 'awaiting_review') {
      ElMessage.warning(t('kbView.run.reviewPending'))
    } else if (res.status === 'completed') {
      ElMessage.success(t('kbView.run.runCompleted'))
    } else {
      // resuming：续跑已在后台开始（建库/发布耗时较久），开始轮询看进度
      ElMessage.success(t('kbView.run.resumeStarted'))
    }
    // 给后台线程一点时间把状态翻成 running 再轮询，避免第一拍还停在旧状态导致轮询过早停止
    if (resumeTimer) clearTimeout(resumeTimer)
    resumeTimer = setTimeout(() => { resumeTimer = null; startPolling() }, 1500)
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : t('kbView.run.resumeFailed'))
  } finally {
    resuming.value = false
  }
}

async function pollOnce(silent = false) {
  if (!domainStore.currentDomain) {
    initialLoading.value = false
    return
  }
  await Promise.all([
    miningStore.fetchProgress(props.runId),
    miningStore.fetchRunDetail(props.runId, { silent }),
    fetchTrace(),
  ])
  initialLoading.value = false
  if (!isActiveRunStatus(miningStore.currentRun?.status) && pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

let pollingGeneration = 0

function startPolling() {
  if (pollTimer) clearInterval(pollTimer)
  // 幂等 + 代际守卫：并发调用（双击重试/延迟恢复与初始链路竞争/连续切换）
  // 时旧 .then 不得再赋值 interval，否则句柄被覆盖成孤儿——双倍轮询且
  // 离开页面后无法清理（2026-08-31 前端审查 H4）。
  const gen = ++pollingGeneration
  if (!domainStore.currentDomain) {
    pollTimer = null
    initialLoading.value = false
    return
  }
  pollOnce(false).then(() => {
    if (gen !== pollingGeneration) return
    if (isActiveRunStatus(miningStore.currentRun?.status)) {
      pollTimer = setInterval(() => pollOnce(true), 3000)
    }
  })
}

onMounted(startPolling)
onUnmounted(() => {
  traceGeneration += 1
  pollingGeneration += 1
  if (pollTimer) clearInterval(pollTimer)
  if (resumeTimer) clearTimeout(resumeTimer)
})
watch(() => domainStore.currentDomain, (domain) => {
  traceGeneration += 1
  pollingGeneration += 1
  trace.value = null
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
  if (resumeTimer) clearTimeout(resumeTimer)
  resumeTimer = null
  miningStore.clearCurrentRun()
  if (domain) startPolling()
})
watch(() => props.runId, () => {
  traceGeneration += 1
  trace.value = null
  miningStore.clearCurrentRun()
  startPolling()
})
watch(() => miningStore.documentsPage, () => {
  if (miningStore.currentRun) {
    miningStore.fetchRunDocuments(miningStore.currentRun.id)
  }
})
</script>

<style scoped>
.run-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.run-detail__back { margin-bottom: 0; }

/* Meta card */
.run-detail__meta-card {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 20px 22px;
  border: 1px solid var(--kb-border-light);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
}

.run-detail__meta-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.run-detail__id {
  font-size: 18px;
  font-weight: 700;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  color: var(--kb-text-primary);
  margin: 0;
  letter-spacing: -0.5px;
}

.run-detail__metrics {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}

.metric {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.metric__value {
  font-size: 16px;
  font-weight: 700;
  color: var(--kb-text-primary);
  font-variant-numeric: tabular-nums;
}

.metric__value--success { color: var(--kb-success); }
.metric__value--accent { color: var(--kb-accent); }
.metric__value--danger { color: var(--kb-danger); }

.metric__label {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* Error banner */
.run-detail__error-banner {
  background: var(--kb-danger-soft);
  color: var(--kb-danger);
  padding: 12px 18px;
  border-radius: var(--kb-radius-sm);
  font-size: 13px;
  border-left: 3px solid var(--kb-danger);
}

/* 36号 §九：部分成功横幅——绿底（有产出）带警示边（有失败） */
.run-detail__partial-banner {
  background: var(--kb-success-soft, #ecfdf5);
  border: 1px solid var(--kb-success-medium, #6ee7b7);
  border-left: 3px solid var(--kb-warning, #f59e0b);
  border-radius: var(--kb-radius-sm);
  padding: 12px 18px;
  font-size: 13px;
}
.partial-banner__title {
  font-weight: 600;
  margin-bottom: 4px;
}
.partial-banner__stats {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
}
.partial-banner__hint {
  margin-top: 4px;
  color: var(--kb-text-muted, #64748b);
  font-size: 12px;
}

/* 人审暂停横幅 */
.run-detail__review-banner {
  background: var(--kb-accent-soft);
  border: 1px solid var(--kb-accent-medium);
  border-left: 3px solid var(--kb-accent);
  border-radius: var(--kb-radius-sm);
  padding: 14px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.review-banner__main { display: flex; align-items: center; gap: 12px; }
.review-banner__icon { font-size: 22px; color: var(--kb-accent); }
.review-banner__title { font-size: 14px; font-weight: 600; color: var(--kb-text-primary); }
.review-banner__sub { font-size: 12px; color: var(--kb-text-secondary); margin-top: 2px; }
.review-banner__actions { display: flex; gap: 8px; }

/* Progress card */
.run-detail__progress-card {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  padding: 16px 22px;
  border: 1px solid var(--kb-border-light);
}

.progress-card__row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.progress-card__bar-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 12px;
}

.progress-card__bar {
  flex: 1;
  height: 8px;
  background: var(--kb-border-light);
  border-radius: 4px;
  overflow: hidden;
  position: relative;
}

.progress-card__fill {
  position: absolute;
  top: 0;
  height: 100%;
  border-radius: 4px;
  transition: width 0.4s var(--kb-ease);
}

.progress-card__fill--completed {
  background: var(--kb-success);
  left: 0;
}

.progress-card__fill--failed {
  background: var(--kb-danger);
}

.progress-card__percent {
  font-size: 14px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--kb-text-primary);
  min-width: 48px;
  text-align: right;
}

.progress-card__stats {
  display: flex;
  gap: 16px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.progress-stat {
  font-size: 12px;
  font-weight: 500;
  color: var(--kb-text-secondary);
}

.progress-stat--completed { color: var(--kb-success); }
.progress-stat--failed { color: var(--kb-danger); }
.progress-stat--skipped { color: var(--kb-text-tertiary); }
.progress-stat--processing { color: var(--kb-accent); }
.progress-stat--stage {
  margin-left: auto;
  font-style: italic;
  color: var(--kb-text-tertiary);
}

/* Section */
.run-detail__stage-header {
  display: flex; align-items: center; gap: 8px; margin-bottom: 8px;
}
.run-detail__stages-unavailable {
  display: flex; align-items: center; gap: 10px;
  color: var(--kb-text-tertiary); font-size: 13px;
  padding: 18px 16px;
  border: 1px dashed var(--kb-border-light); border-radius: 8px;
}

.run-detail__section {
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
  margin: 0;
}

/* Doc filters */
.doc-filters {
  display: flex;
  gap: 6px;
}

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

.filter-tag:hover {
  border-color: var(--kb-accent-medium);
  color: var(--kb-accent);
}

.filter-tag--active {
  background: var(--kb-accent-soft);
  border-color: var(--kb-accent);
  color: var(--kb-accent);
}

.filter-tag__count {
  font-size: 10px;
  background: var(--kb-border-light);
  padding: 0 5px;
  border-radius: 8px;
}

.filter-tag--active .filter-tag__count {
  background: var(--kb-accent-medium);
}

/* Doc name */
.doc-name {
  font-size: 13px;
  color: var(--kb-text-primary);
}

.stage-text, .duration-text {
  font-size: 12px;
  color: var(--kb-text-secondary);
  font-variant-numeric: tabular-nums;
}

.text-muted { color: var(--kb-text-tertiary); }

/* Action badges */
.action-badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}

.action-badge--new {
  background: var(--kb-success-soft);
  color: var(--kb-success);
}

.action-badge--updated {
  background: var(--kb-accent-soft);
  color: var(--kb-accent);
}

.action-badge--unchanged {
  background: var(--kb-border-light);
  color: var(--kb-text-tertiary);
}

.text-error {
  font-size: 12px;
  color: var(--kb-danger);
}

.run-detail__pagination {
  display: flex;
  justify-content: center;
  padding-top: 12px;
  border-top: 1px solid var(--kb-border-light);
  margin-top: 4px;
}

.ontology-line-stats {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--kb-border-light);
}

.ontology-line-stats__tag {
  font-size: 11px;
  font-weight: 600;
  color: var(--kb-accent);
  background: var(--kb-accent-soft);
  padding: 2px 8px;
  border-radius: 6px;
}

.ontology-line-stats__item {
  font-size: 12px;
  color: var(--kb-text-secondary);
}

.ontology-line-stats__item b {
  color: var(--kb-text-primary);
  font-variant-numeric: tabular-nums;
}

.ontology-line-stats__item--link {
  color: var(--kb-accent);
  text-decoration: none;
  transition: opacity var(--kb-duration) var(--kb-ease);
}

.ontology-line-stats__item--link:hover {
  opacity: 0.7;
}

.ontology-line-stats__item--link b {
  color: var(--kb-accent);
}
</style>
