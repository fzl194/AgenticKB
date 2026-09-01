<template>
  <div class="doc-detail" v-loading="miningStore.loading">
    <div class="doc-detail__back">
      <el-button text @click="$router.push(`/kb/${props.kbId}/run/${props.runId}`)">
        <el-icon><ArrowLeft /></el-icon> 返回 Run
      </el-button>
    </div>

    <div v-if="miningStore.error && !miningStore.currentDocument" class="doc-detail__load-error">
      <p>文档详情加载失败：{{ miningStore.error }}</p>
      <el-button size="small" @click="loadAll">重试</el-button>
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
            <span class="metric__label">耗时</span>
          </div>
          <div class="metric" v-if="miningStore.currentDocument.error_message">
            <span class="metric__value metric__value--danger">有错误</span>
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
          <div><strong>跳过原因：</strong>{{ skipReasonText }}</div>
          <div v-if="skipReasonDetail" class="skip-banner__detail">{{ skipReasonDetail }}</div>
        </div>
      </div>

      <!-- Stage Timeline -->
      <div class="doc-detail__section">
        <h4 class="section-label">阶段时间线</h4>
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
                  {{ stage.status === 'completed' ? '完成' : stage.status === 'failed' ? '失败' : stage.status === 'started' ? '进行中' : '跳过' }}
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
          <div v-if="mergedStages.length === 0" class="stage-empty">暂无阶段数据</div>
        </div>
      </div>

      <!-- Artifact Summary -->
      <div class="doc-detail__section" v-if="miningStore.documentArtifacts">
        <h4 class="section-label">产物摘要</h4>
        <div class="artifact-stats">
          <div class="artifact-stat">
            <span class="artifact-stat__value">{{ miningStore.documentArtifacts.segment_count }}</span>
            <span class="artifact-stat__label">原始分段</span>
          </div>
          <div class="artifact-stat">
            <span class="artifact-stat__value">{{ miningStore.documentArtifacts.unit_count }}</span>
            <span class="artifact-stat__label">检索单元</span>
          </div>
        </div>
      </div>

      <!-- Artifact Tabs -->
      <div class="doc-detail__section">
        <div class="section-header">
          <h4 class="section-label" style="margin-bottom: 0">知识产物</h4>
          <div class="artifact-filters">
            <button
              v-for="f in artifactTabs"
              :key="f.key"
              class="filter-tag"
              :class="{ 'filter-tag--active': activeArtifactTab === f.key }"
              @click="activeArtifactTab = f.key"
            >{{ f.label }}</button>
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
          <el-table-column label="类型" width="100" prop="block_type" />
          <el-table-column label="角色" width="100" prop="semantic_role" />
          <el-table-column label="标题" min-width="150" prop="section_title" />
          <el-table-column label="内容" min-width="300">
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
          <el-table-column label="类型" width="120" prop="unit_type" />
          <el-table-column label="结构上下文" min-width="200" prop="title" />
          <el-table-column label="内容" min-width="300">
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
          <div v-else-if="!rawLoading" class="stage-empty">无可渲染的原始文件</div>
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

const artifactTabs = [
  { key: 'segments', label: '原始分段' },
  { key: 'units', label: '检索单元' },
  { key: 'raw-content', label: '原始文本' },
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
// 这里翻译成中文。老数据没有该字段 → 按状态给出兜底说明。
const SKIP_REASON_LABELS: Record<string, string> = {
  // 复用已有成果 —— 正常，无需处理
  unchanged: '内容与已发布快照一致（哈希未变），复用原快照，未重新挖掘',
  restored: '内容与历史快照一致，直接恢复该快照，未重新挖掘',
  // 系统没能处理它 —— 属于故障，需排查
  preprocess_failed: '摄取时格式转换失败（PDF 抽取 / HTML 转换 / 压缩包解包 / doc 转 docx），未能取到正文',
  parser_failed: '文件解析失败（可能已加密、损坏或格式异常），未生成快照',
  unsupported_type: '该文件类型没有对应的解析器，未生成快照',
  // 文件本身没内容 —— 通常无需处理
  empty_file: '文件内容为空或未抽取到文本（如扫描件 PDF），未生成快照',
  no_segments: '分段结果为空，未生成快照',
  // 兜底：解析未产出内容但不属于以上任何一种
  parse_no_tree: '解析未产出内容，未生成快照',
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
  if (code) return SKIP_REASON_LABELS[code] || code
  // 老数据没有 skip_reason 字段 → 只能按状态给泛化说明
  return doc.status === 'skipped'
    ? '该文档未产出可入库的内容（解析为空或分段为空）'
    : '内容未发生变化，复用已发布快照'
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

function docStatusLabel(status: string) {
  const map: Record<string, string> = {
    committed: '完成', processing: '处理中', failed: '失败', pending: '等待', skipped: '跳过',
  }
  return map[status] || status
}

function actionLabel(action: string) {
  const map: Record<string, string> = { new: '新增', updated: '更新', unchanged: '无变化', NEW: '新增', UPDATE: '更新', SKIP: '跳过' }
  return map[action] || action
}

function stageLabel(stage: string) {
  const operatorLabel = miningOperatorLabel(stage)
  if (operatorLabel !== stage) return operatorLabel
  const map: Record<string, string> = {
    parse: '解析', segment: '分段', enrich: '增强', discourse: '语篇分析',
    retrieval_units: '检索单元构建', embedding: '向量化',
    db_write: '数据写入', commit_segments: '写入分段',
    build_relations: '写入关系', build_retrieval_units: '写入检索单元',
    select_snapshot: '快照选择', assemble_build: '构建组装', validate_build: '构建校验',
    publish_release: '发布',
  }
  return map[stage] || stage
}

function mapStageStatus(status: string): 'completed' | 'running' | 'failed' | 'cancelled' | 'pending' | 'queued' | 'succeeded' | 'dead_letter' | 'committed' | 'processing' | 'skipped' {
  const map: Record<string, string> = { started: 'running', skipped: 'skipped' }
  return (map[status] || status) as typeof mapStageStatus extends (...args: unknown[]) => infer R ? R : never
}

function formatTime(t: string | undefined) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
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
    const msg = e instanceof Error ? e.message : '加载失败'
    if (msg.includes('not renderable') || msg.includes('404')) {
      rawError.value = '该文件类型不支持在线预览'
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
