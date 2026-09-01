<template>
  <div class="kb-mining">
    <!-- 顶部：范式选择 + 刷新（挖掘触发已移至文件列表多选批量操作） -->
    <div class="kb-mining__top">
      <div class="kb-mining__workflow">
        <label class="kb-mining__workflow-label">挖掘范式</label>
        <el-select
          v-model="workflowId"
          size="default"
          :loading="loadingOptions"
          :disabled="!canWrite"
          placeholder="选择已发布的挖掘范式"
          class="kb-mining__workflow-select"
          @change="onWorkflowChange"
        >
          <el-option
            v-for="opt in workflowOptions"
            :key="opt.id"
            :label="workflowLabel(opt)"
            :value="opt.id"
          />
        </el-select>
        <span v-if="!canWrite" class="kb-mining__readonly-hint">仅拥有者或编辑者可修改</span>
      </div>
      <div class="kb-mining__trigger">
        <el-tooltip
          v-if="canWrite"
          content="忽略内容哈希缓存，对所有文档重跑 pipeline（含 LLM 阶段），重生已挖知识"
          placement="top"
        >
          <el-checkbox v-model="forceRedo">强制重挖</el-checkbox>
        </el-tooltip>
        <el-button
          v-if="canWrite" type="primary" :loading="mining"
          :disabled="!workflowId || !!busyRun" data-testid="mine-kb"
          @click="triggerMine"
        >
          <el-icon class="el-icon--left"><Cpu /></el-icon>{{ mineButtonLabel }}
        </el-button>
        <el-button :loading="loadingRuns" @click="loadRuns">
          <el-icon class="el-icon--left"><Refresh /></el-icon>
          刷新记录
        </el-button>
      </div>
    </div>
    <p v-if="!workflowId" class="kb-mining__warn">
      尚未选择挖掘范式。在「文件」tab 多选文档后点「挖掘选中」前，请先绑定一个已发布范式。
    </p>
    <p v-else class="kb-mining__tip">
      点「整库挖掘」对本库文档发起挖掘；或在「文件」tab 多选文档 →「挖掘选中」。点下方任一任务查看实时进度与流水线。
    </p>

    <!-- 挖掘记录列表（行可点 → 任务详情：实时进度 + 12 阶段流水线） -->
    <div class="kb-mining__table-wrap">
      <el-table
        :data="runs"
        v-loading="loadingRuns"
        class="kb-table"
        :header-cell-style="{ background: 'transparent' }"
        style="cursor: pointer"
        @row-click="onRowClick"
      >
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="runStatusTagType(row.status)" size="small" effect="light">
              {{ runStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="170">
          <template #default="{ row }">
            <div class="kb-mining__progress">
              <div class="kb-mining__progress-bar">
                <div
                  class="kb-mining__progress-fill kb-mining__progress-fill--done"
                  :style="{ width: pct((row.committed_count ?? 0) + (row.skipped_count ?? 0), row.total_documents) + '%' }"
                ></div>
                <div
                  class="kb-mining__progress-fill kb-mining__progress-fill--fail"
                  :style="{ width: pct(row.failed_count, row.total_documents) + '%' }"
                ></div>
              </div>
              <span class="kb-mining__progress-text">
                {{ processedDocumentCount(row) }}/{{ row.total_documents ?? 0 }}
                <span v-if="(row.failed_count ?? 0) > 0" class="kb-mining__progress-fail">
                  ·失败 {{ row.failed_count }}
                </span>
              </span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="当前阶段" min-width="130">
          <template #default="{ row }">
            <span v-if="row.current_stage">{{ row.current_stage }}</span>
            <span v-else class="kb-mining__muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="文档（新/更/跳）" min-width="150">
          <template #default="{ row }">
            <span class="kb-mining__counts">
              <span class="kb-mining__count kb-mining__count--new">+{{ row.new_count ?? 0 }}</span>
              <span class="kb-mining__count">~{{ row.updated_count ?? 0 }}</span>
              <span class="kb-mining__count">⊘{{ row.skipped_count ?? 0 }}</span>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="范式版本" width="90">
          <template #default="{ row }">
            <span v-if="row.workflow_version !== null">v{{ row.workflow_version }}</span>
            <span v-else class="kb-mining__muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="提交时间" min-width="160">
          <template #default="{ row }">{{ formatTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="错误摘要" min-width="180">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.error_summary"
              :content="row.error_summary"
              placement="top"
              :show-after="200"
            >
              <span class="kb-mining__error">{{ truncate(row.error_summary, 60) }}</span>
            </el-tooltip>
            <span v-else class="kb-mining__muted">—</span>
          </template>
        </el-table-column>
        <template #empty>
          <div v-if="runsError" class="kb-mining__error">
            <span>{{ runsError }}</span>
            <el-button size="small" @click="loadRuns">重试</el-button>
          </div>
          <EmptyState v-else text="还没有挖掘记录。到「文件」tab 多选文档后点「挖掘选中」触发。" />
        </template>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Cpu, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { useMiningWorkflowApi } from '@/api/miningWorkflow'
import { apiErrorDetail } from '@/api/proxyClient'
import {
  isActiveRunStatus,
  processedDocumentCount,
  runStatusLabel,
  runStatusTagType,
} from '@/utils/runStatus'
import EmptyState from '@/components/common/EmptyState.vue'
import type { KbRunRecord } from '@/types/kb'
import type { MiningWorkflowOption } from '@/types/miningWorkflow'

const props = defineProps<{
  kbId: string
  /** 当前 KB 选定的范式（父组件持有，作 select 初值）。 */
  selectedWorkflowId: string | null
  canWrite: boolean
}>()

/** 范式变更同步给父组件（修 Bug A：父不再依赖列表快照读范式）。 */
const emit = defineEmits<{
  (e: 'update:selectedWorkflowId', value: string | null): void
}>()

const router = useRouter()
const kbApi = useKbApi()
const wfApi = useMiningWorkflowApi()

const workflowOptions = ref<MiningWorkflowOption[]>([])
const loadingOptions = ref(false)
const workflowId = ref<string | null>(props.selectedWorkflowId)

const runs = ref<KbRunRecord[]>([])
const loadingRuns = ref(false)
const runsError = ref('')
const mining = ref(false)
const forceRedo = ref(false)

const busyRun = computed(() => runs.value.find((run) =>
  ['queued', 'running', 'awaiting_review', 'interrupted'].includes(run.status),
) ?? null)
const mineButtonLabel = computed(() => {
  if (busyRun.value?.status === 'queued') return '排队中'
  if (busyRun.value?.status === 'running') return '挖掘中'
  if (busyRun.value) return '任务待处理'
  return forceRedo.value ? '强制整库重挖' : '整库挖掘'
})

let pollTimer: number | null = null
let runsGeneration = 0

watch(() => props.selectedWorkflowId, (v) => { workflowId.value = v })
watch(() => props.kbId, () => {
  clearPolling()
  loadOptions()
  loadRuns()
})

// ── 范式目录 ──
async function loadOptions() {
  loadingOptions.value = true
  try {
    workflowOptions.value = await wfApi.options()
  } catch (e) {
    workflowOptions.value = []
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    loadingOptions.value = false
  }
}

function workflowLabel(opt: MiningWorkflowOption): string {
  return opt.is_system_default ? `${opt.name}（默认）` : opt.name
}

async function onWorkflowChange(value: string | null) {
  const prev = props.selectedWorkflowId
  emit('update:selectedWorkflowId', value) // 立即同步父组件
  try {
    await kbApi.updateKb(props.kbId, { mining_workflow_id: value })
    ElMessage.success(value ? '已绑定挖掘范式' : '已解除挖掘范式绑定')
  } catch (e) {
    emit('update:selectedWorkflowId', prev) // 回滚
    workflowId.value = prev
    ElMessage.error(await apiErrorDetail(e))
  }
}

// ── 触发整库挖掘（无 document_ids = 提交本库全部当前文档）──
async function triggerMine() {
  if (!workflowId.value) {
    ElMessage.warning('请先选择挖掘范式')
    return
  }
  if (busyRun.value) {
    ElMessage.warning(mineButtonLabel.value)
    return
  }
  mining.value = true
  try {
    const res = await kbApi.mineKb(props.kbId, undefined, forceRedo.value)
    ElMessage.success(
      `${forceRedo.value ? '强制整库重挖' : '整库挖掘'}已加入队列（run ${res.run_id.slice(0, 8)}）` +
        (res.auto_force_redo ? '（范式已变更，自动强制重挖）' : ''),
    )
    await loadRuns()
    schedulePolling()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    mining.value = false
  }
}

// ── 挖掘记录 ──
async function loadRuns() {
  const kbId = props.kbId
  const generation = ++runsGeneration
  loadingRuns.value = true
  runsError.value = ''
  try {
    const result = await kbApi.getKbRuns(kbId)
    if (generation !== runsGeneration || kbId !== props.kbId) return
    runs.value = result
    schedulePolling()
  } catch (e) {
    if (generation !== runsGeneration || kbId !== props.kbId) return
    runs.value = []
    runsError.value = await apiErrorDetail(e)
  } finally {
    if (generation === runsGeneration) loadingRuns.value = false
  }
}

function onRowClick(row: KbRunRecord) {
  router.push({ name: 'kb-run-detail', params: { kbId: props.kbId, runId: row.id } })
}

function hasActiveRun(): boolean {
  return runs.value.some((r) => isActiveRunStatus(r.status))
}

/** running/queued 时 3s 轮询直到终态。 */
let disposed = false

function schedulePolling() {
  clearPolling()
  if (disposed || !hasActiveRun()) return
  const kbId = props.kbId
  pollTimer = window.setTimeout(async () => {
    try {
      const result = await kbApi.getKbRuns(kbId)
      if (disposed || kbId !== props.kbId) return
      runs.value = result
    } catch {
      // 静默失败，下次 tick 再试
    } finally {
      // 卸载只清"待触发"的 timer——飞行中的响应若在这里重新起 timer，
      // 会形成离开页面后永不清理的轮询链（2026-08-31 前端审查 H3）。
      if (!disposed && kbId === props.kbId) schedulePolling()
    }
  }, 3000)
}

function clearPolling() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

// ── 辅助 ──
function pct(part: number | null, total: number | null): number {
  const t = total ?? 0
  if (t <= 0) return 0
  return Math.min(100, Math.round(((part ?? 0) / t) * 100))
}

function formatTime(t: string | null): string {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + '…' : s
}

/** 供父组件在文件列表批量挖掘后调用：重置列表并开始轮询。 */
function refresh() {
  loadRuns()
}

defineExpose({ refresh })

onMounted(() => {
  loadOptions()
  loadRuns()
})
onUnmounted(() => {
  disposed = true
  clearPolling()
})
</script>

<style scoped>
.kb-mining {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.kb-mining__top {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius);
  padding: 16px 20px;
}

.kb-mining__workflow {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 260px;
}

.kb-mining__workflow-label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--kb-text-secondary);
}

.kb-mining__workflow-select {
  max-width: 420px;
  width: 100%;
}

.kb-mining__readonly-hint {
  font-size: 11.5px;
  color: var(--kb-text-tertiary);
}

.kb-mining__trigger {
  display: flex;
  gap: 8px;
  align-items: center;
}

.kb-mining__warn {
  margin: 0;
  font-size: 12.5px;
  color: var(--kb-warning);
}

.kb-mining__tip {
  margin: 0;
  font-size: 12.5px;
  color: var(--kb-text-tertiary);
}

.kb-mining__table-wrap {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius);
  border: 1px solid var(--kb-border-light);
  overflow: hidden;
}

.kb-mining__progress {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.kb-mining__progress-bar {
  position: relative;
  display: flex;
  width: 100%;
  height: 6px;
  border-radius: 3px;
  background: var(--kb-border-light);
  overflow: hidden;
}

.kb-mining__progress-fill {
  height: 100%;
  transition: width 0.3s ease;
}

.kb-mining__progress-fill--done {
  background: var(--kb-success, #67c23a);
}

.kb-mining__progress-fill--fail {
  background: var(--kb-danger, #f56c6c);
}

.kb-mining__progress-text {
  font-size: 11.5px;
  color: var(--kb-text-secondary);
}

.kb-mining__progress-fail {
  color: var(--kb-danger);
}

.kb-mining__counts {
  display: inline-flex;
  gap: 10px;
  flex-wrap: wrap;
}

.kb-mining__count {
  font-size: 12px;
  color: var(--kb-text-secondary);
}

.kb-mining__count--new {
  color: var(--kb-success);
  font-weight: 600;
}

.kb-mining__muted {
  color: var(--kb-text-tertiary);
}

.kb-mining__error {
  color: var(--kb-danger);
  font-size: 12.5px;
  cursor: help;
}
</style>
